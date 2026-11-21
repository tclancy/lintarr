"""What reaching a share limit does to a torrent and to its files.

One row per qBittorrent `ShareLimitAction`, each measured against the pinned
image rather than assumed: see
docs/measurements/2026-11-21-qbt-share-limit-actions.md. The rows are what
`orphan-inevitability` reasons from
(docs/superpowers/specs/2026-11-19-orphan-inevitability-design.md, "Axioms"),
so this table is the axiom, and `tests/conformance/test_share_limit_actions.py`
is the test that it still holds.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from tests.conformance.qbt import QbtHarness, Row
from tests.conformance.runner import set_prefs_verified
from tests.conformance.torrents import HarnessTorrent
from tests.conformance.watching import POLL

#: Every preference these tests touch, at the image's own default. `reset()`
#: deletes torrents but keeps preferences, so each case restores the lot: a key
#: left out would run on the case before it (`scenarios.PREF_KEYS` says why).
SHARE_LIMIT_DEFAULTS: dict[str, object] = {
    # Queueing off: a batch of five seeders would otherwise sit queuedUP against
    # max_active_uploads = 3 (finding F2, measurements 2026-11-13). This file is
    # about which action a fired limit takes, not about the queue.
    "queueing_enabled": False,
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
    "max_inactive_seeding_time_enabled": False,
}

#: States a torrent that is still seeding can be in. Anything else means the
#: share limit (or something worse) has acted on it.
SEEDING_STATES = frozenset({"uploading", "stalledUP", "forcedUP", "queuedUP", "checkingUP"})


@dataclass(frozen=True)
class Observed:
    """What became of one torrent once its share limit had fired."""

    state: str | None  # None: gone from torrents/info
    file_exists: bool
    super_seeding: bool


@dataclass(frozen=True)
class Fate:
    """The measured outcome of one share-limit action, global or per category."""

    action: int  # the value `max_ratio_act` carries
    name: str  # the string a category's `share_limit_action` carries
    state: str | None  # the state afterwards; None: gone from torrents/info
    keeps_files: bool
    super_seeding: bool = False

    @property
    def observed(self) -> Observed:
        """This fate as `watch_until_fired` reports it."""
        return Observed(self.state, file_exists=self.keeps_files, super_seeding=self.super_seeding)


#: Measured 2026-11-21 on lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.14-ls476.
FATES = (
    Fate(0, "Stop", "stoppedUP", keeps_files=True),
    Fate(1, "Remove", None, keeps_files=True),
    Fate(2, "EnableSuperSeeding", "stalledUP", keeps_files=True, super_seeding=True),
    Fate(3, "RemoveWithContent", None, keeps_files=False),
)

FATE_BY_NAME = {fate.name: fate for fate in FATES}


def set_share_limit_prefs(qbt: QbtHarness, prefs: Mapping[str, object]) -> None:
    """Set `prefs` over the defaults, and refuse to go on unless they read back.

    setPreferences answers 200 and silently ignores a key it does not know, so a
    case whose action never took would otherwise measure the case before it.
    """
    full = SHARE_LIMIT_DEFAULTS | dict(prefs)
    set_prefs_verified(qbt, full, extra_keys=frozenset(full))


def disarm(qbt: QbtHarness, action: int) -> None:
    """Empty the client and make `action` the global action, with no limit armed.

    Adding the torrents before the limit is armed keeps the whole population in
    one share-limit pass, so a batch of categories costs one pass, not one each.
    """
    qbt.reset()
    set_share_limit_prefs(qbt, {"max_ratio_act": action})


def arm_ratio_zero(qbt: QbtHarness, action: int) -> None:
    """Arm the global ratio limit at 0, which every seeding torrent has already met."""
    set_share_limit_prefs(qbt, {"max_ratio_act": action, "max_ratio_enabled": True, "max_ratio": 0})


def _fired(row: Row | None) -> bool:
    """The share limit has visibly acted on this torrent.

    Caller's precondition: the torrent was seeding when the watch began (add it
    with `watching.add_settled(want="stalledUP")`). A torrent that is not in
    `torrents/info` yet would otherwise read as removed.
    """
    if row is None:
        return True  # removed, with or without its files
    return row.state not in SEEDING_STATES or row.super_seeding


def _observe(qbt: QbtHarness, t: HarnessTorrent, row: Row | None) -> Observed:
    return Observed(
        state=None if row is None else row.state,
        file_exists=(qbt.downloads / t.name).exists(),
        super_seeding=False if row is None else row.super_seeding,
    )


def watch_until_fired(
    qbt: QbtHarness, torrents: Mapping[str, HarnessTorrent], timeout: float
) -> dict[str, Observed]:
    """Wait until every torrent's limit has fired, then say what became of each.

    Keys are the caller's labels, so one batch can carry one torrent per
    category. The files are read once the population has all fired, which is
    where `test_assumptions` reads them too: a removal takes its content with it.
    """
    deadline = time.monotonic() + timeout
    while True:
        rows = {row.hash: row for row in qbt.rows()}
        pending = sorted(
            label for label, t in torrents.items() if not _fired(rows.get(t.info_hash))
        )
        if not pending:
            return {label: _observe(qbt, t, rows.get(t.info_hash)) for label, t in torrents.items()}
        if time.monotonic() >= deadline:
            pytest.fail(
                f"share limit did not fire for {pending} within {timeout:.0f}s; "
                f"states {qbt.states()}"
            )
        time.sleep(POLL)
