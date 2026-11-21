"""Give a qBittorrent container categories that already carry a share-limit action.

qBittorrent 5.2.3's WebAPI cannot set one. `torrents/createCategory` and
`torrents/editCategory` read only `category`, `savePath`, `downloadPathEnabled`
and `downloadPath` (`src/webui/api/torrentscontroller.cpp:1876-1925`, release
5.2.3), while `torrents/categories` reports `share_limit_action` — the field
`orphan-inevitability` reads. The one writable route is the client's own store:
it saves categories to `<config>/qBittorrent/categories.json` and parses them
with `CategoryOptions::fromJSON`, whose keys are those below
(`src/base/bittorrent/categoryoptions.cpp`, release 5.2.3). So the harness
writes that file before the client starts, which is also why the container needs
its own `/config` mount.

Measured for real: docs/measurements/2026-11-21-qbt-share-limit-actions.md.
"""

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from tests.conformance import containers
from tests.conformance.qbt import QbtHarness

#: The value qBittorrent writes for "inherit from the level above".
INHERIT = -2

#: Where the client keeps its categories, under the /config mount.
CATEGORIES_FILE = Path("qBittorrent") / "categories.json"


def category_body(action: str, save_path: str = "/downloads") -> dict[str, object]:
    """One category, inheriting every limit, with `action` as its share-limit action.

    The limits inherit so that the global ratio limit is what fires: this file
    is about which *action* applies, not which limit is reached.
    """
    return {
        "save_path": save_path,
        "download_path": None,
        "ratio_limit": INHERIT,
        "seeding_time_limit": INHERIT,
        "inactive_seeding_time_limit": INHERIT,
        "share_limit_action": action,
    }


def write_categories(config: Path, actions: Mapping[str, str]) -> Path:
    """Write `{category: share-limit action}` where the client will read it."""
    path = config / CATEGORIES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    bodies = {name: category_body(action) for name, action in actions.items()}
    path.write_text(json.dumps(bodies, indent=4))
    return path


@contextmanager
def qbt_with_categories(tmp: Path, actions: Mapping[str, str]) -> Iterator[QbtHarness]:
    """A client whose categories exist, with their actions, before it starts."""
    downloads = tmp / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    config = tmp / "config"
    write_categories(config, actions)
    containers.sweep_expired()
    name, harness = containers.start_qbt(downloads, config=config)
    try:
        yield harness
    finally:
        containers.stop_qbt(name, harness)
