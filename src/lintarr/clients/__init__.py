"""Download-client backends, registered by kind.

A plain dict on purpose: in-tree backends only, added by one line here, and a
test can register a fake for its duration by patching it.
"""

from lintarr.clients import qbittorrent
from lintarr.clients.base import Backend

BACKENDS: dict[str, Backend] = {qbittorrent.BACKEND.kind: qbittorrent.BACKEND}
