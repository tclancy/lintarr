"""Command line entry point."""

import dataclasses
import json as jsonlib
import os
import re
from typing import Any

import click

from lintarr.collect.stack import collect_stack
from lintarr.config import LintarrConfig, load_config
from lintarr.facts import Known, Unknown, is_known
from lintarr.invariants import queue_liveness
from lintarr.models import StackFacts
from lintarr.outcomes import Outcome, exit_code
from lintarr.run import run_checks, run_outcome


def _config_from_env() -> LintarrConfig:
    """Load configuration, or fail as a usage error rather than a traceback.

    ``load_config`` rejects an environment it cannot act on — credentials for
    an instance whose URL is missing, a URL with no credentials — and both are
    input mistakes, so they belong in click's usage-error channel: one line
    naming the variable, and exit 2. Handing ``os.environ`` straight to
    ``load_config`` let the ``ValueError`` reach the interpreter, so a single
    typo (``QBIT_URLL``) printed a traceback.

    Exit 2 is the same code ``outcomes.exit_code`` gives ``Outcome.ERROR``, and
    that collision is intentional: both mean *lintarr could not look*, which is
    the distinction CI actually branches on. Splitting them would need a change
    in ``outcomes`` too, not a different exception here.

    Lifted to one place because two commands load config, and a guard that
    exists at one call site is a guard the next command added will not have.
    """
    try:
        return load_config(os.environ)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


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
        # ``detail`` is its own key, never folded into ``kind``: every
        # consumer compares the kind for equality, so prose inside it is a
        # silent break. ``instance`` keeps its published name.
        "errors": [{"instance": e.label, "kind": e.kind, "detail": e.detail} for e in facts.errors],
    }


#: One or more leading version markers, in either case.
_LEADING_V = re.compile(r"^[vV]+")


def _version_label(version: str) -> str:
    """A version string with exactly one leading ``v``, whatever the service sent.

    The two adapters disagree and both are reporting their service faithfully:
    qBittorrent's ``GET /api/v2/app/version`` answers ``v5.2.4`` with the ``v``
    already on it, while an arr's ``GET /api/v3/system/status`` answers a bare
    ``4.0.0``. Prepending unconditionally rendered ``qbittorrent[main] vv5.2.4``.

    Normalising here rather than in the adapters is deliberate.
    ``Known.service_version`` carries the verbatim string and version-ranged
    axioms are written against what the service actually said, so trimming it at
    the fact layer would make an axiom's range disagree with the value it is
    matched on. This is a display convention and it lives at the one place that
    displays.

    Case-insensitive and empty-safe even though neither shape is reachable
    today: ``removeprefix("v")`` alone renders ``V5.2.4`` as ``vV5.2.4``, which
    is the same defect spelt differently and which a ``"vv" not in output``
    assertion cannot see. An empty version is passed through rather than
    rendered as a bare ``v`` — both adapters raise ``bad-response`` on one, so
    there is nothing to label.
    """
    if not version:
        return version
    return f"v{_LEADING_V.sub('', version)}"


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


def _render_error_row(err: dict[str, Any]) -> list[str]:
    """One failed service, with its explanation on a continuation line.

    The detail gets its own line rather than a suffix because the longest of
    them is a full sentence — qBittorrent's 403 note runs to forty words — and
    appending that to the label makes the one line an operator scans for the
    service name unreadable. Indented to the width of ``ERROR  `` so the
    service names still align down the left.
    """
    lines = [f"ERROR  {err['instance']}: {err['kind']}"]
    if err["detail"]:
        lines.append(f"       {err['detail']}")
    return lines


def _render_human(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for group in ("qbits", "arrs"):
        for instance in payload[group]:
            kind = instance.get("kind", "qbittorrent")
            lines.append(f"{kind}[{instance['name']}] {_version_label(instance['version'])}")
            for key, value in sorted(instance.items()):
                if isinstance(value, dict) and "known" in value:
                    lines.extend(_render_fact_lines(key, value, indent="    "))
                elif isinstance(value, list) and value and isinstance(value[0], dict):
                    lines.extend(_render_nested_list(key, value, indent="    "))
            lines.append("")
    for err in payload["errors"]:
        lines.extend(_render_error_row(err))
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
    facts = collect_stack(_config_from_env(), transport=ctx.obj.get("transport"))
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
    # "at or below zero" was wrong: the predicate is `_binds(v) and v <= 0`,
    # and -1 does not bind, so -1 never starves the queue. An operator reading
    # `max_active_downloads = -1` alongside that sentence goes looking at the
    # one limit the check has already cleared.
    (queue_liveness.INVARIANT_ID, queue_liveness.STARVATION): (
        "this client cannot start a first download even while\n"
        "  nothing is running: max_active_downloads or max_active_torrents is zero,\n"
        "  or negative but not -1 (the only value meaning unlimited). Share limits\n"
        "  are not involved, so turning them on will not help."
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
        # Spelled `kind` here and `error_kind` on the dataclass, deliberately.
        # `dump-facts --json` already publishes an error row's kind under `kind`,
        # and two JSON surfaces disagreeing about the name of one concept is worse
        # than a field whose attribute reads differently — while on a `Finding`,
        # which is mostly not an error, a bare `kind` would read as the finding's
        # own kind (#25).
        "kind": finding.error_kind,
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
    actually examined. Exit 2 has one meaning beyond ERROR: an environment this
    tool refuses outright — credentials for a service whose URL is missing, say
    — exits 2 having printed a usage error and no payload at all. Both mean
    "lintarr could not look", which is why they share a code; a run that
    emitted no JSON is the one that never got as far as checking.
    """
    cfg = _config_from_env()
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
