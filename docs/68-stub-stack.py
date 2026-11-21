"""A read-only stub arr stack, for ``docs/68-path-mapping-edges.md``.

One qBittorrent and one Sonarr on a single port, serving the bodies
``tests/fixtures/homelab_paths.py`` measured live on 2026-11-19, with the
remote path mapping and the client's save path taken from argv so one scenario
is served per run::

    python3 docs/68-stub-stack.py <port> <remotePath> <localPath> <savePath> <rootFolder> [mount...]

``<savePath>`` is qBittorrent's default save path and the stack's download
client carries no category, so the save path reaches the check as the client
reports it -- which is how a Windows qBittorrent's ``c:\\downloads\\tv`` gets
in. ``<rootFolder>`` is the arr's only root folder and ``[mount...]`` overrides
its ``diskspace`` list.

Here so the walkthrough's commands are one line each and can be re-run. It is
demo scaffolding and nothing in ``src/`` or ``tests/`` imports it.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
REMOTE_PATH = sys.argv[2]
LOCAL_PATH = sys.argv[3]
SAVE_PATH = sys.argv[4]
ROOT_FOLDER = sys.argv[5]
MOUNTS = sys.argv[6:] or ["/", "/config", "/data"]

PREFS = {
    "queueing_enabled": True,
    "max_active_downloads": 6,
    "max_active_uploads": 3,
    "max_active_torrents": 5,
    "dont_count_slow_torrents": False,
    "max_ratio_enabled": True,
    "max_ratio": 1.5,
    "max_ratio_act": 3,
    "max_seeding_time_enabled": True,
    "max_seeding_time": 20160,
    "max_inactive_seeding_time_enabled": False,
    "save_path": SAVE_PATH,
    "auto_tmm_enabled": True,
    "use_category_paths_in_manual_mode": False,
}

DISK = {"label": "", "freeSpace": 350839271424, "totalSpace": 477303013376}

BODIES = {
    "/api/v2/app/version": "v5.2.3",
    "/api/v2/app/preferences": PREFS,
    "/api/v2/torrents/categories": {},
    "/api/v3/system/status": {"version": "4.0.20.3014", "appName": "Sonarr"},
    "/api/v3/indexer": [],
    "/api/v3/config/mediamanagement": {"id": 1, "copyUsingHardlinks": True},
    "/api/v3/rootfolder": [{"id": 1, "path": ROOT_FOLDER, "accessible": True}],
    "/api/v3/diskspace": [{"path": mount, **DISK} for mount in MOUNTS],
    "/api/v3/remotepathmapping": [
        {"id": 1, "host": "127.0.0.1", "remotePath": REMOTE_PATH, "localPath": LOCAL_PATH}
    ],
    "/api/v3/downloadclient": [
        {
            "id": 1,
            "name": "qBittorrent",
            "implementation": "QBittorrent",
            "enable": True,
            "removeCompletedDownloads": True,
            "fields": [
                {"name": "host", "value": "127.0.0.1"},
                {"name": "port", "value": PORT},
                {"name": "useSsl", "value": False},
                {"name": "tvCategory", "value": ""},
            ],
        }
    ],
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, content_type):
        payload = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path == "/api/v2/auth/login":
            self._send(200, "Ok.", "text/plain")
        else:
            self._send(404, "", "text/plain")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path not in BODIES:
            self._send(404, "", "text/plain")
            return
        body = BODIES[path]
        if isinstance(body, str):
            self._send(200, body, "text/plain")
        else:
            self._send(200, json.dumps(body), "application/json")


server = HTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
print(f"stub stack listening on 127.0.0.1:{PORT}", flush=True)
threading.Event().wait()
