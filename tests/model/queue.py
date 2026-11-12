"""A small executable model of qBittorrent's download queue.

Deliberately independent of src/ — it is a second opinion on the same
behaviour, not a reuse of the implementation. If it imported the predicate it
would agree with it by construction and prove nothing.

Scope note: this models the ONE property queue-liveness reasons about — whether
a queued torrent can ever start. It is not a general qBittorrent simulator.
"""

from collections.abc import Sequence
from dataclasses import dataclass

UNLIMITED = -1


@dataclass(frozen=True, slots=True)
class QueueConfig:
    queueing_enabled: bool
    max_active_downloads: int
    max_active_uploads: int
    max_active_torrents: int
    dont_count_slow_torrents: bool
    share_limit_enabled: bool


def _binds(limit: int) -> bool:
    """A limit constrains the queue unless it is the unlimited sentinel.

    `-1` is the ONLY unlimited sentinel recognized here — a value like `-2` is
    still treated as binding (and, since a count can never be negative, binds
    immediately). Task 5's predicate must copy this exact convention rather
    than infer one (e.g. `limit < 0`), or the two will disagree on any
    negative-but-not-`-1` value.
    """
    return limit != UNLIMITED


def simulate(cfg: QueueConfig, n_torrents: int) -> bool:
    """Step the queue to a fixpoint. True if it ends wedged.

    Wedged means: at least one torrent is still queued, and no further download
    can ever start no matter how long you wait.

    Note: `cfg.max_active_uploads` is deliberately never consulted here. The
    question this model answers is "can a queued DOWNLOAD start" — the upload
    limit governs seeding slots and does not block a download from starting.
    It stays on the dataclass because the queue picture includes it and a
    caller may want to vary it, but it plays no role in this computation.
    """
    if not cfg.queueing_enabled:
        return False

    if n_torrents <= 0:
        return False

    downloading = 0
    seeding = 0
    queued = n_torrents

    def slots_free() -> bool:
        active = downloading + seeding
        if _binds(cfg.max_active_torrents) and active >= cfg.max_active_torrents:
            return False
        if _binds(cfg.max_active_downloads) and downloading >= cfg.max_active_downloads:
            return False
        return True

    # If no download can ever start at all (e.g. max_active_downloads == 0,
    # or max_active_torrents == 0), the queue is wedged immediately regardless
    # of any other rule — including the slow-torrent exemption below. That
    # exemption only stops counting a torrent once it is already downloading;
    # it cannot rescue a torrent that never gets a slot to begin with.
    if not slots_free():
        return True

    # A torrent exempted by the slow-torrent rule stops consuming a slot once
    # it is downloading, so — given that at least one torrent CAN start, per
    # the check above — the queue always drains eventually. That is a coarse
    # simplification and it is the intended one.
    if cfg.dont_count_slow_torrents:
        return False

    while True:
        started = 0
        while queued > 0 and slots_free():
            queued -= 1
            downloading += 1
            started += 1

        if queued == 0:
            return False

        if started == 0:
            # Slots were full at the top of this round and nothing is in
            # flight to ever complete and free one. Nothing will ever change:
            # wedged.
            return True

        # Every in-flight download completes and becomes a seeder, unless
        # share limits are on, in which case seeders leave once they hit the
        # limit, releasing their slot again.
        if not cfg.share_limit_enabled:
            seeding += downloading
        downloading = 0


# --- Step-wise trace -------------------------------------------------------
#
# `trace()` encodes the beliefs the shipped predicate already holds
# (src/lintarr/invariants/queue_liveness.py), NOT observations of a real
# qBittorrent. Citations below are comments only: this package must not import
# from src/. Where a rule comes from simulate() instead, the comment says so.


@dataclass(frozen=True, slots=True)
class Counts:
    downloading: int
    queued: int
    seeding: int
    seeding_queued: int
    left: int


@dataclass(frozen=True, slots=True)
class AddComplete:
    n: int


@dataclass(frozen=True, slots=True)
class AddIncomplete:
    n: int


@dataclass(frozen=True, slots=True)
class RemoveSeeder:
    pass


@dataclass(frozen=True, slots=True)
class FinishDownloads:
    """Model-only: every downloading torrent finishes. The container cannot do
    this; only the R3 comparison against simulate() uses it."""


Event = AddComplete | AddIncomplete | RemoveSeeder | FinishDownloads


class ModelUndefined(Exception):
    """Raised where the predicate states no belief."""


@dataclass(slots=True)
class _State:
    downloading: int = 0
    queued: int = 0
    seeding: int = 0
    left: int = 0

    def counts(self) -> Counts:
        # Rule 2: seeding_queued is always 0. A CONSEQUENCE of rule 1, not a
        # stated belief: seeders absorb every slot, so none sit queued. The
        # predicate flags this as unvalidated (queue_liveness.py:80-85) and its
        # verdict is invariant in max_active_uploads (pinned by the `ul` axis
        # of tests/invariants/test_queue_liveness_sweep.py).
        return Counts(self.downloading, self.queued, self.seeding, 0, self.left)


def _slot_free(cfg: QueueConfig, st: _State) -> bool:
    # Rule 1: every seeder occupies a max_active_torrents slot
    # (queue_liveness.py:21-23, :497-501).
    # Rule 3: with dont_count_slow_torrents, slow torrents (every harness
    # torrent, having no peers) are not counted against max_active_torrents,
    # seeders included (queue_liveness.py:1028, :1042). Exempting slow
    # DOWNLOADS from max_active_downloads too comes from the SIMULATOR
    # (tests/model/queue.py:76-81), not the predicate. Counting zero makes a
    # limit binding at <= 0 still block a first start (queue_liveness.py:17-20).
    counted_total = 0 if cfg.dont_count_slow_torrents else st.downloading + st.seeding
    counted_dl = 0 if cfg.dont_count_slow_torrents else st.downloading
    if _binds(cfg.max_active_torrents) and counted_total >= cfg.max_active_torrents:
        return False
    return not (_binds(cfg.max_active_downloads) and counted_dl >= cfg.max_active_downloads)


def _start_queued(cfg: QueueConfig, st: _State) -> None:
    # Rule 6: queued torrents start FIFO whenever a slot is free. Source: the
    # simulator (`slots_free` above); the predicate is order-agnostic. Counts
    # do not distinguish torrents, so order is implicit.
    while st.queued > 0 and _slot_free(cfg, st):
        st.queued -= 1
        st.downloading += 1


def _apply_add_incomplete(cfg: QueueConfig, st: _State, n: int) -> None:
    if not cfg.queueing_enabled:
        # Rule 5: nothing queues; every incomplete torrent is downloading
        # (queueing premise, queue_liveness.py:1374). Applied before rule 7.
        st.downloading += n
        return
    st.queued += n
    _start_queued(cfg, st)


def _check_complete_fits(cfg: QueueConfig, st: _State, n: int) -> None:
    # Rule 7: the predicate states no belief about seeders that do not fit.
    if not _binds(cfg.max_active_torrents):
        return
    overfull = (
        not cfg.dont_count_slow_torrents
        and st.downloading + st.seeding + n > cfg.max_active_torrents
    )
    if cfg.max_active_torrents <= 0 or overfull:
        raise ModelUndefined(f"AddComplete({n}) does not fit max_active_torrents")


def _apply_add_complete(cfg: QueueConfig, st: _State, n: int) -> None:
    if cfg.share_limit_enabled:
        # Rule 4: a completing torrent leaves at once, freeing its slot, but
        # only for a releasing action (_RELEASING_ACTIONS, queue_liveness.py:438;
        # _action_conflict, :1188-1218). QueueConfig has no action field, so
        # the harness's prefs_for must set a releasing action.
        st.left += n
        return
    if cfg.queueing_enabled:
        _check_complete_fits(cfg, st, n)
    st.seeding += n


def _apply_remove_seeder(cfg: QueueConfig, st: _State) -> None:
    if st.seeding == 0:
        raise ModelUndefined("RemoveSeeder with no seeders")  # rule 8
    st.seeding -= 1
    _start_queued(cfg, st)


def _apply_finish_downloads(cfg: QueueConfig, st: _State) -> None:
    # Same share-limit story as simulate() (queue.py:99-104).
    if cfg.share_limit_enabled:
        st.left += st.downloading
    else:
        st.seeding += st.downloading
    st.downloading = 0
    _start_queued(cfg, st)


def _apply(cfg: QueueConfig, st: _State, event: Event) -> None:
    match event:
        case AddIncomplete(n):
            _apply_add_incomplete(cfg, st, n)
        case AddComplete(n):
            _apply_add_complete(cfg, st, n)
        case RemoveSeeder():
            _apply_remove_seeder(cfg, st)
        case FinishDownloads():
            _apply_finish_downloads(cfg, st)


def trace(cfg: QueueConfig, events: Sequence[Event]) -> list[Counts]:
    """One Counts per event: the steady state after it."""
    st = _State()
    out: list[Counts] = []
    for event in events:
        _apply(cfg, st, event)
        out.append(st.counts())
    return out
