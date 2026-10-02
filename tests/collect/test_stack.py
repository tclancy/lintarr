import httpx

from lintarr.collect.stack import collect_stack
from lintarr.config import load_config

ENV = {
    "QBIT_URL": "http://qbt:8080",
    "QBIT_USER": "admin",
    "QBIT_PASS": "pw",
    "SONARR_URL": "http://sonarr:8989",
    "SONARR_API_KEY": "k",
    "SONARR_URL__ANIME": "http://anime:8989",
    "SONARR_API_KEY__ANIME": "k2",
}


def _transport(sonarr_down=False, anime_indexers=None):
    def handle(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "qbt":
            match path:
                case "/api/v2/auth/login":
                    return httpx.Response(200, text="Ok.")
                case "/api/v2/app/version":
                    return httpx.Response(200, text="v5.2.3")
                case "/api/v2/app/preferences":
                    return httpx.Response(200, json={"queueing_enabled": True})
                case "/api/v2/torrents/categories":
                    return httpx.Response(200, json={})
        if host == "anime" and sonarr_down:
            raise httpx.ConnectError("refused", request=request)
        if host == "anime" and anime_indexers is not None and path == "/api/v3/indexer":
            return httpx.Response(200, json=anime_indexers)
        match path:
            case "/api/v3/system/status":
                return httpx.Response(200, json={"version": "4.0.0"})
            case "/api/v3/indexer":
                return httpx.Response(200, json=[])
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def test_collects_every_configured_instance():
    facts = collect_stack(load_config(ENV), transport=_transport())
    assert [q.name for q in facts.qbits] == ["main"]
    assert sorted(a.name for a in facts.arrs) == ["anime", "main"]
    assert facts.errors == ()


def test_unreachable_instance_is_recorded_not_raised():
    facts = collect_stack(load_config(ENV), transport=_transport(sonarr_down=True))
    assert [a.name for a in facts.arrs] == ["main"]
    assert facts.errors == (("sonarr[anime]", "unreachable"),)


def test_unexpected_json_shape_is_recorded_not_raised():
    """A 200 carrying ``{"message": "Unauthorized"}`` must not abort the whole run.

    Before this was type-checked it raised AttributeError out of the adapter,
    past collect_stack's per-instance handler, killing every other service's
    facts and printing a traceback.
    """
    transport = _transport(anime_indexers={"message": "Unauthorized"})
    facts = collect_stack(load_config(ENV), transport=transport)
    assert [a.name for a in facts.arrs] == ["main"]
    assert facts.qbits, "the healthy qBittorrent instance must still report"
    assert facts.errors == (("sonarr[anime]", "bad-response"),)


def test_malformed_fields_entry_is_recorded_not_raised():
    """A nameless ``fields`` entry must not abort the whole run.

    One level deeper than the payload-shape case above: ``collect_arr``
    type-checked the indexer list but not the entries inside each indexer's
    ``fields``, so ``f["name"]`` raised a bare ``KeyError`` past
    ``collect_stack``'s ``except ServiceError`` and took the healthy
    instances' facts down with it.
    """
    malformed = [{"name": "nzb", "fields": [{"value": 1.0}]}]
    facts = collect_stack(load_config(ENV), transport=_transport(anime_indexers=malformed))
    assert [a.name for a in facts.arrs] == ["main"]
    assert facts.qbits, "the healthy qBittorrent instance must still report"
    assert facts.errors == (("sonarr[anime]", "bad-response"),)


def test_non_object_fields_payload_is_recorded_not_raised():
    """The silent half of the same defect, and the more dangerous one.

    ``fields`` as an array of strings raised nothing at all: ``"value" in
    "seedCriteria.seedRatio"`` is a substring test, so every entry was
    filtered out and the instance reported as healthy with its seed criteria
    ``Unknown("field-absent")`` — a malformed payload indistinguishable from
    an arr version that does not expose those fields.
    """
    malformed = [{"name": "nzb", "fields": ["seedCriteria.seedRatio"]}]
    facts = collect_stack(load_config(ENV), transport=_transport(anime_indexers=malformed))
    assert [a.name for a in facts.arrs] == ["main"]
    assert facts.qbits, "the healthy qBittorrent instance must still report"
    assert facts.errors == (("sonarr[anime]", "bad-response"),)
