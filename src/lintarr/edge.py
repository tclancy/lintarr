"""Edge-triggering: exit non-zero on a transition into a problem, not on every run.

The spec's alerting rule is "fire on transition into FAIL or ERROR, not hourly
forever". Run hourly under a systemd OnFailure= alert, a level-triggered exit
code pages the same FAIL every tick, and an alert that cries wolf is how the
original incident comes back as noise.

A *problem* is a finding whose outcome would make a level-triggered run exit
non-zero, identified by invariant, instance, conflict and outcome. A run is
loud only about problems the previous run did not already have. One that
persists is quiet, one that changes outcome or conflict is new, and one that
clears and comes back is new again.
"""

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from lintarr.outcomes import Finding, Outcome, exit_code

#: invariant, instance, conflict, outcome.
type AlertKey = tuple[str, str, str, str]

STATE_SCHEMA = 1
_KEY_FIELDS = ("invariant", "instance", "conflict", "outcome")


class StateError(ValueError):
    """The state file exists but cannot be trusted."""


def _alerting_outcomes(*, strict: bool) -> frozenset[Outcome]:
    """Outcomes that make a level-triggered run exit non-zero (see ``exit_code``)."""
    loud = {Outcome.FAIL, Outcome.ERROR}
    if strict:
        loud |= {Outcome.SKIP, Outcome.NOT_APPLICABLE}
    return frozenset(loud)


def _key(finding: Finding) -> AlertKey:
    return (finding.invariant, finding.instance, finding.conflict, str(finding.outcome))


def alert_keys(findings: Iterable[Finding], *, strict: bool) -> frozenset[AlertKey]:
    """The problems in *findings*: what the next run should treat as already said."""
    loud = _alerting_outcomes(strict=strict)
    return frozenset(_key(f) for f in findings if f.outcome in loud)


def new_alerts(
    findings: Iterable[Finding], previous: frozenset[AlertKey], *, strict: bool
) -> tuple[Finding, ...]:
    """The problems in *findings* the previous run did not have, in input order."""
    loud = _alerting_outcomes(strict=strict)
    return tuple(f for f in findings if f.outcome in loud and _key(f) not in previous)


def edge_exit_code(
    findings: Iterable[Finding], previous: frozenset[AlertKey], *, strict: bool
) -> int:
    """``exit_code`` over the new problems only, so the codes keep their meaning."""
    fresh = new_alerts(findings, previous, strict=strict)
    return exit_code((f.outcome for f in fresh), strict=strict)


def _parse_state(raw: str) -> frozenset[AlertKey]:
    try:
        payload = json.loads(raw)
        if payload["schema"] != STATE_SCHEMA:
            raise StateError(f"state schema {payload['schema']!r}, expected {STATE_SCHEMA}")
        return frozenset(
            tuple(str(entry[field]) for field in _KEY_FIELDS) for entry in payload["alerting"]
        )
    except StateError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise StateError(f"state file is not lintarr state: {exc!r}") from exc


def load_previous(path: Path) -> frozenset[AlertKey]:
    """What the previous run reported. A missing file means nothing yet.

    An unreadable file raises rather than reading as empty. Empty is the
    fail-safe choice (every problem pages again), but making it here would
    hide that the state is broken. The caller warns and then chooses it.
    """
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return frozenset()
    except OSError as exc:
        raise StateError(f"state file cannot be read: {exc}") from exc
    return _parse_state(raw)


def save_current(path: Path, keys: frozenset[AlertKey]) -> None:
    """Persist *keys* atomically, so a run killed mid-write can't corrupt the state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": STATE_SCHEMA,
        "alerting": [dict(zip(_KEY_FIELDS, key, strict=True)) for key in sorted(keys)],
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
