import httpx

from lintarr.collect.arr import collect_arr
from lintarr.collect.http import ReadOnlyClient
from lintarr.config import ArrConfig
from lintarr.facts import is_known

CFG = ArrConfig(name="main", kind="sonarr", url="http://sonarr", api_key="k")


def _indexer(name, *, protocol="torrent", fields=None, enable_keys=True):
    """Build a raw indexer payload.

    ``enable_keys=True``/``False`` includes all three top-level ``enable*``
    keys set to that value; ``enable_keys=None`` omits them entirely
    (simulating an arr version that doesn't expose them); a dict merges
    specific overrides on top of an all-``True`` default.
    """
    default_fields = [
        {"name": "minimumSeeders", "value": 1},
        {"name": "seedCriteria.seedRatio", "value": None},
        {"name": "seedCriteria.seedTime", "value": None},
        {"name": "seedCriteria.seasonPackSeedTime", "value": None},
    ]
    indexer = {
        "name": name,
        "protocol": protocol,
        "fields": default_fields if fields is None else fields,
    }
    if enable_keys is None:
        return indexer
    base = {"enableRss": True, "enableAutomaticSearch": True, "enableInteractiveSearch": True}
    if isinstance(enable_keys, dict):
        base.update(enable_keys)
    else:
        base = dict.fromkeys(base, bool(enable_keys))
    indexer.update(base)
    return indexer


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


def test_usenet_indexer_is_kept_with_its_protocol():
    arr, _ = _collect([_indexer("News", protocol="usenet")])
    assert (arr.indexers[0].name, arr.indexers[0].protocol) == ("News", "usenet")


def test_enable_fields_are_known_when_present():
    """Real, independent values for the three enable toggles — never collapsed."""
    arr, _ = _collect(
        [_indexer("Off", enable_keys={"enableRss": False, "enableAutomaticSearch": False})]
    )
    idx = arr.indexers[0]
    assert is_known(idx.enable_rss)
    assert idx.enable_rss.value is False
    assert is_known(idx.enable_automatic_search)
    assert idx.enable_automatic_search.value is False
    assert is_known(idx.enable_interactive_search)
    assert idx.enable_interactive_search.value is True


def test_missing_enable_fields_are_unknown_not_false():
    """An indexer with no enable* keys must not be silently read as disabled."""
    arr, _ = _collect([_indexer("Old", enable_keys=None)])
    idx = arr.indexers[0]
    assert not is_known(idx.enable_rss)
    assert idx.enable_rss.reason == "field-absent"
    assert not is_known(idx.enable_automatic_search)
    assert idx.enable_automatic_search.reason == "field-absent"
    assert not is_known(idx.enable_interactive_search)
    assert idx.enable_interactive_search.reason == "field-absent"


def test_issues_only_get_requests():
    _, client = _collect([])
    assert set(client.methods_used) == {"GET"}
