"""Command line entry point."""

import dataclasses
import json as jsonlib
import os
from typing import Any

import click

from lintarr.collect.stack import collect_stack
from lintarr.config import load_config
from lintarr.facts import Known, Unknown, is_known
from lintarr.invariants import queue_liveness
from lintarr.models import StackFacts
from lintarr.outcomes import Outcome, exit_code
from lintarr.run import run_checks, run_outcome


def _fact_to_dict(f: Known[Any] | Unknown) -> dict[str, Any]:
    if is_known(f):
        return {
            "known": True,
            "value": f.value,
            "source": f.source,
            "read_at": f.read_at.isoformat(),
            # The version of the service the fact was read from. Emitted
            # because version-ranged axioms consume it downstream; a value
            # carried but never surfaced cannot be checked end-to-end.
            "service_version": f.service_version,
        }
    return {"known": False, "reason": f.reason, "detail": f.detail}


def _instance_to_dict(instance: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in dataclasses.fields(instance):
        value = getattr(instance, field.name)
        match value:
            case Known() | Unknown():
                out[field.name] = _fact_to_dict(value)
            case tuple() as items if items and dataclasses.is_dataclass(items[0]):
                out[field.name] = [_instance_to_dict(i) for i in items]
            case _:
                out[field.name] = value
    return out


def _to_dict(facts: StackFacts) -> dict[str, Any]:
    return {
        "qbits": [_instance_to_dict(q) for q in facts.qbits],
        "arrs": [_instance_to_dict(a) for a in facts.arrs],
        "errors": [{"instance": i, "kind": k} for i, k in facts.errors],
    }


def _render_fact_lines(key: str, value: dict[str, Any], *, indent: str) -> list[str]:
    if value["known"]:
        return [f"{indent}{key:<28} = {value['value']!r:<12} {value['source']}"]
    return [f"{indent}{key:<28} ? UNKNOWN ({value['reason']})"]


def _render_nested_list(key: str, items: list[dict[str, Any]], *, indent: str) -> list[str]:
    """Render a list of nested dataclass dicts, e.g. an arr's ``indexers``."""
    lines = [f"{indent}{key}:"]
    for item in items:
        identity = ", ".join(
            f"{k}={v!r}"
            for k, v in item.items()
            if k != "name"
            and not (isinstance(v, dict) and "known" in v)
            and not isinstance(v, list)
        )
        name = item.get("name", "")
        lines.append(f"{indent}  {name}  ({identity})" if identity else f"{indent}  {name}")
        for fact_key, fact_value in sorted(item.items()):
            if isinstance(fact_value, dict) and "known" in fact_value:
                lines.extend(_render_fact_lines(fact_key, fact_value, indent=f"{indent}    "))
    return lines


def _render_human(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for group in ("qbits", "arrs"):
        for instance in payload[group]:
            lines.append(
                f"{instance.get('kind', 'qbittorrent')}[{instance['name']}] v{instance['version']}"
            )
            for key, value in sorted(instance.items()):
                if isinstance(value, dict) and "known" in value:
                    lines.extend(_render_fact_lines(key, value, indent="    "))
                elif isinstance(value, list) and value and isinstance(value[0], dict):
                    lines.extend(_render_nested_list(key, value, indent="    "))
            lines.append("")
    for err in payload["errors"]:
        lines.append(f"ERROR  {err['instance']}: {err['kind']}")
    return "\n".join(lines)


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """lintarr — static consistency checker for the *arr stack."""
    ctx.ensure_object(dict)


@cli.command("dump-facts")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def dump_facts(ctx: click.Context, as_json: bool) -> None:
    """Print a source-annotated snapshot of everything lintarr can read."""
    facts = collect_stack(load_config(os.environ), transport=ctx.obj.get("transport"))
    payload = _to_dict(facts)
    click.echo(jsonlib.dumps(payload, indent=2) if as_json else _render_human(payload))


# Keyed on (invariant, conflict), never on the invariant alone. One invariant
# answers one operator question, but it can reach that answer through
# structurally different conflicts with different remedies — and a "Therefore"
# line that names the wrong one tells an operator to change a setting the code
# itself has just established will not help.
_THEREFORE = {
    (queue_liveness.INVARIANT_ID, queue_liveness.SEEDING): (
        "completed torrents hold every active slot and no queued\n"
        "  download can start. Nothing releases a seeder: both global share\n"
        "  limits are off and no category sets its own."
    ),
    (queue_liveness.INVARIANT_ID, queue_liveness.STARVATION): (
        "this client cannot start a first download even while\n"
        "  nothing is running: max_active_downloads or max_active_torrents is\n"
        "  at or below zero. Share limits are not involved, so turning them on\n"
        "  will not help."
    ),
}


def _finding_to_dict(finding) -> dict[str, Any]:
    return {
        "invariant": finding.invariant,
        "instance": finding.instance,
        "outcome": str(finding.outcome),
        # Which conflict inside the invariant decided this. A consumer that
        # branches on the invariant id alone cannot tell the two apart, which
        # is the machine-readable form of the same defect the "Therefore" line
        # above had.
        "conflict": finding.conflict,
        "detail": finding.detail,
        "premises": [{"label": p.label, "state": p.state} for p in finding.premises],
    }


def _render_findings(findings) -> str:
    lines: list[str] = []
    for f in findings:
        lines.append(f"{f.outcome:<5} {f.invariant}  [{f.instance}]")
        if f.premises:
            header = (
                "  What lintarr read from your stack — check these yourself:"
                if f.outcome is Outcome.FAIL
                else "  Could not read:"
            )
            lines.append("")
            lines.append(header)
            for p in f.premises:
                state = "holds" if p.state else "unknown" if p.state is None else "does not hold"
                lines.append(f"    {p.label:<36} {state}")
        if f.detail:
            lines.append(f"  {f.detail}")
        therefore = _THEREFORE.get((f.invariant, f.conflict))
        if therefore and f.outcome is Outcome.FAIL:
            lines.append("")
            lines.append(f"  Therefore: {therefore}")
        lines.append("")
    counts: dict[str, int] = {}
    for f in findings:
        counts[str(f.outcome)] = counts.get(str(f.outcome), 0) + 1
    summary = ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))
    lines.append(f"{len(findings)} checked: {summary}" if findings else "nothing to check")
    return "\n".join(lines)


@cli.command("check")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option(
    "--no-strict",
    "strict",
    flag_value=False,
    default=True,
    help="Do not treat SKIP or N/A as a non-zero exit.",
)
@click.pass_context
def check_command(ctx: click.Context, as_json: bool, strict: bool) -> None:
    """Check whether this stack's settings can coexist.

    ``outcome`` and ``exit_code`` in the JSON payload rank findings on
    different axes and can disagree. ``outcome`` is the worst outcome by
    severity, where "could not look" outranks "looked and found a conflict" —
    a run that read nothing has not established anything. ``exit_code`` is what
    the process returns, and there a proved conflict (1) outranks a skip (3) so
    CI fails on the thing an operator can act on. A run with one FAIL and one
    SKIP therefore reports ``"outcome": "SKIP"`` alongside ``"exit_code": 1``.
    Branch on ``exit_code``; read ``outcome`` for how much of the stack was
    actually examined.
    """
    cfg = load_config(os.environ)
    facts = collect_stack(cfg, transport=ctx.obj.get("transport"))
    findings = run_checks(facts, declared=cfg.declared)
    code = exit_code((f.outcome for f in findings), strict=strict)
    if as_json:
        click.echo(
            jsonlib.dumps(
                {
                    "schema": 1,
                    "outcome": str(run_outcome(findings)),
                    "exit_code": code,
                    "findings": [_finding_to_dict(f) for f in findings],
                },
                indent=2,
            )
        )
    else:
        click.echo(_render_findings(findings))
    ctx.exit(code)
