"""The Level 2 differential: trace() against a real qBittorrent, scenario by scenario.

Each scenario first asserts it reached the state it claims to test (spec
anti-goal "a green that proves nothing"); failing that is a HarnessError, not a
disagreement. Needs Docker.
"""

import pytest

from tests.conformance.known import KNOWN_DISAGREEMENTS
from tests.conformance.qbt import HarnessError, QbtHarness
from tests.conformance.runner import Step, judge, observe_steps, with_one_retry
from tests.conformance.scenarios import SCENARIOS, Scenario
from tests.model.queue import AddIncomplete, trace

pytestmark = pytest.mark.container

# From docs/measurements/2026-11-13-qbt-conformance-assumptions.md, "Settle time":
# every queue change settled in 1.3-3.2 s, the share-limit action fired 9.7-10.5 s
# after the add, so 15 s is the minimum wait; 45 s still fits one 30 s auto-manage tick.
SETTLE_S = 15.0
TIMEOUT_S = 45.0
POLL_S = 1.0


def _assert_reached_target(s: Scenario, steps: list[Step]) -> None:
    """Raise HarnessError unless the scenario ran as designed."""
    if s.cfg.dont_count_slow_torrents:
        last = steps[-1]
        if last.stable_at - last.event_at < SETTLE_S:
            raise HarnessError(f"{s.id}: judged before the slow timer and settle had elapsed")
    if s.cfg.share_limit_enabled and steps[-1].counts.left == 0:
        raise HarnessError(f"{s.id}: the share limit never fired (nothing LEFT)")
    _assert_complete_before_incomplete(s, steps)


def _assert_complete_before_incomplete(s: Scenario, steps: list[Step]) -> None:
    """Where incomplete torrents follow complete ones, the complete ones must have
    finished checking (no download-side state) before the incompletes arrived."""
    first_incomplete = next(
        (i for i, e in enumerate(s.events) if isinstance(e, AddIncomplete)), None
    )
    if not first_incomplete:  # absent, or the very first event
        return
    before = steps[first_incomplete - 1].counts
    if before.downloading or before.queued:
        raise HarnessError(f"{s.id}: complete torrents not seeding before the adds: {before}")


def _assert_scenario_matches_model(qbt: QbtHarness, s: Scenario) -> None:
    qbt.reset()
    qbt.set_prefs(s.prefs)
    steps = observe_steps(qbt, s, poll_s=POLL_S, settle_s=SETTLE_S, timeout_s=TIMEOUT_S)
    _assert_reached_target(s, steps)
    try:
        judge(s.id, [st.counts for st in steps], trace(s.cfg, s.events), KNOWN_DISAGREEMENTS)
    except AssertionError as exc:
        exc.add_note(f"cfg: {s.cfg}\nprefs: {s.prefs}\nevents: {s.events}")
        for i, step in enumerate(steps):
            exc.add_note(f"raw step {i}: " + "; ".join(step.raw_lines()))
        raise


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_scenario_matches_model(qbt: QbtHarness, scenario: Scenario):
    with_one_retry(lambda: _assert_scenario_matches_model(qbt, scenario))
