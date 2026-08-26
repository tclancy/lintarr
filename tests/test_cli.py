import json

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
# tests happens to export any of these, a test could try to collect from a
# service it never intended to, passing locally but failing in CI or vice
# versa. Click treats a value of None in the env mapping as "unset this
# variable", so explicitly clear everything lintarr-relevant that this test
# module does not itself set.
_CLEARED = {
    k: None
    for k in (
        "QBIT_URL",
        "QBIT_URL__MAIN",
        "QBIT_USER",
        "QBIT_USER__MAIN",
        "QBIT_PASS",
        "QBIT_PASS__MAIN",
        "SONARR_URL",
        "SONARR_URL__MAIN",
        "SONARR_API_KEY",
        "SONARR_API_KEY__MAIN",
        "RADARR_URL",
        "RADARR_URL__MAIN",
        "RADARR_API_KEY",
        "RADARR_API_KEY__MAIN",
        "LINTARR_SERVICES",
    )
}


def _transport():
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
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def _run(args):
    return CliRunner().invoke(cli, args, env={**_CLEARED, **ENV}, obj={"transport": _transport()})


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
