"""Raw arr API bodies measured live on 2026-11-19, and builders over them.

Sonarr 4.0.20.3014 and Radarr 6.4.4.10685. Real payloads carry many more keys
than these; the parsers must ignore the ones they do not read, so the bodies
here keep only what the spec's Inputs table names plus a little noise.
Kept apart from ``homelab.py``, which holds homelab#393 incident evidence and
must never change.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from lintarr.facts import Known, Unknown
from lintarr.models import (
    ArrDownloadClient,
    ArrInstance,
    QbtInstance,
    RootFolder,
)
from tests.fixtures.homelab import qbt_with

_VERSION = "4.0.20.3014"
_SOURCE = "GET /api/v3 (measured 2026-11-19)"

MEDIA_MANAGEMENT = {
    "id": 1,
    "copyUsingHardlinks": True,
    "autoUnmonitorPreviouslyDownloadedEpisodes": False,
}

ROOT_FOLDERS = {
    "sonarr": [{"id": 1, "path": "/data/media/tv", "freeSpace": 2174209114112, "accessible": True}],
    "radarr": [
        {"id": 1, "path": "/data/media/movies", "freeSpace": 2174209114112, "accessible": True}
    ],
}

_ROOT_DISK = {"label": "", "freeSpace": 350839271424, "totalSpace": 477303013376}
DISK_SPACE = [
    {"path": "/", **_ROOT_DISK},
    {"path": "/config", **_ROOT_DISK},
    {"path": "/data", "label": "", "freeSpace": 2174209114112, "totalSpace": 3936809893888},
]

REMOTE_PATH_MAPPINGS: list[dict[str, Any]] = []


def download_clients(kind: str = "sonarr") -> list[dict[str, Any]]:
    category = ("tvCategory", "tv") if kind == "sonarr" else ("movieCategory", "movies")
    return [
        {
            "id": 1,
            "name": "qBittorrent",
            "implementation": "QBittorrent",
            "enable": True,
            "priority": 1,
            "fields": [
                {"name": "host", "value": "gluetun"},
                {"name": "port", "value": 8080},
                {"name": "useSsl", "value": False},
                {"name": "urlBase"},
                {"name": category[0], "value": category[1]},
            ],
        }
    ]


def arr_bodies(kind: str = "sonarr") -> dict[str, Any]:
    """Every new endpoint's raw body, keyed by path."""
    return {
        "/api/v3/config/mediamanagement": MEDIA_MANAGEMENT,
        "/api/v3/rootfolder": ROOT_FOLDERS[kind],
        "/api/v3/diskspace": DISK_SPACE,
        "/api/v3/remotepathmapping": REMOTE_PATH_MAPPINGS,
        "/api/v3/downloadclient": download_clients(kind),
    }


FACT_FIELDS = frozenset(
    {"copy_using_hardlinks", "root_folders", "mounts", "remote_path_mappings", "download_clients"}
)


def _known(value: Any) -> Known[Any]:
    return Known(value=value, source=_SOURCE, read_at=datetime.now(UTC), service_version=_VERSION)


def _wrap(value: Any) -> Any:
    """Wrap a raw value as Known; pass an already-built Fact through unchanged."""
    return value if isinstance(value, (Known, Unknown)) else _known(value)


def homelab_arr(kind: str = "sonarr", name: str = "tv") -> ArrInstance:
    """Tom's arr as measured: hardlinks on, everything under ``/data``."""
    folder = ROOT_FOLDERS[kind][0]["path"]
    category = "tv" if kind == "sonarr" else "movies"
    return ArrInstance(
        name=name,
        kind=kind,
        version=_VERSION,
        indexers=(),
        copy_using_hardlinks=_known(True),
        root_folders=_known((RootFolder(path=folder, accessible=True),)),
        mounts=_known(("/", "/config", "/data")),
        remote_path_mappings=_known(()),
        download_clients=_known(
            (
                ArrDownloadClient(
                    name="qBittorrent",
                    implementation="QBittorrent",
                    enabled=True,
                    host="gluetun",
                    category=category,
                ),
            )
        ),
    )


def arr_with(base: ArrInstance | None = None, **overrides: Any) -> ArrInstance:
    """*base* (default: the homelab sonarr) with individual fields overridden.

    The five path facts take raw values (wrapped ``Known``) or a ready ``Fact``;
    identity fields and ``indexers`` are passed through as they are.
    """
    wrapped = {k: _wrap(v) if k in FACT_FIELDS else v for k, v in overrides.items()}
    return replace(base or homelab_arr(), **wrapped)


#: qBittorrent 5.2.3, measured 2026-11-19: ``/api/v2/torrents/categories``.
QBT_CATEGORIES = {
    "movies": {"name": "movies", "savePath": "/data/torrents/movies"},
    "tv": {"name": "tv", "savePath": "/data/torrents/tv"},
}


def homelab_qbt(name: str = "main") -> QbtInstance:
    """Tom's qBittorrent as measured 2026-11-19: auto TMM on, categories under ``/data``."""
    return qbt_with(
        name,
        save_path="/data/torrents",
        auto_tmm_enabled=True,
        use_category_paths_in_manual_mode=False,
        categories=QBT_CATEGORIES,
    )
