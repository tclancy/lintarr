"""Edge-triggering: page on a transition into a problem, not on every run it persists.

Run hourly under a failure alert, a level-triggered checker re-pages the same
FAIL forever, and an alert that cries wolf is how the original incident comes
back as noise. These pin which findings count as *new*, and that the state
carrying "what we already said" survives a round trip and fails safe.
"""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lintarr.edge import (
    StateError,
    alert_keys,
    edge_exit_code,
    load_previous,
    new_alerts,
    save_current,
)
from lintarr.outcomes import Finding, Outcome, exit_code

NOTHING: frozenset = frozenset()


def _finding(outcome: Outcome, instance: str = "qbittorrent[main]", conflict: str = "") -> Finding:
    return Finding(invariant="queue-liveness", instance=instance, outcome=outcome, conflict=conflict)


FAIL = _finding(Outcome.FAIL, conflict="seeding")
PASS = _finding(Outcome.PASS)


def test_first_run_reports_every_problem_as_new():
    assert new_alerts([FAIL], NOTHING, strict=True) == (FAIL,)


def test_a_problem_already_reported_is_not_new():
    previous = alert_keys([FAIL], strict=True)
    assert new_alerts([FAIL], previous, strict=True) == ()
    assert edge_exit_code([FAIL], previous, strict=True) == 0


def test_pass_is_never_an_alert():
    assert alert_keys([PASS], strict=True) == NOTHING
    assert edge_exit_code([PASS], NOTHING, strict=True) == 0


@pytest.mark.parametrize("outcome", [Outcome.SKIP, Outcome.NOT_APPLICABLE])
def test_could_not_decide_alerts_only_under_strict(outcome):
    # Mirrors outcomes.exit_code: --no-strict means SKIP and N/A exit 0, so
    # they must not become alerts either, or the two modes disagree.
    finding = _finding(outcome)
    assert edge_exit_code([finding], NOTHING, strict=True) == 3
    assert edge_exit_code([finding], NOTHING, strict=False) == 0
    # The exit code alone can't see this: exit_code already zeroes SKIP under
    # --no-strict, so a SKIP wrongly counted as a problem would still be
    # recorded and reported as "new".
    assert alert_keys([finding], strict=False) == NOTHING
    assert new_alerts([finding], NOTHING, strict=False) == ()


def test_a_second_problem_pages_while_the_first_persists():
    other = _finding(Outcome.FAIL, instance="qbittorrent[4k]", conflict="seeding")
    previous = alert_keys([FAIL], strict=True)
    assert new_alerts([FAIL, other], previous, strict=True) == (other,)


def test_the_same_instance_changing_outcome_is_new():
    # FAIL -> ERROR is a different problem with a different remedy.
    error = _finding(Outcome.ERROR, conflict="seeding")
    previous = alert_keys([FAIL], strict=True)
    assert edge_exit_code([error], previous, strict=True) == 2


def test_the_same_invariant_failing_on_a_different_conflict_is_new():
    starved = _finding(Outcome.FAIL, conflict="starvation")
    previous = alert_keys([FAIL], strict=True)
    assert new_alerts([starved], previous, strict=True) == (starved,)


def test_a_problem_that_clears_and_returns_pages_again():
    after_first = alert_keys([FAIL], strict=True)
    after_clear = alert_keys([PASS], strict=True)
    assert after_clear == NOTHING
    assert edge_exit_code([FAIL], after_clear, strict=True) == 1
    assert edge_exit_code([FAIL], after_first, strict=True) == 0


def test_exit_code_ranks_new_problems_like_a_level_run():
    # ERROR outranks FAIL outranks SKIP, exactly as outcomes.exit_code does.
    findings = [FAIL, _finding(Outcome.ERROR, instance="sonarr[main]")]
    assert edge_exit_code(findings, NOTHING, strict=True) == 2


_outcomes = st.sampled_from(list(Outcome))
_findings = st.lists(
    st.builds(
        _finding,
        outcome=_outcomes,
        instance=st.sampled_from(["qbittorrent[main]", "qbittorrent[4k]", "sonarr[main]"]),
        conflict=st.sampled_from(["", "seeding", "starvation"]),
    ),
    max_size=6,
)


@given(findings=_findings, strict=st.booleans())
def test_an_unchanged_stack_pages_at_most_once(findings, strict):
    first = edge_exit_code(findings, NOTHING, strict=strict)
    second = edge_exit_code(findings, alert_keys(findings, strict=strict), strict=strict)
    assert second == 0
    # And the first run is exactly as loud as a level-triggered one.
    assert first == exit_code((f.outcome for f in findings), strict=strict)


# --- state file -----------------------------------------------------------


def test_state_round_trips(tmp_path):
    path = tmp_path / "state.json"
    keys = alert_keys([FAIL, _finding(Outcome.ERROR, instance="sonarr[main]")], strict=True)
    save_current(path, keys)
    assert load_previous(path) == keys


def test_missing_state_means_nothing_reported_yet(tmp_path):
    # First run on a box: every problem is new, which is the safe direction.
    assert load_previous(tmp_path / "absent.json") == NOTHING


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", json.dumps({"schema": 99, "alerting": []}), json.dumps({"schema": 1})],
)
def test_unreadable_state_is_an_error_not_a_silent_empty(tmp_path, content):
    # The caller decides how to fail safe; this layer must not guess.
    path = tmp_path / "state.json"
    path.write_text(content)
    with pytest.raises(StateError):
        load_previous(path)


def test_save_creates_the_state_directory(tmp_path):
    path = tmp_path / "state" / "lintarr" / "state.json"
    save_current(path, alert_keys([FAIL], strict=True))
    assert load_previous(path) == alert_keys([FAIL], strict=True)


def test_save_leaves_no_temporary_files(tmp_path):
    path = tmp_path / "state.json"
    save_current(path, alert_keys([FAIL], strict=True))
    save_current(path, NOTHING)
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_a_state_path_that_cannot_be_read_is_a_state_error(tmp_path):
    # Not FileNotFoundError: a parent that is a file raises NotADirectoryError,
    # which escaped as a traceback and exited 1, the code that means FAIL.
    (tmp_path / "blocker").write_text("")
    with pytest.raises(StateError):
        load_previous(tmp_path / "blocker" / "state.json")
