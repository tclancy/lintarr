"""Feasibility gate: the leecher really makes a seeder upload, and its cap holds. Needs Docker."""

import time

import pytest

from tests.conformance.torrents import make_torrent
from tests.conformance.uploading import (
    LEECHER_CAP,
    UPLOAD_PIECE_LENGTH,
    UPLOAD_SIZE,
    add_seeders,
    apply_prefs,
    attach_leecher,
    reconnect,
)
from tests.model.queue import UNLIMITED, QueueConfig

pytestmark = pytest.mark.container

SLOW_THRESHOLD = 2048  # the image default slow-upload threshold, bytes/s
BRING_UP_S = 60.0
CAP_WINDOW_S = 10.0
UNLIMITED_SEEDER = QueueConfig(True, UNLIMITED, UNLIMITED, UNLIMITED, False, False)


def _row(h, info_hash: str):
    return next((r for r in h.rows() if r.hash == info_hash), None)


def _await_uploading(pair, torrents) -> None:
    """Poll every 1 s for up to 60 s until the seeder uploads above threshold twice running."""
    info_hash = torrents[0].info_hash
    streak, row = 0, None
    deadline = time.monotonic() + BRING_UP_S
    while time.monotonic() < deadline:
        reconnect(pair, torrents)
        row = _row(pair.seeder, info_hash)
        uploading = row is not None and row.state == "uploading" and row.upspeed > SLOW_THRESHOLD
        streak = streak + 1 if uploading else 0
        if streak == 2:
            return
        time.sleep(1)
    pytest.fail(f"seeder never uploaded above {SLOW_THRESHOLD} B/s; last row {row!r}")


def test_leecher_makes_a_seeder_upload_above_threshold(qbt_pair):
    apply_prefs(qbt_pair, UNLIMITED_SEEDER)
    torrents = [make_torrent("smoke-0", size=UPLOAD_SIZE, piece_length=UPLOAD_PIECE_LENGTH)]
    add_seeders(qbt_pair.seeder, torrents)
    attach_leecher(qbt_pair, torrents)

    _await_uploading(qbt_pair, torrents)

    speeds = []
    deadline = time.monotonic() + CAP_WINDOW_S
    while time.monotonic() < deadline:
        row = _row(qbt_pair.leecher, torrents[0].info_hash)
        speeds.append(row.dlspeed if row else None)
        time.sleep(1)
    assert speeds and all(s is not None and s <= LEECHER_CAP * 1.5 for s in speeds), speeds
