import httpx

from lintarr.collect.arr import collect_arr
from lintarr.collect.http import ReadOnlyClient
from lintarr.config import ArrConfig
from lintarr.facts import is_known

CFG = ArrConfig(name="main", kind="sonarr", url="http://sonarr", api_key="k")


def _indexer(name, *, enable=True, protocol="torrent", fields=None):
    default = [
        {"name": "minimumSeeders", "value": 1},
        {"name": "seedCriteria.seedRatio", "value": None},
        {"name": "seedCriteria.seedTime", "value": None},
        {"name": "seedCriteria.seasonPackSeedTime", "value": None},
    ]
    return {"name": name, "enable": enable, "protocol": protocol,
            "fields": default if fields is None else fields}


def _collect(indexers, version="4.0.15.2941"):
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/api/v3/system/status":
                return httpx.Response(200, json={"version": version})
            case "/api/v3/indexer":
                return httpx.Response(200, json=indexers)
            case other:
                return httpx.Response(404, text=other)

    client = ReadOnlyClient("http://sonarr", transport=httpx.MockTransport(handle))
    return collect_arr(client, CFG), client


def test_reads_version():
    arr, _ = _collect([])
    assert arr.version == "4.0.15.2941"


def test_unset_seed_ratio_is_known_none_not_unknown():
    """Field present with a null value means configured-but-unset."""
    arr, _ = _collect([_indexer("1337x")])
    ratio = arr.indexers[0].seed_ratio
    assert is_known(ratio)
    assert ratio.value is None


def test_set_seed_ratio_is_read():
    fields = [{"name": "seedCriteria.seedRatio", "value": 2.0}]
    arr, _ = _collect([_indexer("EZTV", fields=fields)])
    assert arr.indexers[0].seed_ratio.value == 2.0


def test_missing_seed_field_entirely_is_unknown():
    arr, _ = _collect([_indexer("Old", fields=[{"name": "minimumSeeders", "value": 1}])])
    assert not is_known(arr.indexers[0].seed_ratio)
    assert arr.indexers[0].seed_ratio.reason == "field-absent"


def test_disabled_and_usenet_indexers_are_kept_with_their_flags():
    arr, _ = _collect([
        _indexer("Off", enable=False),
        _indexer("News", protocol="usenet"),
    ])
    assert [(i.name, i.enabled, i.protocol) for i in arr.indexers] == [
        ("Off", False, "torrent"),
        ("News", True, "usenet"),
    ]


def test_issues_only_get_requests():
    _, client = _collect([])
    assert set(client.methods_used) == {"GET"}
