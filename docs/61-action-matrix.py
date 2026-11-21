"""The ``ACTION`` truth table, for ``docs/61-category-share-limit-action.md``.

The global ``max_ratio_act`` down the side, category ``tv``'s own
``share_limit_action`` across the top. Calls ``queue_liveness.check`` directly
on preferences every *other* route leaves at PASS — both global share limits
on and reachable, the inactive gate off, one indexer with real seed goals, and
``max_active_torrents`` bound so seeders can fill it — so each cell is the
``ACTION`` route's own verdict and nothing else's.

Builds its ``QbtInstance`` from ``lintarr.models`` rather than from a test
fixture, so the same file runs unchanged against an older checkout. That is
the point: the walkthrough runs it on ``origin/main`` and on this branch and
diffs the two tables.
"""

from datetime import UTC, datetime

from lintarr.clients.qbittorrent.queue_liveness import check
from lintarr.facts import Known
from lintarr.models import ArrInstance, IndexerFacts, QbtInstance

GLOBALS = [(0, "Stop"), (1, "Remove"), (3, "RemoveWithContent"), (2, "EnableSuperSeeding")]
CATEGORY = ["Default", "Stop", "Remove", "RemoveWithContent", "EnableSuperSeeding"]


def k(value):
    return Known(value=value, source="demo", read_at=datetime.now(UTC), service_version="v5.2.3")


def qbt(act, category_action):
    return QbtInstance(
        name="main",
        version="v5.2.3",
        queueing_enabled=k(True),
        max_active_downloads=k(6),
        max_active_uploads=k(3),
        # Binds, and slow torrents are not exempt: seeders can fill it.
        max_active_torrents=k(5),
        dont_count_slow_torrents=k(False),
        # Both global limits on and reachable, and the inactive gate off.
        max_ratio_enabled=k(True),
        max_ratio=k(1.5),
        max_ratio_act=k(act),
        max_seeding_time_enabled=k(True),
        max_seeding_time=k(20160),
        max_inactive_seeding_time_enabled=k(False),
        categories=k({"tv": {"share_limit_action": category_action}}),
        save_path=k("/data/torrents"),
        auto_tmm_enabled=k(True),
        use_category_paths_in_manual_mode=k(False),
    )


INDEXER = IndexerFacts(
    name="1337x",
    protocol=k("torrent"),
    enable_rss=k(True),
    enable_automatic_search=k(True),
    enable_interactive_search=k(False),
    seed_ratio=k(2.0),
    seed_time=k(2880),
    season_pack_seed_time=k(2880),
)
ARRS = (
    ArrInstance(
        name="main",
        kind="sonarr",
        version="4.0.20.3014",
        indexers=(INDEXER,),
        copy_using_hardlinks=k(True),
        root_folders=k(()),
        mounts=k(()),
        remote_path_mappings=k(()),
        download_clients=k(()),
    ),
)

head = f"{'global action':<24}" + "".join(f"{c:>20}" for c in CATEGORY)
print(head)
print("-" * len(head))
for act, label in GLOBALS:
    cells = (check(qbt(act, c), ARRS).outcome.value for c in CATEGORY)
    print(f"{f'{label} ({act})':<24}" + "".join(f"{v:>20}" for v in cells))
