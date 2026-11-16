"""Make seeders actually upload: preferences, seeders and a capped leecher on a qbt_pair.

The leecher dials the seeder directly with addPeers (no tracker, no DHT), and
caps each torrent's download so a 32 MiB torrent keeps the seeder uploading for
the whole observation window.
"""

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from tests.conformance.containers import Pair
from tests.conformance.qbt import HarnessError, QbtHarness, Row
from tests.conformance.runner import set_prefs_verified
from tests.conformance.scenarios import SLOW_TIMER_S, prefs_for
from tests.conformance.states import tally
from tests.conformance.torrents import HarnessTorrent, make_torrent
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
MIN_WINDOW_POLLS = 2
MAX_WINDOW_GAP_S = 2.0  # the window's first poll may start at most this late
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
        present = {r.hash: r for r in _of(poll.rows, seeders)}
        for h in sorted(seeders):
            r = present.get(h)
            if r is not None and r.state == "queuedUP":
                demoted.add(h)
            elif h not in demoted and (r is None or not _is_uploading(r)):
                raise HarnessError(
                    f"seeder stopped uploading at t={poll.t}: {r or h + ' (absent)'}"
                )


def settled(
    polls: Sequence[Poll], seeders: set[str], downloads: set[str], *, stable_s: float = 30.0
) -> Counts:
    """The one tally shared by every poll in the final `stable_s` seconds."""
    if not polls:
        raise HarnessError("no polls to settle over")
    cutoff = polls[-1].t - stable_s
    window = [p for p in polls if p.t >= cutoff]
    if len(window) < MIN_WINDOW_POLLS or window[0].t - cutoff > MAX_WINDOW_GAP_S:
        raise HarnessError(
            f"final {stable_s:.0f}s not covered: polls at {[p.t for p in window]}, cutoff {cutoff}"
        )
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


# --- scenario driver -------------------------------------------------------------

BRING_UP_S = 60.0
WINDOW_S = 120.0
POLL_S = 1.0
READY_STREAK = 2


@dataclass(frozen=True)
class UploadScenario:
    id: str
    cfg: QueueConfig
    n_seeders: int
    n_downloads: int
    leecher: bool


def _limits(dl: int, ul: int, tot: int, *, slow: bool) -> QueueConfig:
    return QueueConfig(True, dl, ul, tot, slow, False)


UPLOAD_SCENARIOS: tuple[UploadScenario, ...] = (
    # Control: the same seeders with no leecher, so idle (F1 inside this harness).
    UploadScenario("U0-off", _limits(-1, -1, 3, slow=False), 3, 1, leecher=False),
    UploadScenario("U1-off", _limits(-1, -1, 3, slow=False), 3, 1, leecher=True),
    UploadScenario("U1-on", _limits(-1, -1, 3, slow=True), 3, 1, leecher=True),
    # _WEDGED's real limits, uploading: decides the _WEDGED pin.
    UploadScenario("W-off", _limits(3, 3, 5, slow=False), 5, 3, leecher=True),
)


@dataclass
class Recording:
    """Raw polls of one run. Bring-up t counts from the first bring-up poll's
    start; window t counts from the downloads being added (the event)."""

    bringup: list[Poll] = field(default_factory=list)
    window: list[Poll] = field(default_factory=list)
    error: str | None = None


def _seeder_torrents(sc: UploadScenario, rep: int) -> list[HarnessTorrent]:
    return [
        make_torrent(f"{sc.id}-r{rep}-c{i}", size=UPLOAD_SIZE, piece_length=UPLOAD_PIECE_LENGTH)
        for i in range(sc.n_seeders)
    ]


def _download_torrents(sc: UploadScenario, rep: int) -> list[HarnessTorrent]:
    return [make_torrent(f"{sc.id}-r{rep}-i{i}") for i in range(sc.n_downloads)]


def _poll(h: QbtHarness, t0: float) -> Poll:
    rows = tuple(h.rows())
    return Poll(round(time.monotonic() - t0, 2), rows)


def _sleep_until(t: float) -> None:
    time.sleep(max(0.0, t - time.monotonic()))


def _bring_up(pair: Pair, sc: UploadScenario, seeds: list[HarnessTorrent], rec: Recording) -> None:
    """Poll each second until readiness holds twice running; raise HarnessError after 60 s."""
    is_ready = ready if sc.leecher else idle_ready
    hashes = set(_hashes(seeds))
    t0, streak = time.monotonic(), 0
    for k in range(int(BRING_UP_S / POLL_S) + 1):
        _sleep_until(t0 + k * POLL_S)
        poll = _poll(pair.seeder, t0)
        rec.bringup.append(poll)
        streak = streak + 1 if is_ready(poll.rows, hashes) else 0
        if streak == READY_STREAK:
            return
        if sc.leecher:
            reconnect(pair, seeds)
    raise HarnessError(f"{sc.id}: seeders not ready within {BRING_UP_S:.0f}s")


def _watch(pair: Pair, downloads: list[HarnessTorrent], rec: Recording) -> None:
    """Add the downloads to the seeder only, then poll it each second for 120 s."""
    t0 = time.monotonic()
    for t in downloads:
        pair.seeder.add(t, complete=False)
    for k in range(int(WINDOW_S / POLL_S) + 1):
        _sleep_until(t0 + k * POLL_S)
        rec.window.append(_poll(pair.seeder, t0))


def _setup(pair: Pair, sc: UploadScenario, seeds: list[HarnessTorrent]) -> None:
    pair.seeder.reset()
    pair.leecher.reset()
    apply_prefs(pair, sc.cfg)
    add_seeders(pair.seeder, seeds)
    if sc.leecher:
        attach_leecher(pair, seeds)


def _guarded(rec: Recording, step: Callable[[], None]) -> bool:
    """Run `step`; record any failure in `rec.error` instead of raising."""
    try:
        step()
    except Exception as exc:  # the recording must survive whatever happens
        rec.error = f"{type(exc).__name__}: {exc}"
        return False
    return True


def observe(pair: Pair, sc: UploadScenario, *, rep: int) -> Recording:
    """Drive one run and keep its raw polls. Never judges and never raises."""
    seeds, downloads = _seeder_torrents(sc, rep), _download_torrents(sc, rep)
    rec = Recording()
    steps = (
        lambda: _setup(pair, sc, seeds),
        lambda: _bring_up(pair, sc, seeds, rec),
        lambda: _watch(pair, downloads, rec),
    )
    for step in steps:
        if not _guarded(rec, step):
            break  # the error is recorded; the polls so far are kept
    return rec


def _by_kind(sc: UploadScenario, rec: Recording, kind: str, want: int) -> set[str]:
    """Hashes of this scenario's torrents of one kind ('c' complete, 'i' incomplete), by name."""
    pattern = re.compile(rf"{re.escape(sc.id)}-r\d+-{kind}\d+")
    found = {r.hash for p in rec.bringup + rec.window for r in p.rows if pattern.fullmatch(r.name)}
    if len(found) != want:
        label = "seeders" if kind == "c" else "downloads"
        raise HarnessError(f"{sc.id}: saw {len(found)} {label}, expected {want}")
    return found


def judge(sc: UploadScenario, rec: Recording) -> Outcome:
    """The run's Outcome; HarnessError for a recorded error or a seeder that stopped uploading."""
    if rec.error is not None:
        raise HarnessError(rec.error)
    if not rec.bringup:
        raise HarnessError(f"{sc.id}: no bring-up polls")
    seeders = _by_kind(sc, rec, "c", sc.n_seeders)
    downloads = _by_kind(sc, rec, "i", sc.n_downloads)
    at_event = rec.bringup[-1].rows
    before = before_counts(at_event, seeders)
    if sc.leecher:
        check_uploading(rec.window, seeders)
    after = settled(rec.window, seeders, downloads)
    tot = sc.cfg.max_active_torrents
    return Outcome(
        before,
        after,
        active_seeders(at_event, seeders),
        verdict(before, after, tot=tot, n_downloads=sc.n_downloads),
    )
