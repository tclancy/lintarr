"""Unit tests (no Docker) for set_prefs_verified: qBittorrent ignores unknown
preference keys silently, so a scenario's preferences count as set only once
they read back."""

import pytest

from tests.conformance.qbt import HarnessError
from tests.conformance.runner import set_prefs_verified
from tests.conformance.scenarios import SCENARIOS


class _FakePrefsHarness:
    """Stores what set_prefs sent, then answers preferences() with it, after
    `rewrite` has had a chance to change it the way a real client might."""

    def __init__(self, rewrite=lambda prefs: prefs) -> None:
        self._rewrite = rewrite
        self._stored: dict[str, object] = {}
        self.sent: list[dict[str, object]] = []

    def set_prefs(self, prefs: dict[str, object]) -> None:
        self.sent.append(dict(prefs))
        self._stored = self._rewrite(dict(prefs))

    def preferences(self) -> dict[str, object]:
        return dict(self._stored) | {"unrelated_key": "x"}


_PREFS = next(s.prefs for s in SCENARIOS if s.id == "5-downloads-bind-slow-on")


def test_matching_read_back_passes():
    h = _FakePrefsHarness()
    set_prefs_verified(h, _PREFS)
    assert h.sent == [_PREFS]


def test_ints_read_back_as_floats_still_match():
    def as_floats(prefs):
        return {k: float(v) if type(v) is int else v for k, v in prefs.items()}

    h = _FakePrefsHarness(as_floats)
    set_prefs_verified(h, _PREFS)
    assert h.sent == [_PREFS]


def test_a_key_the_client_ignored_is_a_harness_error():
    def ignore_slow(prefs):
        prefs["dont_count_slow_torrents"] = False
        return prefs

    with pytest.raises(HarnessError, match="dont_count_slow_torrents"):
        set_prefs_verified(_FakePrefsHarness(ignore_slow), _PREFS)


def test_a_key_missing_from_the_read_back_is_a_harness_error():
    def drop_timer(prefs):
        del prefs["slow_torrent_inactive_timer"]
        return prefs

    with pytest.raises(HarnessError, match="slow_torrent_inactive_timer"):
        set_prefs_verified(_FakePrefsHarness(drop_timer), _PREFS)


def test_a_bool_is_not_matched_by_an_int():
    def bool_as_int(prefs):
        prefs["queueing_enabled"] = 1
        return prefs

    with pytest.raises(HarnessError, match="queueing_enabled"):
        set_prefs_verified(_FakePrefsHarness(bool_as_int), _PREFS)


def test_prefs_missing_a_pref_key_are_refused_before_sending():
    h = _FakePrefsHarness()
    partial = {k: v for k, v in _PREFS.items() if k != "max_ratio_act"}
    with pytest.raises(HarnessError, match="max_ratio_act"):
        set_prefs_verified(h, partial)
    assert h.sent == []


@pytest.mark.parametrize("s", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_every_scenario_sends_max_ratio_as_qbittorrent_reads_it_back(s):
    """Measurement item 4: with max_ratio_enabled off, qBittorrent reads max_ratio
    back as -1 whatever was sent. prefs_for already sends -1 then, so the strict
    read-back never trips on that documented normalisation."""
    if not s.prefs["max_ratio_enabled"]:
        assert s.prefs["max_ratio"] == -1
