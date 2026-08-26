"""lintarr holds every credential in the stack. It must not be able to mutate it."""

import httpx

from lintarr.collect.stack import collect_stack
from lintarr.config import load_config

ENV = {
    "QBIT_URL": "http://qbt:8080",
    "QBIT_USER": "admin",
    "QBIT_PASS": "pw",
    "SONARR_URL": "http://sonarr:8989",
    "SONARR_API_KEY": "k",
}


def test_only_verb_other_than_get_is_the_qbittorrent_login():
    seen: list[tuple[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        match request.url.path:
            case "/api/v2/auth/login":
                return httpx.Response(200, text="Ok.")
            case "/api/v2/app/version":
                return httpx.Response(200, text="v5.2.3")
            case "/api/v2/app/preferences" | "/api/v3/system/status":
                return httpx.Response(200, json={"version": "4.0.0"})
            case "/api/v2/torrents/categories" | "/api/v3/indexer":
                return httpx.Response(200, json=[])
        return httpx.Response(404)

    facts = collect_stack(load_config(ENV), transport=httpx.MockTransport(handle))

    # The verb assertion below is only meaningful if collection actually
    # happened. Without these, a regression that silently dropped arr (or
    # qBittorrent) collection would still produce a passing test.
    assert facts.qbits, "no qBittorrent instance was collected — verb assertion would be vacuous"
    assert facts.arrs, "no arr instance was collected — verb assertion would be vacuous"
    assert facts.errors == (), f"expected a clean run, got errors: {facts.errors}"

    seen_paths = {p for _, p in seen}
    assert "/api/v3/system/status" in seen_paths, "arr path was never exercised"
    assert "/api/v3/indexer" in seen_paths, "arr path was never exercised"

    non_get = [(m, p) for m, p in seen if m != "GET"]
    assert non_get == [("POST", "/api/v2/auth/login")]
