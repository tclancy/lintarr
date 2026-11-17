"""Feasibility gate: the leecher really makes a seeder upload, and its cap holds. Needs Docker.

Every failure here is a harness property, not a queue-model claim, so each raises
HarnessError (kind `harness-error`), never AssertionError (`disagreement`).
"""

import itertools
import time

import pytest

from tests.conformance.qbt import HarnessError
from tests.conformance.runner import with_one_retry
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
_ATTEMPTS = itertools.count(1)  # module-wide, so a retry never reuses a torrent name


def _row(h, info_hash: str):
    return next((r for r in h.rows() if r.hash == info_hash), None)


def _is_uploading(row) -> bool:
    return row is not None and row.state == "uploading" and row.upspeed > SLOW_THRESHOLD


def _await_uploading(pair, torrents) -> None:
    """Poll every 1 s for up to 60 s until the seeder uploads above threshold twice running."""
    info_hash = torrents[0].info_hash
    streak, row = 0, None
    deadline = time.monotonic() + BRING_UP_S
    while time.monotonic() < deadline:
        reconnect(pair, torrents)
        row = _row(pair.seeder, info_hash)
        streak = streak + 1 if _is_uploading(row) else 0
        if streak == 2:
            return
        time.sleep(1)
    raise HarnessError(f"seeder never uploaded above {SLOW_THRESHOLD} B/s; last row {row!r}")


def _assert_cap_holds(pair, info_hash: str) -> None:
    """For 10 s, the leecher stays under its cap while the seeder is still uploading."""
    speeds = []
    deadline = time.monotonic() + CAP_WINDOW_S
    while time.monotonic() < deadline:
        seeder_row = _row(pair.seeder, info_hash)
        if not _is_uploading(seeder_row):
            raise HarnessError(f"seeder stopped uploading in the cap window: {seeder_row!r}")
        row = _row(pair.leecher, info_hash)
        speeds.append(row.dlspeed if row else None)
        time.sleep(1)
    if not speeds or any(s is None or s > LEECHER_CAP * 1.5 for s in speeds):
        raise HarnessError(f"leecher cap {LEECHER_CAP} B/s breached or row missing: {speeds}")


def _assert_smoke_holds(pair) -> None:
    pair.seeder.reset()
    pair.leecher.reset()
    apply_prefs(pair, UNLIMITED_SEEDER)
    name = f"smoke-{next(_ATTEMPTS)}"
    torrents = [make_torrent(name, size=UPLOAD_SIZE, piece_length=UPLOAD_PIECE_LENGTH)]
    add_seeders(pair.seeder, torrents)
    attach_leecher(pair, torrents)
    _await_uploading(pair, torrents)
    _assert_cap_holds(pair, torrents[0].info_hash)


def test_leecher_makes_a_seeder_upload_above_threshold(qbt_pair):
    with_one_retry(lambda: _assert_smoke_holds(qbt_pair))
