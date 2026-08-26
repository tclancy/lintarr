import json
import os

import httpx
from click.testing import CliRunner

from lintarr.cli import cli

ENV = {
    "QBIT_URL": "http://qbt:8080",
    "QBIT_USER": "admin",
    "QBIT_PASS": "hunter2",
}

# Click's CliRunner env parameter ADDS to the ambient environment rather than
# replacing it, and the CLI reads os.environ. If the machine running these
# tests happens to export any lintarr-relevant variable — including a named
# instance like QBIT_URL__SECONDARY that no fixed list would anticipate — a
# test could try to collect from a service it never intended to, passing
# locally but failing in CI or vice versa. Click treats a value of None in
# the env mapping as "unset this variable", so clear every ambient variable
# matching lintarr's prefixes (QBIT_URL__MAIN, for instance, *does* resolve
# to a real "main" instance per config._instances(), so it is not a no-op to
# clear it) and layer each test's own keys on top so they win.
_LINTARR_PREFIXES = ("QBIT_", "SONARR_", "RADARR_", "LINTARR_")
_CLEARED = {k: None for k in os.environ if k.startswith(_LINTARR_PREFIXES)}


def _transport(*, arr_indexers=None):
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/api/v2/auth/login":
                return httpx.Response(200, text="Ok.")
            case "/api/v2/app/version":
                return httpx.Response(200, text="v5.2.3")
            case "/api/v2/app/preferences":
                return httpx.Response(
                    200, json={"queueing_enabled": True, "max_active_torrents": 5}
                )
            case "/api/v2/torrents/categories":
                return httpx.Response(200, json={})
            case "/api/v3/system/status":
                return httpx.Response(200, json={"version": "4.0.15.2941"})
            case "/api/v3/indexer":
                return httpx.Response(200, json=arr_indexers or [])
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def _run(args, *, extra_env=None, transport=None):
    env = {**_CLEARED, **ENV, **(extra_env or {})}
    return CliRunner().invoke(cli, args, env=env, obj={"transport": transport or _transport()})


def test_dump_facts_json_marks_unread_fields_unknown():
    result = _run(["dump-facts", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    qbt = payload["qbits"][0]
    assert qbt["max_active_torrents"]["known"] is True
    assert qbt["max_active_torrents"]["value"] == 5
    assert qbt["max_ratio_enabled"]["known"] is False
    assert qbt["max_ratio_enabled"]["reason"] == "field-absent"


def test_dump_facts_records_the_source_of_every_known_fact():
    payload = json.loads(_run(["dump-facts", "--json"]).output)
    assert payload["qbits"][0]["max_active_torrents"]["source"] == "GET /api/v2/app/preferences"


def test_dump_facts_never_prints_credentials():
    assert "hunter2" not in _run(["dump-facts"]).output
    assert "hunter2" not in _run(["dump-facts", "--json"]).output


_ARR_ENV = {"SONARR_URL": "http://sonarr:8989", "SONARR_API_KEY": "sonarrkey"}
_INDEXERS = [
    {
        "name": "1337x",
        "enable": True,
        "protocol": "torrent",
        # Only seedRatio is present -> seed_time/season_pack_seed_time are
        # Unknown("field-absent"); seed_ratio is Known(2.0).
        "fields": [{"name": "seedCriteria.seedRatio", "value": 2.0}],
    }
]


def test_dump_facts_json_includes_indexer_facts():
    transport = _transport(arr_indexers=_INDEXERS)
    result = _run(["dump-facts", "--json"], extra_env=_ARR_ENV, transport=transport)
    payload = json.loads(result.output)
    indexer = payload["arrs"][0]["indexers"][0]
    assert indexer["name"] == "1337x"
    assert indexer["seed_ratio"]["known"] is True
    assert indexer["seed_ratio"]["value"] == 2.0
    assert indexer["seed_time"]["known"] is False
    assert indexer["seed_time"]["reason"] == "field-absent"


def test_dump_facts_human_mode_includes_indexer_facts():
    """Regression: human mode used to drop every arr indexer fact silently."""
    transport = _transport(arr_indexers=_INDEXERS)
    result = _run(["dump-facts"], extra_env=_ARR_ENV, transport=transport)
    assert "1337x" in result.output
    assert "field-absent" in result.output
