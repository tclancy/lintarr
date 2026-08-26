"""Command line entry point."""

import dataclasses
import json as jsonlib
import os
from typing import Any

import click

from lintarr.collect.stack import collect_stack
from lintarr.config import load_config
from lintarr.facts import Known, Unknown, is_known
from lintarr.models import StackFacts


def _fact_to_dict(f: Known[Any] | Unknown) -> dict[str, Any]:
    if is_known(f):
        return {
            "known": True,
            "value": f.value,
            "source": f.source,
            "read_at": f.read_at.isoformat(),
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


def _render_human(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for group in ("qbits", "arrs"):
        for instance in payload[group]:
            lines.append(f"{instance.get('kind', 'qbittorrent')}[{instance['name']}] "
                         f"v{instance['version']}")
            for key, value in sorted(instance.items()):
                if not isinstance(value, dict) or "known" not in value:
                    continue
                if value["known"]:
                    lines.append(f"    {key:<28} = {value['value']!r:<12} {value['source']}")
                else:
                    lines.append(f"    {key:<28} ? UNKNOWN ({value['reason']})")
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
