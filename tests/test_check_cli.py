import json
import os

import httpx
from click.testing import CliRunner

from lintarr.cli import cli

_PREFIXES = ("QBIT_", "SONARR_", "RADARR_", "LINTARR_")
_CLEARED = {k: None for k in os.environ if k.startswith(_PREFIXES)}
# Distinctive credentials: "pw" would match too much to prove anything about
# what does not reach the output.
QBIT_PASSWORD = "hunter2"
SONARR_API_KEY = "sonarrkey"
ENV = {
    "QBIT_URL": "http://qbt:8080",
    "QBIT_USER": "admin",
    "QBIT_PASS": QBIT_PASSWORD,
    "SONARR_URL": "http://sonarr:8989",
    "SONARR_API_KEY": SONARR_API_KEY,
}

# A torrent indexer with no seed criteria at all — homelab#393's shape. Without
# this, ``arrs`` collects to () and the seeding conflict has no indexer to
# read, so the stack is reported undecidable rather than wedged.
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


def _run(args, prefs=WEDGED_PREFS, indexers=WEDGED_INDEXERS, *, env=None, transport=None):
    return CliRunner().invoke(
        cli,
        args,
        env={**_CLEARED, **(ENV if env is None else env)},
        obj={"transport": transport or _transport(prefs, indexers)},
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


# --- What a person actually reads --------------------------------------------
#
# Nothing tested the rendered text before this, so every mutant living in it
# survived: a "Therefore" line printed on a PASS, a swapped FAIL header, and a
# cause keyed on the invariant id that stated the wrong one of two conflicts.

STARVED_PREFS = WEDGED_PREFS | {
    # Every seeding safeguard ON, so the seeding conflict is definitively false
    # and the only thing wrong is that no download can ever get a slot.
    "max_active_downloads": 0,
    "dont_count_slow_torrents": True,
    "max_ratio_enabled": True,
    "max_ratio": 1.5,
    "max_seeding_time_enabled": True,
    "max_seeding_time": 20160,
}

# max_ratio_enabled absent from the payload -> unreadable -> the seeding
# conflict cannot be decided, and nothing else is wrong.
UNREADABLE_PREFS = {k: v for k, v in WEDGED_PREFS.items() if k != "max_ratio_enabled"} | {
    "max_active_downloads": 6,
    "max_active_torrents": 10,
}


def _unreachable_transport():
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    return httpx.MockTransport(handle)


def test_the_seeding_conflict_explains_itself_as_seeders_holding_the_slots():
    output = _run(["check"]).output
    assert "What lintarr read from your stack" in output
    assert "Therefore: completed torrents hold every active slot" in output
    assert "no category sets its own" in output


def test_the_starvation_conflict_names_the_limits_and_not_the_share_settings():
    """The wrong "Therefore" here recommends a change the code says will not help.

    This configuration has both share limits ON and the slow-torrent exemption
    ON. A cause keyed on the invariant id alone printed "completed torrents
    hold every active slot" — nothing has completed, and turning the share
    limits on is what the operator has already done.
    """
    output = _run(["check"], prefs=STARVED_PREFS).output
    assert "FAIL" in output
    assert "max_active_downloads or max_active_torrents is" in output
    assert "completed torrents hold every active slot" not in output


def test_the_two_conflicts_do_not_explain_themselves_the_same_way():
    """Pins the discrimination itself, not just each string."""
    seeding = _run(["check"]).output
    starved = _run(["check"], prefs=STARVED_PREFS).output
    assert "Therefore:" in seeding and "Therefore:" in starved
    assert seeding.split("Therefore:")[1] != starved.split("Therefore:")[1]


def test_no_cause_is_stated_when_nothing_was_proved():
    """A "Therefore" on a SKIP asserts a conclusion from premises we never read."""
    output = _run(["check"], prefs=UNREADABLE_PREFS).output
    assert "SKIP" in output
    assert "Therefore:" not in output


def test_a_skip_reports_an_unknown_premise_as_unknown():
    """Not "does not hold" — that asserts the negation of a fact we do not have."""
    output = _run(["check"], prefs=UNREADABLE_PREFS).output
    assert "qbt.no_global_ratio" in output
    assert "unknown" in output
    assert "does not hold" not in output


def test_a_pass_states_no_premises_and_no_cause():
    repaired = WEDGED_PREFS | {
        "max_active_downloads": 6,
        "max_active_torrents": 10,
        "dont_count_slow_torrents": True,
        "max_ratio_enabled": True,
        "max_ratio": 1.5,
        "max_seeding_time_enabled": True,
    }
    output = _run(["check"], prefs=repaired).output
    assert "PASS" in output
    assert "Therefore:" not in output
    assert "What lintarr read from your stack" not in output


# --- Exit codes --------------------------------------------------------------


def test_an_unreadable_service_exits_two_not_one():
    """ "Could not look" is not "looked and found a conflict"."""
    result = _run(["check"], transport=_unreachable_transport())
    assert result.exit_code == 2
    assert "ERROR" in result.output


def test_an_undecidable_run_exits_three_under_strict():
    result = _run(["check"], prefs=UNREADABLE_PREFS)
    assert result.exit_code == 3


def test_no_strict_lets_an_undecidable_run_exit_zero():
    result = _run(["check", "--no-strict"], prefs=UNREADABLE_PREFS)
    assert result.exit_code == 0


def test_no_strict_still_fails_on_a_proved_conflict():
    """--no-strict relaxes "could not look", never "looked and found it"."""
    assert _run(["check", "--no-strict"]).exit_code == 1


def test_the_json_payload_carries_the_exit_code_the_process_returned():
    result = _run(["check", "--json"])
    payload = json.loads(result.output)
    assert payload["exit_code"] == result.exit_code == 1


def test_the_json_outcome_and_exit_code_may_disagree_and_both_are_reported():
    """One FAIL and one SKIP: severity says SKIP, the exit code says FAIL.

    A machine consumer branches on ``exit_code``; ``outcome`` tells it how much
    of the stack was examined. Emitting only the severity left the payload
    contradicting the process's own return value with no way to tell.
    """
    env = {k: v for k, v in ENV.items() if not k.startswith("SONARR_")}
    result = _run(
        ["check", "--json"],
        prefs=STARVED_PREFS,
        env={**env, "LINTARR_SERVICES": "qbittorrent,sonarr"},
    )
    payload = json.loads(result.output)
    outcomes = {f["invariant"]: f["outcome"] for f in payload["findings"]}
    assert outcomes["queue-liveness"] == "FAIL"
    assert outcomes["collect"] == "SKIP"
    assert payload["outcome"] == "SKIP"
    assert payload["exit_code"] == result.exit_code == 1


def test_a_declared_service_that_never_arrived_is_named_in_the_output():
    """A qBittorrent-only run must say out loud that it never read Sonarr."""
    env = {k: v for k, v in ENV.items() if not k.startswith("SONARR_")}
    result = _run(["check"], env={**env, "LINTARR_SERVICES": "qbittorrent,sonarr"})
    assert "sonarr" in result.output
    assert "declared but never collected" in result.output
    assert result.exit_code == 3


def test_the_json_finding_names_which_conflict_decided_it():
    payload = json.loads(_run(["check", "--json"]).output)
    finding = payload["findings"][0]
    assert finding["conflict"] == "seeders-absorb-every-slot"


def test_check_never_prints_credentials():
    """Neither the qBittorrent password nor the arr api key may reach the output.

    Asserted against a run that definitely read both services, so the absence
    is not vacuous.
    """
    output = _run(["check"]).output
    assert "queue-liveness" in output, "nothing was checked — the assertions below prove nothing"
    assert "arr.indexer_without_seed_criteria" in output, "the arr was never read"
    assert QBIT_PASSWORD not in output
    assert SONARR_API_KEY not in output
