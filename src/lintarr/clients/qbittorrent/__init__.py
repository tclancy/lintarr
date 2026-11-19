"""The qBittorrent backend."""

import httpx

from lintarr.clients.base import Backend
from lintarr.clients.qbittorrent import queue_liveness
from lintarr.clients.qbittorrent.collect import AUTH_PATH, collect_qbt
from lintarr.clients.qbittorrent.therefore import THEREFORE
from lintarr.collect.http import ReadOnlyClient
from lintarr.config import ClientConfig
from lintarr.models import ClientFacts, ClientInstance


def collect(cfg: ClientConfig, *, transport: httpx.BaseTransport | None) -> ClientInstance:
    """Open a read-only session on *cfg* and collect it as a client instance."""
    with ReadOnlyClient(cfg.url, transport=transport, auth_path=AUTH_PATH) as client:
        qbt = collect_qbt(client, cfg)
    return ClientInstance(cfg.name, "qbittorrent", qbt.version, ClientFacts(), qbt)


BACKEND = Backend(
    kind="qbittorrent",
    credentials=frozenset({"user", "pass"}),
    collect=collect,
    checks=(lambda inst, arrs: queue_liveness.check(inst.specific, arrs),),
    therefore=THEREFORE,
)
