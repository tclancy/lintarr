"""Drive a scenario against the container, judge it, and write the result file.

`observe` turns a scenario into one stable Counts per event. `judge` compares
that with trace() and the known-disagreement registry. `classify_report` and
`result_kind` feed `conformance-result.json`, which the nightly alert reads to
say what kind of failure it was (spec "Failure alerting").
"""

import itertools
import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pytest

from tests.conformance.known import Known
from tests.conformance.qbt import HarnessError
from tests.conformance.scenarios import Scenario
from tests.conformance.states import Bucket, UnmappedState, bucket_of, tally
from tests.conformance.torrents import HarnessTorrent, make_torrent
from tests.model.queue import (
    AddComplete,
    AddIncomplete,
    Counts,
    Event,
    FinishDownloads,
    RemoveSeeder,
)

RESULT_FILE = "conformance-result.json"

# Every run names its torrents afresh. A retry that re-added the same names (so
# the same hashes) raced reset()'s asynchronous file deletion: qBittorrent deleted
# the data just rewritten for the new add, and the torrent came up missingFiles.
_RUN_SEQ = itertools.count()


class Harness(Protocol):
    def add(self, t: HarnessTorrent, *, complete: bool) -> None: ...
    def delete(self, hashes: list[str], *, delete_files: bool) -> None: ...
    def states(self) -> dict[str, str]: ...


# --- observe -----------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """What qBittorrent showed once it was stable after one event."""

    counts: Counts
    states: dict[str, str]  # raw hash -> state, as last polled
    names: dict[str, str]  # hash -> torrent name, for every torrent added so far
    event_at: float  # time.monotonic() once the event was applied
    stable_at: float  # time.monotonic() of the second of the two equal polls

    def raw_lines(self) -> list[str]:
        return _raw_lines(self.states, self.names)


def _raw_lines(states: Mapping[str, str], names: Mapping[str, str]) -> list[str]:
    return [f"{names.get(h, '?')} {h[:12]} {s}" for h, s in sorted(states.items())]


def _wait_stable(h: Harness, event_at: float, *, poll_s: float, settle_s: float, timeout_s: float):
    """Two equal raw polls, both at least `settle_s` after the event.

    Raw maps are compared, never buckets, so transient states such as
    checkingResumeData are not mapped and cannot raise (M2).
    """
    deadline = event_at + timeout_s
    time.sleep(max(0.0, event_at + settle_s - time.monotonic()))
    previous = h.states()
    while True:
        time.sleep(poll_s)
        current = h.states()
        if current == previous:
            return current, time.monotonic()
        if time.monotonic() >= deadline:
            raise HarnessError(f"states not stable within {timeout_s:.0f}s: {current}")
        previous = current


@dataclass
class _Run:
    scenario: Scenario
    seq: int
    expected: set[str]
    names: dict[str, str]
    last_states: dict[str, str]

    def add(self, h: Harness, n: int, *, complete: bool) -> None:
        tag = "c" if complete else "i"
        for _ in range(n):
            t = make_torrent(f"{self.scenario.id}-r{self.seq}-{tag}{len(self.names)}")
            h.add(t, complete=complete)
            self.expected.add(t.info_hash)
            self.names[t.info_hash] = t.name

    def remove_seeder(self, h: Harness) -> None:
        """Delete one torrent now SEEDING; it leaves the expected set, so it is
        not counted as LEFT (H4)."""
        present = self.expected & self.last_states.keys()
        seeders = sorted(x for x in present if bucket_of(self.last_states[x]) == Bucket.SEEDING)
        if not seeders:
            raise HarnessError(f"RemoveSeeder: nothing is seeding in {self.last_states}")
        h.delete([seeders[0]], delete_files=True)
        self.expected.discard(seeders[0])


def _apply(run: _Run, h: Harness, event: Event) -> None:
    match event:
        case AddComplete(n):
            run.add(h, n, complete=True)
        case AddIncomplete(n):
            run.add(h, n, complete=False)
        case RemoveSeeder():
            run.remove_seeder(h)
        case FinishDownloads():
            raise HarnessError("FinishDownloads is model-only; the container cannot finish")


def observe_steps(
    h: Harness, s: Scenario, *, poll_s: float, settle_s: float, timeout_s: float
) -> list[Step]:
    """Apply each event and record the stable state after it."""
    run = _Run(s, next(_RUN_SEQ), expected=set(), names={}, last_states={})
    steps = []
    for event in s.events:
        _apply(run, h, event)
        event_at = time.monotonic()
        states, stable_at = _wait_stable(
            h, event_at, poll_s=poll_s, settle_s=settle_s, timeout_s=timeout_s
        )
        run.last_states = states
        try:
            counts = tally(states, run.expected)  # an unmapped state still here raises
        except UnmappedState as exc:
            exc.add_note(f"{s.id} step {len(steps)} raw: {_raw_lines(states, run.names)}")
            raise
        steps.append(Step(counts, states, dict(run.names), event_at, stable_at))
    return steps


def observe(
    h: Harness, s: Scenario, *, poll_s: float, settle_s: float, timeout_s: float
) -> list[Counts]:
    """One stable Counts per event."""
    steps = observe_steps(h, s, poll_s=poll_s, settle_s=settle_s, timeout_s=timeout_s)
    return [step.counts for step in steps]


# --- judge -------------------------------------------------------------------


class StaleKnownEntry(AssertionError):
    """A registry entry the model now agrees with: remove it."""


def _first_difference(a: list[Counts], b: list[Counts]) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def _detail(scenario_id: str, observed: list[Counts], expected: list[Counts], label: str) -> str:
    i = _first_difference(observed, expected)
    at_obs = observed[i] if i < len(observed) else None
    at_exp = expected[i] if i < len(expected) else None
    return (
        f"{scenario_id}: step {i}: observed {at_obs!r} != {label} {at_exp!r}\n"
        f"  observed trace: {observed}\n  {label} trace: {expected}"
    )


def _judge_known(
    scenario_id: str, observed: list[Counts], predicted: list[Counts], entry: Known
) -> None:
    recorded = list(entry.observed)
    if recorded == predicted:
        raise StaleKnownEntry(
            f"{scenario_id}: the model now predicts the recorded observation; "
            f"remove the KNOWN_DISAGREEMENTS entry ({entry.tracked_in})"
        )
    if observed != recorded:
        raise AssertionError(
            "known disagreement changed shape. "
            + _detail(scenario_id, observed, recorded, "recorded")
            + f"\n  predicted trace: {predicted}\n  why: {entry.why}"
        )


def judge(
    scenario_id: str,
    observed: list[Counts],
    predicted: list[Counts],
    known: Mapping[str, Known],
) -> None:
    """Raise AssertionError on a disagreement, StaleKnownEntry on a stale entry."""
    entry = known.get(scenario_id)
    if entry is not None:
        _judge_known(scenario_id, observed, predicted, entry)
    elif observed != predicted:
        raise AssertionError("disagreement. " + _detail(scenario_id, observed, predicted, "model"))


# --- retry ---------------------------------------------------------------------


def with_one_retry(attempt: Callable[[], None]) -> None:
    """Run `attempt`; on any failure run it once more. Only the second failure
    propagates, carrying a note of the first."""
    try:
        attempt()
        return
    except Exception as first:  # noqa: BLE001 - any failure earns the one retry
        first_failure = first
    try:
        attempt()
    except Exception as second:
        second.add_note(f"first attempt failed too: {first_failure!r}")
        raise


# --- result file ----------------------------------------------------------------

_PRECEDENCE = ("harness-error", "unmapped-state", "disagreement")
EXC_TYPE_ATTR = "conformance_exc_type"  # set on each failed report by the conftest hook


def classify_report(report: pytest.TestReport) -> str | None:
    """The failure kind of one setup, call or teardown report; None if it did not fail."""
    if not report.failed:
        return None
    exc_type = getattr(report, EXC_TYPE_ATTR, None)
    if exc_type is None:
        return "harness-error"
    if issubclass(exc_type, UnmappedState):
        return "unmapped-state"
    if issubclass(exc_type, AssertionError):  # StaleKnownEntry included
        return "disagreement"
    return "harness-error"  # HarnessError, and anything else that broke the run


def result_kind(kinds: Iterable[str], exitstatus: int) -> str:
    seen = set(kinds)
    for kind in _PRECEDENCE:
        if kind in seen:
            return kind
    return "harness-error" if exitstatus != 0 else "pass"


def write_result(path: Path, failures: list[tuple[str, str]], exitstatus: int) -> None:
    """`failures` holds one (nodeid, kind) per failed report."""
    failed = sorted({nodeid for nodeid, _ in failures})
    kind = result_kind((k for _, k in failures), exitstatus)
    path.write_text(json.dumps({"kind": kind, "failed": failed}, indent=2) + "\n")
