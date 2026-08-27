import json
import os

import httpx
from click.testing import CliRunner

from lintarr.cli import cli

_PREFIXES = ("QBIT_", "SONARR_", "RADARR_", "LINTARR_")
_CLEARED = {k: None for k in os.environ if k.startswith(_PREFIXES)}
ENV = {
    "QBIT_URL": "http://qbt:8080",
    "QBIT_USER": "admin",
    "QBIT_PASS": "pw",
    "SONARR_URL": "http://sonarr:8989",
    "SONARR_API_KEY": "sonarrkey",
}

# A torrent indexer with no seed criteria at all — homelab#393's shape. Without
# this, ``arrs`` collects to () and queue_liveness.check()'s
# arr.indexer_without_seed_criteria premise reads as False (nothing to find),
# never None, so the seeding conflict can never turn every premise true and
# the stack cannot be reported as wedged.
WEDGED_INDEXERS = [
    {
        "id": 1,
        "name": "1337x",
        "protocol": "torrent",
        "implementation": "Torznab",
        "enableRss": True,
        "enableAutomaticSearch": True,
        "enableInteractiveSearch": False,
        "fields": [],
    }
]

WEDGED_PREFS = {
    "queueing_enabled": True,
    "max_active_downloads": 3,
    "max_active_uploads": 3,
    "max_active_torrents": 5,
    "dont_count_slow_torrents": False,
    "max_ratio_enabled": False,
    "max_ratio": -1,
    "max_ratio_act": 0,
    "max_seeding_time_enabled": False,
    "max_seeding_time": -1,
}


def _transport(prefs, indexers=WEDGED_INDEXERS):
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/api/v2/auth/login":
                return httpx.Response(200, text="Ok.")
            case "/api/v2/app/version":
                return httpx.Response(200, text="v5.2.3")
            case "/api/v2/app/preferences":
                return httpx.Response(200, json=prefs)
            case "/api/v2/torrents/categories":
                return httpx.Response(200, json={})
            case "/api/v3/system/status":
                return httpx.Response(200, json={"version": "4.0.15.2941"})
            case "/api/v3/indexer":
                return httpx.Response(200, json=indexers)
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def _run(args, prefs=WEDGED_PREFS, indexers=WEDGED_INDEXERS):
    return CliRunner().invoke(
        cli, args, env={**_CLEARED, **ENV}, obj={"transport": _transport(prefs, indexers)}
    )


def test_wedged_config_exits_one_and_names_the_settings():
    result = _run(["check"])
    assert result.exit_code == 1
    assert "queue-liveness" in result.output
    assert "qbt.no_global_ratio" in result.output


def test_repaired_config_exits_zero():
    repaired = WEDGED_PREFS | {
        "max_active_downloads": 6,
        "max_active_torrents": 10,
        "dont_count_slow_torrents": True,
        "max_ratio_enabled": True,
        "max_ratio": 1.5,
        "max_seeding_time_enabled": True,
    }
    result = _run(["check"], prefs=repaired)
    assert result.exit_code == 0


def test_json_mode_emits_findings_with_premises():
    result = _run(["check", "--json"])
    payload = json.loads(result.output)
    finding = payload["findings"][0]
    assert finding["invariant"] == "queue-liveness"
    assert finding["outcome"] == "FAIL"
    assert any(p["label"] == "qbt.no_global_ratio" for p in finding["premises"])


def test_summary_line_counts_by_outcome():
    assert "1 FAIL" in _run(["check"]).output
