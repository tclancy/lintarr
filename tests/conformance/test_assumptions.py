"""Pins what docs/measurements/2026-11-13-qbt-conformance-assumptions.md measured.

Items 1-3 of Task 4, against the pinned image. Each test asserts the behaviour
as it was observed, so a qBittorrent change trips here before it silently
changes what a differential scenario means. Needs Docker.
"""

import time

import pytest

from tests.conformance.qbt import QbtHarness
from tests.conformance.torrents import HarnessTorrent, make_torrent

pytestmark = pytest.mark.container

# Measured: the share limit fired 8.4-8.9 s after the torrent reached stalledUP
# (one pass of qBittorrent's share-limit check). 30 s is that pass three times.
SHARE_LIMIT_TIMEOUT = 30.0
# Measured: an add settles in 1.3-3.2 s (checkingResumeData, then any queue release).
SETTLE_TIMEOUT = 10.0
# How long a torrent that should stay queued is watched for.
HOLD_SECONDS = 15.0
POLL = 0.25

# Every key a test below touches, at the image's own default, restored first.
_QUEUE_DEFAULTS: dict[str, object] = {
    "queueing_enabled": True,
    "max_active_downloads": 3,
    "max_active_uploads": 3,
    "max_active_torrents": 5,
    "dont_count_slow_torrents": False,
    "slow_torrent_inactive_timer": 60,
    "max_ratio_enabled": False,
    "max_ratio": -1,
    "max_ratio_act": 0,
    "max_seeding_time_enabled": False,
    "max_seeding_time": -1,
}


def _preferences(qbt: QbtHarness) -> dict[str, object]:
    """QbtHarness has no preferences read; this file may not widen it (Task 4 scope)."""
    r = qbt._http.get("/api/v2/app/preferences")
    r.raise_for_status()
    return r.json()


def _fresh(qbt: QbtHarness, prefs: dict[str, object]) -> None:
    qbt.reset()
    qbt.set_prefs(_QUEUE_DEFAULTS | prefs)


def _assert_eventually(qbt: QbtHarness, done, timeout: float) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    states = qbt.states()
    while not done(states):
        if time.monotonic() >= deadline:
            pytest.fail(f"condition not met within {timeout:.0f}s; last states {states}")
        time.sleep(POLL)
        states = qbt.states()
    return states


def _state_is(t: HarnessTorrent, want: str):
    return lambda states: states.get(t.info_hash) == want


def _add_settled(qbt: QbtHarness, t: HarnessTorrent, *, complete: bool, want: str) -> None:
    qbt.add(t, complete=complete)
    _assert_eventually(qbt, _state_is(t, want), SETTLE_TIMEOUT)


def test_ratio_zero_with_stop_action_stops_an_unshared_seeder(qbt):
    """Item 1: max_ratio=0 fires on a just-added complete torrent with zero upload."""
    _fresh(qbt, {"max_ratio_enabled": True, "max_ratio": 0, "max_ratio_act": 0})
    t = make_torrent("assume-ratio-stop")
    _add_settled(qbt, t, complete=True, want="stalledUP")
    _assert_eventually(qbt, _state_is(t, "stoppedUP"), SHARE_LIMIT_TIMEOUT)


def test_seeding_time_zero_with_stop_action_stops_a_seeder(qbt):
    """Item 1's fallback, measured too: max_seeding_time=0 fires the same way."""
    _fresh(qbt, {"max_seeding_time_enabled": True, "max_seeding_time": 0, "max_ratio_act": 0})
    t = make_torrent("assume-seedtime-stop")
    _add_settled(qbt, t, complete=True, want="stalledUP")
    _assert_eventually(qbt, _state_is(t, "stoppedUP"), SHARE_LIMIT_TIMEOUT)


def test_ratio_zero_with_remove_files_action_removes_torrent_and_data(qbt):
    """Item 2: action 3 makes the torrent vanish from torrents/info, files and all."""
    _fresh(qbt, {"max_ratio_enabled": True, "max_ratio": 0, "max_ratio_act": 3})
    t = make_torrent("assume-ratio-remove")
    _add_settled(qbt, t, complete=True, want="stalledUP")
    _assert_eventually(qbt, lambda states: t.info_hash not in states, SHARE_LIMIT_TIMEOUT)
    assert not (qbt._downloads / t.name).exists()


@pytest.mark.parametrize("value", [-5, 0, 1])
def test_slow_timer_has_no_api_minimum(qbt, value):
    """Item 3: setPreferences stores any integer for the slow timer, negatives included."""
    _fresh(qbt, {"slow_torrent_inactive_timer": value})
    assert _preferences(qbt)["slow_torrent_inactive_timer"] == value


_DOWNLOAD_LIMIT_1 = {"max_active_downloads": 1, "max_active_torrents": -1}
_TORRENT_LIMIT_1 = {"max_active_downloads": -1, "max_active_torrents": 1}


def _two_incompletes(
    qbt: QbtHarness, limits: dict[str, object], dont_count: bool, tag: str
) -> HarnessTorrent:
    """One slow download holds the only slot `limits` allows; return the second torrent."""
    _fresh(
        qbt,
        limits
        | {
            "max_active_uploads": -1,
            "dont_count_slow_torrents": dont_count,
            "slow_torrent_inactive_timer": 1,
        },
    )
    _add_settled(qbt, make_torrent(f"assume-{tag}-a"), complete=False, want="stalledDL")
    b = make_torrent(f"assume-{tag}-b")
    qbt.add(b, complete=False)
    return b


def _assert_held_in_queue(qbt: QbtHarness, t: HarnessTorrent) -> None:
    _assert_eventually(qbt, _state_is(t, "queuedDL"), SETTLE_TIMEOUT)
    time.sleep(HOLD_SECONDS)
    assert qbt.states()[t.info_hash] == "queuedDL"


def test_slow_exemption_frees_the_download_limit(qbt):
    """Item 3, end to end: exemption on, 1 s timer, max_active_downloads=1 full.
    The second download reaches stalledDL within one settle (measured 1.3-3.2 s;
    it sometimes passes through queuedDL for ~1.6 s, sometimes skips it)."""
    b = _two_incompletes(qbt, _DOWNLOAD_LIMIT_1, dont_count=True, tag="dl-exempt")
    _assert_eventually(qbt, _state_is(b, "stalledDL"), SETTLE_TIMEOUT)


def test_download_limit_holds_without_exemption(qbt):
    """Item 3's control: the same population with the exemption off stays queued."""
    b = _two_incompletes(qbt, _DOWNLOAD_LIMIT_1, dont_count=False, tag="dl-control")
    _assert_held_in_queue(qbt, b)


def test_slow_exemption_does_not_free_the_torrent_limit(qbt):
    """Item 3's stop-rule finding: exemption on, max_active_torrents=1 full of one
    slow download. The second download stays queuedDL (held 120 s when measured).
    trace() rule 3 predicts it starts."""
    b = _two_incompletes(qbt, _TORRENT_LIMIT_1, dont_count=True, tag="tot-exempt")
    _assert_held_in_queue(qbt, b)


@pytest.mark.parametrize("dont_count", [True, False], ids=["exempt", "not-exempt"])
def test_download_displaces_seeder_from_full_torrent_limit(qbt, dont_count):
    """Item 3's surprise: max_active_torrents full of seeders does not queue a new
    download. The download starts and a seeder is demoted to queuedUP."""
    _fresh(
        qbt,
        {
            "max_active_downloads": -1,
            "max_active_uploads": -1,
            "max_active_torrents": 2,
            "dont_count_slow_torrents": dont_count,
            "slow_torrent_inactive_timer": 1,
        },
    )
    seeds = [make_torrent(f"assume-displace-{dont_count}-s{i}") for i in range(2)]
    for s in seeds:
        qbt.add(s, complete=True)
    _assert_eventually(qbt, lambda st: all(st.get(s.info_hash) == "stalledUP" for s in seeds), 10)
    inc = make_torrent(f"assume-displace-{dont_count}-inc")
    _add_settled(qbt, inc, complete=False, want="stalledDL")
    time.sleep(HOLD_SECONDS)
    states = qbt.states()
    assert states[inc.info_hash] == "stalledDL"
    assert sorted(states[s.info_hash] for s in seeds) == ["queuedUP", "stalledUP"]
