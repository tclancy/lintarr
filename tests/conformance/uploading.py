"""Make seeders actually upload: preferences, seeders and a capped leecher on a qbt_pair.

The leecher dials the seeder directly with addPeers (no tracker, no DHT), and
caps each torrent's download so a 32 MiB torrent keeps the seeder uploading for
the whole observation window.
"""

import time

from tests.conformance.containers import Pair
from tests.conformance.qbt import HarnessError, QbtHarness
from tests.conformance.runner import set_prefs_verified
from tests.conformance.scenarios import SLOW_TIMER_S, prefs_for
from tests.conformance.torrents import HarnessTorrent
from tests.model.queue import QueueConfig

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
