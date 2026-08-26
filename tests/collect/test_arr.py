import httpx
import pytest

from lintarr.collect.arr import collect_arr
from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.config import ArrConfig
from lintarr.facts import is_known

CFG = ArrConfig(name="main", kind="sonarr", url="http://sonarr", api_key="k")

_UNSET = object()


def _field(name, value=_UNSET, **extra):
    """One entry of an arr indexer's ``fields`` list, shaped like the real thing.

    A live Sonarr/Radarr field carries ``order``/``label``/``type``/``advanced``
    alongside ``name``, and — crucially — omits ``value`` entirely when the
    setting has never been configured. Fixtures that always supply ``value``
    are what let a bug collapsing "no value key" into ``Known(None)`` hide.
    Pass no *value* to reproduce that real omission.
    """
    entry = {"name": name, "order": 0, "label": name, "type": "textbox", "advanced": True}
    entry.update(extra)
    if value is not _UNSET:
        entry["value"] = value
    return entry


def _indexer(name, *, protocol="torrent", fields=None, enable_keys=True):
    """Build a raw indexer payload.

    ``enable_keys=True``/``False`` includes all three top-level ``enable*``
    keys set to that value; ``enable_keys=None`` omits them entirely
    (simulating an arr version that doesn't expose them); a dict merges
    specific overrides on top of an all-``True`` default.
    """
    default_fields = [
        _field("minimumSeeders", 1, type="number", advanced=False),
        _field("seedCriteria.seedRatio", None, type="number"),
        _field("seedCriteria.seedTime", None, type="number"),
        _field("seedCriteria.seasonPackSeedTime", None, type="number"),
    ]
    indexer = {
        "id": 1,
        "name": name,
        "protocol": protocol,
        "implementation": "Torznab",
        "priority": 25,
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


def _collect(indexers, version="4.0.15.2941", status=_UNSET):
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/api/v3/system/status":
                body = {"version": version} if status is _UNSET else status
                return httpx.Response(200, json=body)
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
    arr, _ = _collect([_indexer("EZTV", fields=[_field("seedCriteria.seedRatio", 2.0)])])
    assert arr.indexers[0].seed_ratio.value == 2.0


def test_missing_seed_field_entirely_is_unknown():
    arr, _ = _collect([_indexer("Old", fields=[_field("minimumSeeders", 1)])])
    assert not is_known(arr.indexers[0].seed_ratio)
    assert arr.indexers[0].seed_ratio.reason == "field-absent"


def test_field_entry_without_a_value_key_is_unknown_not_known_none():
    """A field listed with no ``value`` key was never read — it is not a null.

    This is the same shape as the ``enable`` defaulting bug: collapsing it to
    ``Known(None)`` makes an unread setting indistinguishable from one the
    user deliberately cleared, on the exact field the flagship check reads.
    """
    arr, _ = _collect([_indexer("NoValue", fields=[_field("seedCriteria.seedRatio")])])
    ratio = arr.indexers[0].seed_ratio
    assert not is_known(ratio)
    assert ratio.reason == "field-absent"


def test_field_entry_with_an_explicit_null_value_is_known_none():
    """The companion shape: ``value: null`` means configured-but-unset."""
    arr, _ = _collect([_indexer("Null", fields=[_field("seedCriteria.seedRatio", None)])])
    ratio = arr.indexers[0].seed_ratio
    assert is_known(ratio)
    assert ratio.value is None


def test_usenet_indexer_is_kept_with_its_protocol():
    arr, _ = _collect([_indexer("News", protocol="usenet")])
    idx = arr.indexers[0]
    assert idx.name == "News"
    assert is_known(idx.protocol)
    assert idx.protocol.value == "usenet"


def test_missing_protocol_is_unknown_not_empty_string():
    """An unread protocol must not silently classify an indexer as not-a-torrent."""
    raw = _indexer("NoProto")
    del raw["protocol"]
    arr, _ = _collect([raw])
    protocol = arr.indexers[0].protocol
    assert not is_known(protocol)
    assert protocol.reason == "field-absent"


@pytest.mark.parametrize("name", [None, "", "   "])
def test_indexer_without_a_usable_name_is_bad_response(name):
    raw = _indexer("placeholder")
    if name is None:
        del raw["name"]
    else:
        raw["name"] = name
    with pytest.raises(ServiceError) as e:
        _collect([raw])
    assert e.value.kind == "bad-response"


@pytest.mark.parametrize("status", [{}, {"version": None}, {"version": ""}, {"version": "  "}])
def test_missing_or_null_version_is_bad_response(status):
    """A fabricated version ('' or the literal 'None') would be stamped on every fact."""
    with pytest.raises(ServiceError) as e:
        _collect([], status=status)
    assert e.value.kind == "bad-response"


@pytest.mark.parametrize("status", [["not", "a", "dict"], "a string", 7])
def test_non_object_status_payload_is_bad_response(status):
    with pytest.raises(ServiceError) as e:
        _collect([], status=status)
    assert e.value.kind == "bad-response"


@pytest.mark.parametrize("payload", [{"message": "Unauthorized"}, ["a string"], "nope"])
def test_non_list_of_objects_indexer_payload_is_bad_response(payload):
    """A 200 carrying an error object must not raise AttributeError out of the adapter."""
    with pytest.raises(ServiceError) as e:
        _collect(payload)
    assert e.value.kind == "bad-response"


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
