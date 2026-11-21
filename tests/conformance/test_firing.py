"""Unit tests (no Docker) for the share-limit table and the fired-limit watcher.

The container tests in test_share_limit_actions.py are only as good as the
detection here: a watcher that called every torrent "fired" on sight would pass
them while measuring nothing.
"""

import json

import pytest

from lintarr.clients.qbittorrent.share_limits import ACTION_BY_NAME, DEFAULT_ACTION
from tests.conformance import categories, firing
from tests.conformance.firing import (
    FATE_BY_NAME,
    FATES,
    Observed,
    watch_until_fired,
)
from tests.conformance.qbt import Row
from tests.conformance.torrents import make_torrent

# --- the table ----------------------------------------------------------------


def test_every_action_the_check_maps_has_a_measured_fate():
    """`ACTION_BY_NAME` is what `effective_action` returns; an action with no row
    here is an action nothing has measured."""
    assert {fate.name: fate.action for fate in FATES} == ACTION_BY_NAME


def test_default_is_not_a_fate_of_its_own():
    assert DEFAULT_ACTION not in FATE_BY_NAME


def test_only_remove_with_content_takes_the_files():
    assert [fate.name for fate in FATES if not fate.keeps_files] == ["RemoveWithContent"]


def test_a_fate_reads_as_the_observation_it_predicts():
    assert FATE_BY_NAME["Remove"].observed == Observed(
        state=None, file_exists=True, super_seeding=False
    )
    assert FATE_BY_NAME["Stop"].observed == Observed(
        state="stoppedUP", file_exists=True, super_seeding=False
    )


# --- the watcher --------------------------------------------------------------


class _FakeHarness:
    """Answers rows() from a scripted sequence, one entry per poll."""

    def __init__(self, polls: list[list[Row]], downloads) -> None:
        self._polls = polls
        self.downloads = downloads

    def rows(self) -> list[Row]:
        return self._polls[0] if len(self._polls) == 1 else self._polls.pop(0)

    def states(self) -> dict[str, str]:
        return {row.hash: row.state for row in self.rows()}


def _row(t, state: str, *, super_seeding: bool = False) -> Row:
    return Row(t.info_hash, t.name, state, 0, 0, 0, super_seeding=super_seeding)


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firing, "POLL", 0)


def _seeded(tmp_path, name: str):
    t = make_torrent(name)
    (tmp_path / t.name).write_bytes(t.data)
    return t


def test_a_seeding_torrent_is_not_yet_fired(tmp_path):
    t = _seeded(tmp_path, "still-seeding")
    h = _FakeHarness([[_row(t, "stalledUP")]], tmp_path)

    with pytest.raises(pytest.fail.Exception, match=r"did not fire for \['t'\]"):
        watch_until_fired(h, {"t": t}, timeout=0)


def test_a_stopped_torrent_has_fired_and_keeps_its_file(tmp_path):
    t = _seeded(tmp_path, "stopped")
    h = _FakeHarness([[_row(t, "stalledUP")], [_row(t, "stoppedUP")]], tmp_path)

    assert watch_until_fired(h, {"t": t}, timeout=5) == {
        "t": Observed("stoppedUP", file_exists=True, super_seeding=False)
    }


def test_a_super_seeding_torrent_has_fired_while_still_seeding(tmp_path):
    t = _seeded(tmp_path, "super")
    h = _FakeHarness([[_row(t, "stalledUP", super_seeding=True)]], tmp_path)

    assert watch_until_fired(h, {"t": t}, timeout=5) == {
        "t": Observed("stalledUP", file_exists=True, super_seeding=True)
    }


def test_a_vanished_torrent_has_fired_and_its_file_is_read(tmp_path):
    kept = _seeded(tmp_path, "kept")
    taken = make_torrent("taken")  # no file: removed with its content
    h = _FakeHarness([[]], tmp_path)

    assert watch_until_fired(h, {"kept": kept, "taken": taken}, timeout=5) == {
        "kept": Observed(None, file_exists=True, super_seeding=False),
        "taken": Observed(None, file_exists=False, super_seeding=False),
    }


def test_the_watch_waits_for_every_torrent_not_merely_one(tmp_path):
    fast = _seeded(tmp_path, "fast")
    slow = _seeded(tmp_path, "slow")
    h = _FakeHarness([[_row(slow, "stalledUP")], [_row(slow, "stoppedUP")]], tmp_path)

    with pytest.raises(pytest.fail.Exception, match=r"did not fire for \['slow'\]"):
        watch_until_fired(h, {"fast": fast, "slow": slow}, timeout=0)


# --- the pre-seeded categories ------------------------------------------------


def test_written_categories_carry_their_action_and_inherit_every_limit(tmp_path):
    path = categories.write_categories(tmp_path, {"tv": "Remove"})

    assert path == tmp_path / "qBittorrent" / "categories.json"
    assert json.loads(path.read_text()) == {
        "tv": {
            "save_path": "/downloads",
            "download_path": None,
            "ratio_limit": -2,
            "seeding_time_limit": -2,
            "inactive_seeding_time_limit": -2,
            "share_limit_action": "Remove",
        }
    }


def test_written_categories_use_the_keys_qbittorrent_parses(tmp_path):
    """CategoryOptions::fromJSON reads these keys and nothing else; a camelCase
    key would be silently dropped and the category would read back Default."""
    body = json.loads(categories.write_categories(tmp_path, {"tv": "Stop"}).read_text())["tv"]

    assert set(body) == {
        "save_path",
        "download_path",
        "ratio_limit",
        "seeding_time_limit",
        "inactive_seeding_time_limit",
        "share_limit_action",
    }
