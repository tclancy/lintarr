"""Thin wrapper over the qBittorrent Web API v2, for the conformance harness only.

These POSTs live under tests/ by design: src/ is read-only against the stack.
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from tests.conformance.torrents import HarnessTorrent

USERNAME = "admin"


class HarnessError(Exception):
    """The harness could not set up or drive the container."""


@dataclass(frozen=True)
class Row:
    """The fields of one torrents/info row the harness reads."""

    hash: str
    name: str
    state: str
    upspeed: int
    dlspeed: int
    num_leechs: int


class QbtHarness:
    def __init__(
        self,
        base_url: str,
        password: str,
        downloads: Path,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=10, transport=transport)
        self._password = password
        self._downloads = downloads

    def close(self) -> None:
        self._http.close()

    def login(self) -> None:
        """Log in exactly once. Failed logins feed qBittorrent's 5-failure IP ban."""
        r = self._http.post(
            "/api/v2/auth/login", data={"username": USERNAME, "password": self._password}
        )
        ok = r.status_code == 204 or (r.status_code == 200 and r.text.strip() == "Ok.")
        if not ok:
            raise HarnessError(f"login answered {r.status_code} {r.text!r}")

    def version(self) -> str:
        return self._checked(self._http.get("/api/v2/app/version")).text

    def set_prefs(self, prefs: dict[str, object]) -> None:
        self._checked(
            self._http.post("/api/v2/app/setPreferences", data={"json": json.dumps(prefs)})
        )

    def preferences(self) -> dict[str, object]:
        return self._checked(self._http.get("/api/v2/app/preferences")).json()

    @staticmethod
    def _write_durably(path: Path, data: bytes) -> None:
        # flush + fsync so the data is on disk before the add: guards the missingFiles
        # flake seen twice on the first add after a fresh pair.
        with path.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

    def add(self, t: HarnessTorrent, *, complete: bool) -> None:
        if complete:
            self._write_durably(self._downloads / t.name, t.data)
        r = self._http.post(
            "/api/v2/torrents/add",
            data={
                "savepath": "/downloads",
                "skip_checking": "true" if complete else "false",
                "stopped": "false",
            },
            files={"torrents": (f"{t.name}.torrent", t.metainfo, "application/x-bittorrent")},
        )
        self._checked(r)

    def rows(self) -> list[Row]:
        r = self._checked(self._http.get("/api/v2/torrents/info"))
        return [
            Row(
                hash=row["hash"],
                name=row["name"],
                state=row["state"],
                upspeed=row["upspeed"],
                dlspeed=row["dlspeed"],
                num_leechs=row["num_leechs"],
            )
            for row in r.json()
        ]

    def states(self) -> dict[str, str]:
        return {r.hash: r.state for r in self.rows()}

    def add_peers(self, hashes: list[str], peers: list[str]) -> None:
        """Tell the client about peers (`ip:port`) for these torrents."""
        self._checked(
            self._http.post(
                "/api/v2/torrents/addPeers",
                data={"hashes": "|".join(hashes), "peers": "|".join(peers)},
            )
        )

    def set_download_limit(self, hashes: list[str], limit: int) -> None:
        """Cap download speed for these torrents, in bytes per second."""
        self._checked(
            self._http.post(
                "/api/v2/torrents/setDownloadLimit",
                data={"hashes": "|".join(hashes), "limit": str(limit)},
            )
        )

    def delete(self, hashes: list[str], *, delete_files: bool) -> None:
        self._checked(
            self._http.post(
                "/api/v2/torrents/delete",
                data={
                    "hashes": "|".join(hashes),
                    "deleteFiles": "true" if delete_files else "false",
                },
            )
        )

    def reset(self, timeout: float = 15.0) -> None:
        """Delete every torrent, then wait until the client reports none."""
        hashes = list(self.states())
        if hashes:
            self.delete(hashes, delete_files=True)
        deadline = time.monotonic() + timeout
        last: dict[str, str] = {}
        while time.monotonic() < deadline:
            last = self.states()
            if last == {}:
                return
            time.sleep(0.25)
        raise HarnessError(f"client not empty after reset: {last}")

    @staticmethod
    def _checked(r: httpx.Response) -> httpx.Response:
        if r.status_code != 200:
            raise HarnessError(
                f"{r.request.method} {r.request.url.path} -> {r.status_code} {r.text!r}"
            )
        return r
