"""A read-only stub arr stack, for ``docs/65-bad-category-names-its-field.md``.

One qBittorrent and one arr on a single port, serving the bodies
``tests/fixtures/homelab_paths.py`` measured live on 2026-11-19. The arr's
flavour and the download client's category field come from argv, so one
scenario is served per run::

    python3 docs/65-stub-stack.py <port> <sonarr|radarr> <category field> <json value>

A Radarr carries ``movieCategory`` where a Sonarr carries ``tvCategory``, and
the value is JSON so a scenario can serve the junk (``[]``) that the
download-client reader rejects.

Here so the walkthrough's commands are one line each and can be re-run. It is
demo scaffolding and nothing in ``src/`` or ``tests/`` imports it.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
ARR_KIND = sys.argv[2]
CATEGORY_FIELD = sys.argv[3]
CATEGORY_VALUE = json.loads(sys.argv[4])

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
    "save_path": "/data/torrents",
    "auto_tmm_enabled": True,
    "use_category_paths_in_manual_mode": False,
}

CATEGORIES = {
    name: {
        "download_path": None,
        "inactive_seeding_time_limit": -2,
        "name": name,
        "ratio_limit": -2,
        "savePath": f"/data/torrents/{name}",
        "seeding_time_limit": -2,
        "share_limit_action": "Default",
    }
    for name in ("movies", "tv")
}

DISK = {"label": "", "freeSpace": 350839271424, "totalSpace": 477303013376}
MEDIA_ROOT = "/data/media/movies" if ARR_KIND == "radarr" else "/data/media/tv"
BODIES = {
    "/api/v2/app/version": "v5.2.3",
    "/api/v2/app/preferences": PREFS,
    "/api/v2/torrents/categories": CATEGORIES,
    "/api/v3/system/status": {"version": "4.0.20.3014", "appName": ARR_KIND.title()},
    "/api/v3/indexer": [
        {
            "id": 1,
            "name": "1337x",
            "protocol": "torrent",
            "implementation": "Torznab",
            "enableRss": True,
            "enableAutomaticSearch": True,
            "enableInteractiveSearch": False,
            "fields": [{"name": "seedCriteria.seedRatio", "value": 2.0}],
        }
    ],
    "/api/v3/config/mediamanagement": {"id": 1, "copyUsingHardlinks": True},
    "/api/v3/rootfolder": [{"id": 1, "path": MEDIA_ROOT, "accessible": True}],
    "/api/v3/diskspace": [
        {"path": "/", **DISK},
        {"path": "/config", **DISK},
        {"path": "/data", "label": "", "freeSpace": 2174209114112, "totalSpace": 3936809893888},
    ],
    "/api/v3/remotepathmapping": [],
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
                {"name": CATEGORY_FIELD, "value": CATEGORY_VALUE},
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
