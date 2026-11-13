"""Unit tests (no Docker) for the differential's judge, observer and result file.

Spec check R11, plus the M2/H4/H5 review findings. The observer is driven by a
fake harness whose raw states are scripted, so these run in the default suite.
"""

import json
import re
from pathlib import Path

import pytest

from tests.conformance import conftest as conformance_conftest
from tests.conformance.known import KNOWN_DISAGREEMENTS, Known
from tests.conformance.qbt import HarnessError
from tests.conformance.runner import (
    StaleKnownEntry,
    judge,
    observe,
    result_kind,
    with_one_retry,
)
from tests.conformance.scenarios import PREF_KEYS, RELEASING_ACTIONS, SCENARIOS, Scenario
from tests.conformance.states import UnmappedState
from tests.conformance.torrents import HarnessTorrent
from tests.model.queue import (
    AddComplete,
    AddIncomplete,
    Counts,
    QueueConfig,
    RemoveSeeder,
    trace,
)

pytest_plugins = ["pytester"]

REPO = Path(__file__).resolve().parents[2]

PREDICTED = [Counts(0, 0, 5, 0, 0), Counts(0, 3, 5, 0, 0)]
SEEN = [Counts(0, 0, 5, 0, 0), Counts(3, 0, 2, 3, 0)]


def _known(observed: list[Counts]) -> Known:
    return Known(observed=tuple(observed), why="F1: test entry", tracked_in="CONTINUATION.md")


# --- judge -------------------------------------------------------------------


def test_known_entry_matching_observation_passes():
    assert judge("s", SEEN, PREDICTED, {"s": _known(SEEN)}) is None


def test_known_entry_with_changed_observation_fails():
    changed = [SEEN[0], Counts(2, 1, 2, 3, 0)]
    with pytest.raises(AssertionError) as exc:
        judge("s", changed, PREDICTED, {"s": _known(SEEN)})
    assert not isinstance(exc.value, StaleKnownEntry)


def test_known_entry_now_matching_model_is_stale():
    with pytest.raises(StaleKnownEntry):
        judge("s", PREDICTED, PREDICTED, {"s": _known(PREDICTED)})


def test_unknown_disagreement_fails_with_full_detail():
    with pytest.raises(AssertionError) as exc:
        judge("1-wedged-slow-off", SEEN, PREDICTED, {})
    msg = str(exc.value)
    assert "1-wedged-slow-off" in msg
    assert "step 1" in msg
    assert repr(SEEN[1]) in msg
    assert repr(PREDICTED[1]) in msg


def test_agreement_passes_without_an_entry():
    assert judge("s", PREDICTED, PREDICTED, {}) is None


# --- the real registry and scenario set ------------------------------------


def _open_items() -> str:
    text = (REPO / "CONTINUATION.md").read_text()
    return text.split("## Open items", 1)[1].split("\n## ", 1)[0]


def test_every_registry_entry_has_why_and_tracked_in():
    ids = {s.id for s in SCENARIOS}
    open_items = _open_items()
    for sid, entry in KNOWN_DISAGREEMENTS.items():
        assert sid in ids, f"{sid} is not a scenario"
        assert entry.why.strip(), sid
        assert entry.tracked_in.strip(), sid
        finding = re.match(r"(F\d+)", entry.why)
        assert finding, f"{sid}: why must open with the finding it is explained by"
        assert f"**{finding.group(1)}" in open_items, f"{sid}: no Open items line for it"
        assert len(entry.observed) == len(next(s for s in SCENARIOS if s.id == sid).events)


_CFG_KEYS = (
    "queueing_enabled",
    "max_active_downloads",
    "max_active_uploads",
    "max_active_torrents",
    "dont_count_slow_torrents",
)


def test_scenario_prefs_agree_with_cfg():
    assert len(SCENARIOS) >= 12  # reachability: the loop below has something to check
    for s in SCENARIOS:
        assert set(s.prefs) == PREF_KEYS, s.id
        for key in _CFG_KEYS:
            assert s.prefs[key] == getattr(s.cfg, key), (s.id, key)
        share = bool(s.prefs["max_ratio_enabled"] or s.prefs["max_seeding_time_enabled"])
        assert share == s.cfg.share_limit_enabled, s.id
        if share:
            assert s.prefs["max_ratio_act"] in RELEASING_ACTIONS, s.id


def test_every_scenario_runs_with_slow_exemption_on_and_off():
    bases = {s.id.rsplit("-slow-", 1)[0] for s in SCENARIOS}
    for base in bases:
        slows = {s.cfg.dont_count_slow_torrents for s in SCENARIOS if s.id.startswith(base + "-")}
        assert slows == {False, True}, base


def test_every_scenario_has_a_model_prediction():
    for s in SCENARIOS:
        assert len(trace(s.cfg, s.events)) == len(s.events), s.id


# --- observe, against a fake harness ----------------------------------------


class FakeHarness:
    """Each poll returns the next entry of `script` for every added torrent.

    The last script entry repeats once the script runs out.
    """

    def __init__(self, script: list[str]) -> None:
        self.script = script
        self.added: dict[str, int] = {}  # hash -> polls seen since its add
        self.deleted: list[str] = []

    def add(self, t: HarnessTorrent, *, complete: bool) -> None:
        self.added[t.info_hash] = 0

    def delete(self, hashes: list[str], *, delete_files: bool) -> None:
        assert delete_files
        for h in hashes:
            self.added.pop(h)
            self.deleted.append(h)

    def states(self) -> dict[str, str]:
        out = {}
        for h, n in self.added.items():
            out[h] = self.script[min(n, len(self.script) - 1)]
            self.added[h] = n + 1
        return out


_OPEN = QueueConfig(True, -1, -1, -1, False, False)


def _scenario(*events) -> Scenario:
    return Scenario(id="fake", cfg=_OPEN, prefs={}, events=tuple(events))


def _observe(h, s: Scenario, timeout_s: float = 5.0) -> list[Counts]:
    return observe(h, s, poll_s=0, settle_s=0, timeout_s=timeout_s)


def test_transient_states_do_not_raise_before_stability():
    settles = FakeHarness(["checkingResumeData", "stalledUP"])
    assert _observe(settles, _scenario(AddComplete(1))) == [Counts(0, 0, 1, 0, 0)]

    stuck = FakeHarness(["checkingResumeData", "missingFiles"])
    with pytest.raises(UnmappedState):
        _observe(stuck, _scenario(AddComplete(1)))


def test_unstable_states_time_out_as_a_harness_error():
    class Flapping(FakeHarness):
        def states(self) -> dict[str, str]:
            self.flip = not getattr(self, "flip", False)
            return {h: "stalledUP" if self.flip else "queuedUP" for h in self.added}

    with pytest.raises(HarnessError):
        _observe(Flapping([]), _scenario(AddComplete(1)), timeout_s=0.2)


def test_remove_seeder_drops_hash_from_expected():
    h = FakeHarness(["stalledUP"])
    observed = _observe(h, _scenario(AddComplete(2), RemoveSeeder()))
    assert len(h.deleted) == 1
    assert observed == [Counts(0, 0, 2, 0, 0), Counts(0, 0, 1, 0, 0)]  # not left=1


def test_incomplete_adds_are_counted_by_their_own_state():
    h = FakeHarness(["checkingResumeData", "queuedDL"])
    assert _observe(h, _scenario(AddIncomplete(2))) == [Counts(0, 2, 0, 0, 0)]


def test_each_run_adds_fresh_hashes():
    """A retry must not re-add a hash whose files reset() may still be deleting."""
    first, second = FakeHarness(["stalledUP"]), FakeHarness(["stalledUP"])
    _observe(first, _scenario(AddComplete(2)))
    _observe(second, _scenario(AddComplete(2)))
    assert not first.added.keys() & second.added.keys()


# --- retry --------------------------------------------------------------------


def test_one_retry_passes_when_the_second_attempt_does():
    calls = []

    def attempt():
        calls.append(1)
        if len(calls) == 1:
            raise HarnessError("flaky")

    with_one_retry(attempt)
    assert len(calls) == 2


def test_one_retry_fails_when_both_attempts_do():
    attempts = []

    def attempt():
        attempts.append(1)
        exc = AssertionError(f"attempt {len(attempts)}")
        exc.add_note(f"raw step 0: attempt {len(attempts)} states")
        raise exc

    with pytest.raises(AssertionError, match="attempt 2") as info:
        with_one_retry(attempt)
    notes = "\n".join(info.value.__notes__)
    assert "raw step 0: attempt 2 states" in notes
    assert "AssertionError('attempt 1')" in notes  # the first failure's message
    assert "raw step 0: attempt 1 states" in notes  # and its diagnostics


# --- result file --------------------------------------------------------------


def test_result_kind_precedence():
    assert result_kind([], 0) == "pass"
    assert result_kind(["disagreement"], 1) == "disagreement"
    assert result_kind(["disagreement", "unmapped-state"], 1) == "unmapped-state"
    assert result_kind(["unmapped-state", "harness-error", "disagreement"], 1) == "harness-error"


_INNER_TESTS = """
import pytest
from tests.conformance.qbt import HarnessError
from tests.conformance.runner import StaleKnownEntry
from tests.conformance.states import UnmappedState

pytestmark = pytest.mark.container


@pytest.fixture(scope="session")
def broken_container():
    raise HarnessError("no container")


def test_setup_error(broken_container):
    pass


def test_unmapped():
    raise UnmappedState("missingFiles")


def test_disagreement():
    assert 1 == 2


def test_stale():
    raise StaleKnownEntry("model now matches")


def test_passes():
    pass
"""


def _inner_run(pytester: pytest.Pytester, source: str) -> pytest.HookRecorder:
    pytester.makeini("[pytest]\nmarkers =\n    container: inner\n")
    pytester.makepyfile(test_inner=source)
    return pytester.inline_run(plugins=[conformance_conftest])


def test_failure_kind_mapping(pytester: pytest.Pytester):
    from tests.conformance.runner import classify_report

    rec = _inner_run(pytester, _INNER_TESTS)
    kinds = {
        (r.nodeid.rsplit("::", 1)[1], r.when): classify_report(r)
        for r in rec.getreports("pytest_runtest_logreport")
        if r.failed
    }
    assert kinds == {
        ("test_setup_error", "setup"): "harness-error",
        ("test_unmapped", "call"): "unmapped-state",
        ("test_disagreement", "call"): "disagreement",
        ("test_stale", "call"): "disagreement",
    }
    result = json.loads((pytester.path / "conformance-result.json").read_text())
    assert result["kind"] == "harness-error"
    assert sorted(i.rsplit("::", 1)[1] for i in result["failed"]) == [
        "test_disagreement",
        "test_setup_error",
        "test_stale",
        "test_unmapped",
    ]


def test_nothing_classified_with_a_failing_exit_is_a_harness_error():
    assert result_kind([], 2) == "harness-error"


def test_no_result_file_without_a_container_item(pytester: pytest.Pytester):
    _inner_run(pytester, "def test_plain():\n    assert False\n")
    assert not (pytester.path / "conformance-result.json").exists()


def test_collect_only_writes_no_result_file(pytester: pytest.Pytester):
    pytester.makeini("[pytest]\nmarkers =\n    container: inner\n")
    pytester.makepyfile(test_inner=_INNER_TESTS)
    pytester.inline_run("--collect-only", plugins=[conformance_conftest])
    assert not (pytester.path / "conformance-result.json").exists()


def test_collection_error_writes_harness_error(pytester: pytest.Pytester):
    pytester.makepyfile(test_broken="import does_not_exist\n")
    _inner_run(pytester, "import pytest\n\n@pytest.mark.container\ndef test_ok():\n    pass\n")
    result = json.loads((pytester.path / "conformance-result.json").read_text())
    assert result["kind"] == "harness-error"
