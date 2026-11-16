"""Make seeders actually upload: preferences, seeders and a capped leecher on a qbt_pair.

The leecher dials the seeder directly with addPeers (no tracker, no DHT), and
caps each torrent's download so a 32 MiB torrent keeps the seeder uploading for
the whole observation window.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass

from tests.conformance.containers import Pair
from tests.conformance.qbt import HarnessError, QbtHarness, Row
from tests.conformance.runner import set_prefs_verified
from tests.conformance.scenarios import SLOW_TIMER_S, prefs_for
from tests.conformance.states import tally
from tests.conformance.torrents import HarnessTorrent
from tests.model.queue import Counts, QueueConfig

PEER_PORT = 6881
PEER_PREFS: dict[str, object] = {
    "listen_port": PEER_PORT,
    "random_port": False,
    "upnp": False,
    "dht": False,
    "pex": False,
    "lsd": False,
    "limit_lan_peers": True,
}
# Queueing off, so the leecher's own queue never limits what it downloads.
LEECHER_CFG = QueueConfig(False, -1, -1, -1, False, False)

UPLOAD_SIZE = 32 * 1024 * 1024
UPLOAD_PIECE_LENGTH = 256 * 1024
LEECHER_CAP = 65536  # bytes/s per torrent
UPLOAD_THRESHOLD = 2048  # bytes/s; a seeder must exceed this to count as uploading

SEEDING_STATES = frozenset({"stalledUP", "queuedUP"})
SEEDERS_TIMEOUT_S = 15.0
PRESENT_TIMEOUT_S = 10.0


def apply_prefs(pair: Pair, seeder_cfg: QueueConfig) -> None:
    """Set and read back the seeder's and the leecher's full preferences."""
    extra = frozenset(PEER_PREFS)
    seeder = prefs_for(seeder_cfg, slow_timer_s=SLOW_TIMER_S) | PEER_PREFS
    leecher = prefs_for(LEECHER_CFG, slow_timer_s=SLOW_TIMER_S) | PEER_PREFS
    set_prefs_verified(pair.seeder, seeder, extra_keys=extra)
    set_prefs_verified(pair.leecher, leecher, extra_keys=extra)


def _hashes(torrents: list[HarnessTorrent]) -> list[str]:
    return [t.info_hash for t in torrents]


def add_seeders(h: QbtHarness, torrents: list[HarnessTorrent]) -> None:
    """Add complete torrents; raise HarnessError unless all seed within 15 s."""
    for t in torrents:
        h.add(t, complete=True)
    want = set(_hashes(torrents))
    states: dict[str, str] = {}
    deadline = time.monotonic() + SEEDERS_TIMEOUT_S
    while time.monotonic() < deadline:
        states = {k: v for k, v in h.states().items() if k in want}
        if states.keys() == want and set(states.values()) <= SEEDING_STATES:
            return
        time.sleep(0.25)
    raise HarnessError(f"seeders not stalledUP/queuedUP within {SEEDERS_TIMEOUT_S:.0f}s: {states}")


def _await_present(h: QbtHarness, hashes: list[str]) -> None:
    """Wait until the client lists every hash, so per-torrent calls find them."""
    deadline = time.monotonic() + PRESENT_TIMEOUT_S
    while time.monotonic() < deadline:
        if set(hashes) <= h.states().keys():
            return
        time.sleep(0.25)
    raise HarnessError(f"torrents not listed within {PRESENT_TIMEOUT_S:.0f}s: {hashes}")


def attach_leecher(pair: Pair, torrents: list[HarnessTorrent]) -> None:
    """Add the torrents incomplete on the leecher, cap them, and point them at the seeder."""
    for t in torrents:
        pair.leecher.add(t, complete=False)
    hashes = _hashes(torrents)
    _await_present(pair.leecher, hashes)
    pair.leecher.set_download_limit(hashes, LEECHER_CAP)
    reconnect(pair, torrents)


def reconnect(pair: Pair, torrents: list[HarnessTorrent]) -> None:
    """Re-issue addPeers; called every bring-up poll to sidestep libtorrent's 60 s back-off."""
    pair.leecher.add_peers(_hashes(torrents), [f"{pair.seeder_ip}:{PEER_PORT}"])


# --- pure decision logic (no Docker) -------------------------------------------


class UnstableWindow(AssertionError):
    """The final observation window's tallies were not all equal: a disagreement."""


@dataclass(frozen=True)
class Poll:
    t: float  # seconds since the event
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class Outcome:
    before: Counts
    after: Counts
    active_at_bringup: int
    verdict: str


def _is_uploading(r: Row) -> bool:
    return r.state == "uploading" and r.upspeed > UPLOAD_THRESHOLD


def _of(rows: Sequence[Row], hashes: set[str]) -> list[Row]:
    return [r for r in rows if r.hash in hashes]


def ready(rows: Sequence[Row], seeders: set[str]) -> bool:
    """Every seeder queuedUP or uploading fast, and at least one uploading."""
    mine = _of(rows, seeders)
    if {r.hash for r in mine} != seeders:
        return False
    return all(r.state == "queuedUP" or _is_uploading(r) for r in mine) and any(
        _is_uploading(r) for r in mine
    )


def idle_ready(rows: Sequence[Row], seeders: set[str]) -> bool:
    """Every seeder is stalledUP or queuedUP (the U0 case: nobody uploads)."""
    mine = _of(rows, seeders)
    return {r.hash for r in mine} == seeders and all(r.state in SEEDING_STATES for r in mine)


def active_seeders(rows: Sequence[Row], seeders: set[str]) -> int:
    return sum(1 for r in _of(rows, seeders) if r.state != "queuedUP")


def before_counts(rows: Sequence[Row], seeders: set[str]) -> Counts:
    """Tally the seeders only; downloads not yet added must not count as LEFT."""
    return tally({r.hash: r.state for r in rows}, seeders)


def check_uploading(polls: Sequence[Poll], seeders: set[str]) -> None:
    """Raise HarnessError if a never-demoted seeder stops uploading in any poll."""
    demoted: set[str] = set()
    for poll in polls:
        for r in _of(poll.rows, seeders):
            if r.state == "queuedUP":
                demoted.add(r.hash)
            elif r.hash not in demoted and not _is_uploading(r):
                raise HarnessError(f"seeder stopped uploading at t={poll.t}: {r}")


def settled(
    polls: Sequence[Poll], seeders: set[str], downloads: set[str], *, stable_s: float = 30.0
) -> Counts:
    """The one tally shared by every poll in the final `stable_s` seconds."""
    cutoff = polls[-1].t - stable_s
    window = [p for p in polls if p.t >= cutoff]
    wanted = seeders | downloads
    tallies = [(p.t, tally({r.hash: r.state for r in p.rows}, wanted)) for p in window]
    if len({c for _, c in tallies}) != 1:
        listing = "; ".join(f"t={t}: {c}" for t, c in tallies)
        raise UnstableWindow(f"counts changed in the final {stable_s:.0f}s: {listing}")
    return tallies[0][1]


def verdict(before: Counts, after: Counts, *, tot: int, n_downloads: int) -> str:
    """'yield' if uploading seeders gave up slots, 'hold' if they kept them."""
    free = max(0, tot - before.seeding) if tot != -1 else n_downloads
    started = after.downloading
    if after.seeding_queued > before.seeding_queued or started > free:
        return "yield"
    if after.seeding_queued == before.seeding_queued and after.queued == n_downloads - started:
        return "hold"
    return "other"
