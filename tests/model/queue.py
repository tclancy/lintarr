"""A small executable model of qBittorrent's download queue.

Deliberately independent of src/ — it is a second opinion on the same
behaviour, not a reuse of the implementation. If it imported the predicate it
would agree with it by construction and prove nothing.

Scope note: this models the ONE property queue-liveness reasons about — whether
a queued torrent can ever start. It is not a general qBittorrent simulator.
"""

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
