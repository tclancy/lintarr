"""Unit tests (no Docker) for the QbtHarness calls the leecher setup needs."""

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from tests.conformance.qbt import HarnessError, QbtHarness, Row
from tests.conformance.torrents import make_torrent


def _harness(answer: httpx.Response, downloads: Path = Path("/nowhere")):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer

    h = QbtHarness("http://qbt", "pw", downloads, transport=httpx.MockTransport(handler))
    return h, seen


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def _multipart_field(request: httpx.Request, name: str) -> str | None:
    """One form field of a multipart add, or None when it was not sent."""
    parts = request.content.decode(errors="replace").split("\r\n\r\n")
    for header, value in zip(parts, parts[1:], strict=False):
        if f'name="{name}"' in header:
            return value.split("\r\n")[0]
    return None


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


def test_rows_reads_speeds_leechers_and_super_seeding():
    row = {
        "hash": "h",
        "name": "n",
        "state": "uploading",
        "upspeed": 70000,
        "dlspeed": 0,
        "num_leechs": 1,
        "super_seeding": True,
        "extra": "ignored",
    }
    h, _ = _harness(httpx.Response(200, json=[row]))
    assert h.rows() == [Row("h", "n", "uploading", 70000, 0, 1, super_seeding=True)]
    assert h.states() == {"h": "uploading"}


def test_add_files_a_torrent_under_a_category_when_one_is_named(tmp_path: Path):
    h, seen = _harness(httpx.Response(200), tmp_path)
    h.add(make_torrent("with-category"), complete=True, category="tv")
    assert _multipart_field(seen[0], "category") == "tv"


def test_add_sends_no_category_field_when_none_is_named(tmp_path: Path):
    h, seen = _harness(httpx.Response(200), tmp_path)
    h.add(make_torrent("no-category"), complete=True)
    assert _multipart_field(seen[0], "category") is None


def test_categories_returns_the_endpoint_body():
    body = {"tv": {"name": "tv", "share_limit_action": "Remove"}}
    h, seen = _harness(httpx.Response(200, json=body))
    assert h.categories() == body
    assert seen[0].url.path == "/api/v2/torrents/categories"


def test_non_200_raises_harness_error():
    h, _ = _harness(httpx.Response(400, text="bad"))
    with pytest.raises(HarnessError, match="addPeers"):
        h.add_peers(["a"], ["1.2.3.4:5"])


def test_make_torrent_public_has_no_private_key():
    assert b"7:privatei1e" not in make_torrent("x", private=False).metainfo
    assert b"7:privatei1e" in make_torrent("x").metainfo
