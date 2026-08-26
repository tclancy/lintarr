import json
import os

import httpx
import pytest
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


_ARR_ENV = {"SONARR_URL": "http://sonarr:8989", "SONARR_API_KEY": "sonarrkey"}
_INDEXERS = [
    {
        "id": 1,
        "name": "1337x",
        "protocol": "torrent",
        "implementation": "Torznab",
        # The three real top-level toggles. There is no plain ``enable`` key on
        # an arr indexer; a fixture carrying one documents a field that does
        # not exist, and it was exactly that phantom key which produced an
        # earlier defaulting bug.
        "enableRss": True,
        "enableAutomaticSearch": True,
        "enableInteractiveSearch": False,
        # Only seedRatio is present -> seed_time/season_pack_seed_time are
        # Unknown("field-absent"); seed_ratio is Known(2.0).
        "fields": [
            {
                "name": "seedCriteria.seedRatio",
                "order": 0,
                "label": "Seed Ratio",
                "type": "number",
                "advanced": True,
                "value": 2.0,
            }
        ],
    }
]


def _run_both_services(args, *, collected):
    """Run dump-facts over a qBittorrent *and* an arr, proving both were collected.

    Absence assertions ("the password is not in the output") are vacuous
    unless something was actually collected — deleting qBittorrent collection
    entirely used to leave the credential test green. *collected* is a list of
    markers whose presence proves each service's facts reached the output.
    """
    result = _run(args, extra_env=_ARR_ENV, transport=_transport(arr_indexers=_INDEXERS))
    assert result.exit_code == 0, result.output
    for marker in collected:
        assert marker in result.output, f"{marker!r} missing — absence assertions would be vacuous"
    return result.output


@pytest.mark.parametrize(
    ("args", "collected"),
    [
        (["dump-facts"], ["qbittorrent[main]", "sonarr[main]", "1337x", "max_active_torrents"]),
        (["dump-facts", "--json"], ['"qbits"', '"indexers"', '"1337x"', '"max_active_torrents"']),
    ],
)
def test_dump_facts_never_prints_credentials(args, collected):
    """Neither the qBittorrent password nor the arr api key may reach the output.

    The api key travels in a request header and is the credential most likely
    to leak into a ``source`` string, so it is covered alongside the password.
    """
    output = _run_both_services(args, collected=collected)
    assert "hunter2" not in output
    assert "sonarrkey" not in output


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
    assert indexer["protocol"]["known"] is True
    assert indexer["protocol"]["value"] == "torrent"


def test_dump_facts_json_emits_the_service_version_of_every_known_fact():
    """service_version gates version-ranged axioms; unemitted, it is uncheckable."""
    transport = _transport(arr_indexers=_INDEXERS)
    payload = json.loads(
        _run(["dump-facts", "--json"], extra_env=_ARR_ENV, transport=transport).output
    )
    assert payload["qbits"][0]["max_active_torrents"]["service_version"] == "v5.2.3"
    indexer = payload["arrs"][0]["indexers"][0]
    assert indexer["seed_ratio"]["service_version"] == "4.0.15.2941"


def test_dump_facts_human_mode_includes_indexer_facts():
    """Regression: human mode used to drop every arr indexer fact silently.

    The assertions name indexer-specific fact keys. ``"field-absent"`` alone
    was satisfied by qBittorrent's own unknown prefs and ``"1337x"`` only
    proved the name line rendered, so both survived deleting the fact-
    rendering loop from ``_render_nested_list`` — the exact regression this
    test exists to catch.
    """
    transport = _transport(arr_indexers=_INDEXERS)
    result = _run(["dump-facts"], extra_env=_ARR_ENV, transport=transport)
    assert "1337x" in result.output
    # A Known indexer fact, an Unknown one, and the enable/protocol facts:
    # every branch of the nested renderer.
    assert "seed_ratio" in result.output
    assert "seed_time" in result.output
    assert "season_pack_seed_time" in result.output
    assert "enable_interactive_search" in result.output
    assert "protocol" in result.output
    assert "seed_time                    ? UNKNOWN (field-absent)" in result.output
