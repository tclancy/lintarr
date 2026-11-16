"""Unit tests (no Docker) for the QbtHarness calls the leecher setup needs."""

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from tests.conformance.qbt import HarnessError, QbtHarness, Row
from tests.conformance.torrents import make_torrent


def _harness(answer: httpx.Response):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer

    h = QbtHarness("http://qbt", "pw", Path("/nowhere"), transport=httpx.MockTransport(handler))
    return h, seen


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def test_add_peers_posts_pipe_joined_hashes_and_peers():
    h, seen = _harness(httpx.Response(200))
    h.add_peers(["a", "b"], ["172.18.0.2:6881"])
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/api/v2/torrents/addPeers"
    assert _form(seen[0]) == {"hashes": "a|b", "peers": "172.18.0.2:6881"}


def test_set_download_limit_posts_bytes_per_second():
    h, seen = _harness(httpx.Response(200))
    h.set_download_limit(["a"], 65536)
    assert seen[0].url.path == "/api/v2/torrents/setDownloadLimit"
    assert _form(seen[0])["limit"] == "65536"
    assert _form(seen[0])["hashes"] == "a"


def test_rows_reads_speeds_and_leechers():
    row = {
        "hash": "h",
        "name": "n",
        "state": "uploading",
        "upspeed": 70000,
        "dlspeed": 0,
        "num_leechs": 1,
        "extra": "ignored",
    }
    h, _ = _harness(httpx.Response(200, json=[row]))
    assert h.rows() == [Row("h", "n", "uploading", 70000, 0, 1)]
    assert h.states() == {"h": "uploading"}


def test_non_200_raises_harness_error():
    h, _ = _harness(httpx.Response(400, text="bad"))
    with pytest.raises(HarnessError, match="addPeers"):
        h.add_peers(["a"], ["1.2.3.4:5"])


def test_make_torrent_public_has_no_private_key():
    assert b"7:privatei1e" not in make_torrent("x", private=False).metainfo
    assert b"7:privatei1e" in make_torrent("x").metainfo
