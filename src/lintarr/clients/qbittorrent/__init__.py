"""The qBittorrent backend."""

import httpx

from lintarr.clients.base import Backend
from lintarr.clients.qbittorrent import orphan_inevitability, queue_liveness
from lintarr.clients.qbittorrent.collect import AUTH_PATH, collect_qbt
from lintarr.clients.qbittorrent.save_paths import save_paths
from lintarr.clients.qbittorrent.therefore import THEREFORE
from lintarr.collect.http import ReadOnlyClient
from lintarr.config import ClientConfig
from lintarr.models import ClientFacts, ClientInstance


def collect(cfg: ClientConfig, *, transport: httpx.BaseTransport | None) -> ClientInstance:
    """Open a read-only session on *cfg* and collect it as a client instance."""
    with ReadOnlyClient(cfg.url, transport=transport, auth_path=AUTH_PATH) as client:
        qbt = collect_qbt(client, cfg)
    return ClientInstance(cfg.name, "qbittorrent", qbt.version, ClientFacts(save_paths(qbt)), qbt)


BACKEND = Backend(
    kind="qbittorrent",
    credentials=frozenset({"user", "pass"}),
    collect=collect,
    checks=(
        lambda inst, arrs: queue_liveness.check(inst.specific, arrs),
        orphan_inevitability.check,
    ),
    therefore=THEREFORE,
    arr_dependent={
        orphan_inevitability.INVARIANT_ID: orphan_inevitability.PREMISE_NOTHING_RECLAIMS
    },
)
