import httpx

from lintarr.collect.http import ReadOnlyClient
from lintarr.collect.qbittorrent import AUTH_PATH, collect_qbt
from lintarr.config import QbtConfig
from lintarr.facts import is_known

CFG = QbtConfig(name="main", url="http://qbt", username="admin", password="pw")

PREFS = {
    "queueing_enabled": True,
    "max_active_downloads": 6,
    "max_active_uploads": 3,
    "max_active_torrents": 10,
    "dont_count_slow_torrents": True,
    "max_ratio_enabled": False,
    "max_ratio": -1,
    "max_ratio_act": 0,
    "max_seeding_time_enabled": False,
    "max_seeding_time": -1,
}


def _handler(prefs=PREFS, version="v5.2.3", categories=None):
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case p if p == AUTH_PATH:
                return httpx.Response(200, text="Ok.")
            case "/api/v2/app/version":
                return httpx.Response(200, text=version)
            case "/api/v2/app/preferences":
                return httpx.Response(200, json=prefs)
            case "/api/v2/torrents/categories":
                return httpx.Response(200, json=categories or {})
            case other:
                return httpx.Response(404, text=other)

    return handle


def _collect(**kw):
    client = ReadOnlyClient(
        "http://qbt", transport=httpx.MockTransport(_handler(**kw)), auth_path=AUTH_PATH
    )
    return collect_qbt(client, CFG), client


def test_reads_all_three_active_limits():
    qbt, _ = _collect()
    assert qbt.max_active_downloads.value == 6
    assert qbt.max_active_uploads.value == 3
    assert qbt.max_active_torrents.value == 10


def test_version_is_captured():
    qbt, _ = _collect()
    assert qbt.version == "v5.2.3"


def test_zero_and_unlimited_sentinels_survive():
    prefs = PREFS | {"max_active_downloads": 0, "max_active_torrents": -1}
    qbt, _ = _collect(prefs=prefs)
    assert qbt.max_active_downloads.value == 0
    assert qbt.max_active_torrents.value == -1


def test_absent_preference_becomes_unknown_field_absent():
    prefs = {k: v for k, v in PREFS.items() if k != "dont_count_slow_torrents"}
    qbt, _ = _collect(prefs=prefs)
    assert not is_known(qbt.dont_count_slow_torrents)
    assert qbt.dont_count_slow_torrents.reason == "field-absent"


def test_only_get_after_the_single_auth_post():
    _, client = _collect()
    assert client.methods_used[0] == "POST"
    assert set(client.methods_used[1:]) == {"GET"}
