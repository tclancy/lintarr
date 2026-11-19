"""qBittorrent's operator-facing "Therefore" lines, one per conflict it can FAIL on.

Moved verbatim from ``cli.py``, where it was ``_THEREFORE``: a table there would
make every new backend edit the CLI. ``cli`` looks a finding's line up through
its backend's ``therefore``.
"""

from lintarr.clients.qbittorrent import queue_liveness

# Keyed on (invariant, conflict), never on the invariant alone. One invariant
# answers one operator question, but it can reach that answer through
# structurally different conflicts with different remedies — and a "Therefore"
# line that names the wrong one tells an operator to change a setting the code
# itself has just established will not help.
THEREFORE: dict[tuple[str, str], str] = {
    (queue_liveness.INVARIANT_ID, queue_liveness.SEEDING): (
        "completed torrents hold every active slot and no queued\n"
        "  download can start. Nothing releases a seeder: both global share\n"
        "  limits are off and no category sets its own."
    ),
    # "at or below zero" was wrong: the predicate is `_binds(v) and v <= 0`,
    # and -1 does not bind, so -1 never starves the queue. An operator reading
    # `max_active_downloads = -1` alongside that sentence goes looking at the
    # one limit the check has already cleared.
    (queue_liveness.INVARIANT_ID, queue_liveness.STARVATION): (
        "this client cannot start a first download even while\n"
        "  nothing is running: max_active_downloads or max_active_torrents is zero,\n"
        "  or negative but not -1 (the only value meaning unlimited). Share limits\n"
        "  are not involved, so turning them on will not help."
    ),
    # The three override routes. Each one's remedy is on the *arr* side, which
    # is what separates them from SEEDING: the sentence above sends an operator
    # to qBittorrent's share limits, and here those are settings the torrent has
    # already put out of reach. Measured in
    # docs/measurements/2026-10-05-sonarr-seed-ratio-range.md.
    (queue_liveness.INVARIANT_ID, queue_liveness.OVERRIDE_BOTH): (
        "an indexer sets both seed criteria to a negative value other\n"
        "  than -2, which qBittorrent reads as 'no limit' and never redirects to\n"
        "  your global or category setting. Seeders hold every active slot and no\n"
        "  queued download can start. A global ratio or seeding-time limit will\n"
        "  NOT help — the fix is that indexer's seed ratio and seed time in\n"
        "  Sonarr/Radarr, or a global inactive-seeding-time limit, which is the\n"
        "  one release gate no indexer can override and which this FAIL has\n"
        "  already checked you have unset. A category may override that gate\n"
        "  per-category, which lintarr does not yet read."
    ),
    (queue_liveness.INVARIANT_ID, queue_liveness.ACTION): (
        "your share-limit action is set to something that does not free\n"
        "  the slot. qBittorrent consults the action only AFTER a limit is\n"
        "  reached, so this defeats the ratio, seeding-time and\n"
        "  inactive-seeding-time limits at once and every limit you have set is\n"
        "  decorative. Super seeding keeps the torrent running; so does any\n"
        "  value matching no action branch. Set qBittorrent's share-limit\n"
        "  action to Stop, Remove, or Remove-with-content."
    ),
    (queue_liveness.INVARIANT_ID, queue_liveness.OVERRIDE_RATIO): (
        "an indexer sets a negative seed ratio other than -2, so\n"
        "  qBittorrent never enforces a ratio for its torrents and never consults\n"
        "  your global ratio limit. The seeding-time limit is the only thing left\n"
        "  that could free the slot, and it is off everywhere. Either give that\n"
        "  indexer a seed ratio of 0 or more, or turn on a global seeding-time\n"
        "  limit."
    ),
    (queue_liveness.INVARIANT_ID, queue_liveness.OVERRIDE_SEED_TIME): (
        "an indexer sets a negative seed time other than -2, so\n"
        "  qBittorrent never enforces a seeding time for its torrents and never\n"
        "  consults your global one. The ratio limit is the only thing left that\n"
        "  could free the slot, and it is off everywhere. Either give that indexer\n"
        "  a seed time of 0 or more, or turn on a global ratio limit."
    ),
}
