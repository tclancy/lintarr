"""Unit tests (no Docker) for the uploading slice's pure decision logic."""

from collections.abc import Callable

import pytest

from tests.conformance.qbt import HarnessError, Row
from tests.conformance.runner import EXC_TYPE_ATTR, classify_report
from tests.conformance.uploading import (
    UPLOAD_SCENARIOS,
    Outcome,
    Poll,
    Recording,
    UnstableWindow,
    UploadScenario,
    before_counts,
    check_uploading,
    judge,
    ready,
    settled,
    verdict,
)
from tests.model.queue import Counts


def row(h: str, state: str, upspeed: int = 0) -> Row:
    return Row(hash=h, name=h, state=state, upspeed=upspeed, dlspeed=0, num_leechs=0)


def uploading(h: str, upspeed: int = 70000) -> Row:
    return row(h, "uploading", upspeed)


SEEDERS = {"a", "b", "c"}


def test_ready_accepts_queued_seeders():
    rows = [
        uploading("a"),
        uploading("b"),
        uploading("c"),
        row("d", "queuedUP"),
        row("e", "queuedUP"),
    ]
    assert ready(rows, {"a", "b", "c", "d", "e"})
    assert not ready([row(h, "queuedUP") for h in "abcde"], set("abcde"))


def test_ready_rejects_slow_uploader():
    rows = [uploading("a", 2048), uploading("b"), uploading("c")]
    assert not ready(rows, SEEDERS)


def test_before_counts_ignores_absent_downloads():
    rows = [uploading("a"), uploading("b"), uploading("c")]
    assert before_counts(rows, SEEDERS) == Counts(0, 0, 3, 0, 0)


def test_seeder_stopping_mid_window_is_harness_error():
    polls = [
        Poll(0.0, (uploading("a"),)),
        Poll(50.0, (row("a", "stalledUP"),)),
    ]
    with pytest.raises(HarnessError, match="seeder stopped uploading") as exc:
        check_uploading(polls, {"a"})
    assert "50" in str(exc.value)


def test_demoted_seeder_is_excused():
    polls = [
        Poll(0.0, (uploading("a"),)),
        Poll(2.0, (row("a", "queuedUP"),)),
        Poll(3.0, (row("a", "stalledUP"),)),
    ]
    assert check_uploading(polls, {"a"}) is None


def _poll(t: float, downloading: int) -> Poll:
    rows = [row(f"d{i}", "downloading") for i in range(downloading)]
    rows += [row(f"d{i}", "queuedDL") for i in range(downloading, 2)]
    return Poll(t, tuple(rows))


def test_settled_returns_the_stable_counts():
    polls = [_poll(0.0, 0), _poll(40.0, 1), _poll(60.0, 1), _poll(70.0, 1)]
    assert settled(polls, set(), {"d0", "d1"}) == Counts(1, 1, 0, 0, 0)


def test_vanished_seeder_is_harness_error():
    polls = [Poll(0.0, (uploading("a"), uploading("b"))), Poll(7.0, (uploading("a"),))]
    with pytest.raises(HarnessError, match="seeder stopped uploading") as exc:
        check_uploading(polls, {"a", "b"})
    assert "t=7.0" in str(exc.value)


@pytest.mark.parametrize(
    "polls",
    [
        [],
        [_poll(70.0, 1)],
        [_poll(0.0, 1), _poll(70.0, 1)],
        [_poll(45.0, 1), _poll(70.0, 1)],
    ],
    ids=["empty", "single-poll", "single-poll-in-window", "starts-late"],
)
def test_sparse_window_is_harness_error(polls):
    with pytest.raises(HarnessError):
        settled(polls, set(), {"d0", "d1"})


def test_unstable_window_is_a_disagreement():
    polls = [_poll(40.0, 1), _poll(55.0, 1), _poll(60.0, 2), _poll(70.0, 1)]
    with pytest.raises(UnstableWindow) as exc:
        settled(polls, set(), {"d0", "d1"})
    for p in polls:
        assert f"{p.t}" in str(exc.value)


def test_verdict_yield_u1():
    assert verdict(Counts(0, 0, 3, 0, 0), Counts(1, 0, 2, 1, 0), tot=3, n_downloads=1) == "yield"


def test_verdict_hold_u1():
    assert verdict(Counts(0, 0, 3, 0, 0), Counts(0, 1, 3, 0, 0), tot=3, n_downloads=1) == "hold"


def test_verdict_hold_when_downloads_fit_free_slots():
    assert verdict(Counts(0, 0, 3, 2, 0), Counts(2, 1, 3, 2, 0), tot=5, n_downloads=3) == "hold"


def test_verdict_yield_w():
    assert verdict(Counts(0, 0, 3, 2, 0), Counts(3, 0, 2, 3, 0), tot=5, n_downloads=3) == "yield"


def test_verdict_other():
    assert verdict(Counts(0, 0, 3, 0, 0), Counts(0, 0, 3, 0, 1), tot=3, n_downloads=1) == "other"


def test_unstable_window_is_classified_disagreement():
    report = pytest.TestReport(
        nodeid="t::x",
        location=("t.py", 0, "x"),
        keywords={},
        outcome="failed",
        longrepr=None,
        when="call",
    )
    setattr(report, EXC_TYPE_ATTR, UnstableWindow)  # what the conftest hook sets
    assert classify_report(report) == "disagreement"


# --- judge -------------------------------------------------------------------


def named(name: str, state: str, upspeed: int = 0) -> Row:
    return Row(hash=f"h-{name}", name=name, state=state, upspeed=upspeed, dlspeed=0, num_leechs=0)


def _window(rows_at: Callable[[float], tuple[Row, ...]]) -> list[Poll]:
    return [Poll(float(t), rows_at(float(t))) for t in range(121)]


def _scenario(sid: str) -> UploadScenario:
    return next(sc for sc in UPLOAD_SCENARIOS if sc.id == sid)


def test_judge_raises_harness_error_for_a_recorded_error():
    rec = Recording(bringup=[], window=[], error="bring-up timed out")
    with pytest.raises(HarnessError, match="bring-up timed out"):
        judge(_scenario("U1-off"), rec)


def test_judge_u1_yield():
    seeds = [named(f"U1-off-r1-c{i}", "uploading", 65000) for i in range(3)]
    bringup = [Poll(0.0, tuple(seeds))]

    def rows_at(t: float) -> tuple[Row, ...]:
        if t < 2:
            return (*seeds, named("U1-off-r1-i0", "queuedDL"))
        return (*seeds[:2], named("U1-off-r1-c2", "queuedUP"), named("U1-off-r1-i0", "stalledDL"))

    o = judge(_scenario("U1-off"), Recording(bringup, _window(rows_at), None))
    assert o == Outcome(Counts(0, 0, 3, 0, 0), Counts(1, 0, 2, 1, 0), 3, "yield")


def test_judge_u0_does_not_require_uploading():
    seeds = tuple(named(f"U0-off-r1-c{i}", "stalledUP") for i in range(3))
    rec = Recording(
        [Poll(0.0, seeds)], _window(lambda t: (*seeds, named("U0-off-r1-i0", "queuedDL"))), None
    )
    assert judge(_scenario("U0-off"), rec).after == Counts(0, 1, 3, 0, 0)


def test_judge_u1_checks_uploading():
    seeds = tuple(named(f"U1-off-r1-c{i}", "stalledUP") for i in range(3))
    rec = Recording(
        [Poll(0.0, seeds)], _window(lambda t: (*seeds, named("U1-off-r1-i0", "queuedDL"))), None
    )
    with pytest.raises(HarnessError, match="seeder stopped uploading"):
        judge(_scenario("U1-off"), rec)


def test_judge_counts_a_download_that_never_appears_as_harness_error():
    seeds = tuple(named(f"U0-off-r1-c{i}", "stalledUP") for i in range(3))
    rec = Recording([Poll(0.0, seeds)], _window(lambda t: seeds), None)
    with pytest.raises(HarnessError, match="downloads"):
        judge(_scenario("U0-off"), rec)


def test_scenarios_match_the_global_constraints():
    got = {
        sc.id: (sc.cfg.max_active_downloads, sc.cfg.max_active_uploads,
                sc.cfg.max_active_torrents, sc.cfg.dont_count_slow_torrents,
                sc.n_seeders, sc.n_downloads, sc.leecher)
        for sc in UPLOAD_SCENARIOS
    }  # fmt: skip
    assert got == {
        "U0-off": (-1, -1, 3, False, 3, 1, False),
        "U1-off": (-1, -1, 3, False, 3, 1, True),
        "U1-on": (-1, -1, 3, True, 3, 1, True),
        "W-off": (3, 3, 5, False, 5, 3, True),
    }
    assert all(
        sc.cfg.queueing_enabled and not sc.cfg.share_limit_enabled for sc in UPLOAD_SCENARIOS
    )
