"""The declared set once arr kinds are only known after collection.

An arr's kind is read from its ``appName``, so an arr that never answered has
no kind, and its error row is labelled ``arr[<name>]``. These run the real
``collect_stack`` and ``run_checks`` over a mock transport, because every
case here is about two layers agreeing: what collection labels, and what the
declared-services logic does with that label.
"""

import httpx

from lintarr.collect.stack import collect_stack
from lintarr.config import load_config
from lintarr.outcomes import Outcome, exit_code
from lintarr.run import declared_services, run_checks
from tests.fixtures.homelab_paths import arr_bodies

ARR_BODIES = arr_bodies()

CLIENT_ENV = {
    "DOWNLOAD_CLIENT_KIND": "qbittorrent",
    "DOWNLOAD_CLIENT_URL": "http://qbt:8080",
    "DOWNLOAD_CLIENT_USER": "admin",
    "DOWNLOAD_CLIENT_PASS": "pw",
}
ENV = CLIENT_ENV | {"ARR_URL__TV": "http://sonarr:8989", "ARR_API_KEY__TV": "k"}

#: Prefs under which queue-liveness hinges on arr data alone: with no indexer
#: to read, the seeding conflict is the one unknown premise.
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
    "max_inactive_seeding_time_enabled": False,
}

_NO_ARR_DETAIL = (
    "no sonarr or radarr is configured, so no indexer's seed criteria "
    "can be read — this conflict needs them and cannot be decided"
)


def _transport(*, arr_down: bool = False, app_name: str = "Sonarr") -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "sonarr" and arr_down:
            raise httpx.ConnectError("refused", request=request)
        match request.url.path:
            case "/api/v2/auth/login":
                return httpx.Response(200, text="Ok.")
            case "/api/v2/app/version":
                return httpx.Response(200, text="v5.2.4")
            case "/api/v2/app/preferences":
                return httpx.Response(200, json=WEDGED_PREFS)
            case "/api/v2/torrents/categories":
                return httpx.Response(200, json={})
            case "/api/v3/system/status":
                return httpx.Response(200, json={"version": "4.0.0", "appName": app_name})
            case "/api/v3/indexer":
                return httpx.Response(200, json=[])
            case path if path in ARR_BODIES:
                return httpx.Response(200, json=ARR_BODIES[path])
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def _findings(env, transport):
    cfg = load_config(env)
    facts = collect_stack(cfg, transport=transport)
    return run_checks(facts, declared=declared_services(cfg, facts))


def _errors(findings):
    return [f.instance for f in findings if f.outcome is Outcome.ERROR]


def _collect_skips(findings):
    return [f.instance for f in findings if f.invariant == "collect" and f.outcome is Outcome.SKIP]


def _queue(findings):
    (finding,) = [f for f in findings if f.invariant == "queue-liveness"]
    return finding


def test_a_declared_sonarr_whose_only_arr_is_unreachable_errors_once():
    """Review Focus 4: one ERROR for the arr, not an ERROR and a SKIP."""
    findings = _findings(ENV | {"LINTARR_SERVICES": "sonarr"}, _transport(arr_down=True))
    assert _errors(findings) == ["arr[tv]"]
    assert _collect_skips(findings) == []


def test_an_unreachable_arr_by_default_errors_once_and_is_not_called_unconfigured():
    findings = _findings(ENV, _transport(arr_down=True))
    assert _errors(findings) == ["arr[tv]"]
    assert _collect_skips(findings) == []
    queue = _queue(findings)
    assert queue.outcome is Outcome.SKIP
    assert queue.detail != _NO_ARR_DETAIL


def test_an_unsupported_app_name_is_an_error_and_fails_the_run():
    findings = _findings(ENV, _transport(app_name="Lidarr"))
    assert _errors(findings) == ["arr[tv]"]
    assert exit_code((f.outcome for f in findings), strict=True) != 0


def test_a_declared_sonarr_with_no_arr_configured_skips_and_fails_the_run():
    findings = _findings(CLIENT_ENV | {"LINTARR_SERVICES": "qbittorrent,sonarr"}, _transport())
    assert _collect_skips(findings) == ["sonarr"]
    assert exit_code((f.outcome for f in findings), strict=True) != 0


def test_no_arr_configured_and_none_declared_is_not_applicable():
    """Reachability control: the queue finding above *is* the arr-shaped SKIP."""
    findings = _findings(CLIENT_ENV, _transport())
    queue = _queue(findings)
    assert queue.outcome is Outcome.NOT_APPLICABLE
    assert queue.detail == _NO_ARR_DETAIL
    assert _collect_skips(findings) == []


def test_a_detected_arr_is_declared_by_default():
    cfg = load_config(ENV)
    facts = collect_stack(cfg, transport=_transport(app_name="Radarr"))
    assert declared_services(cfg, facts) == frozenset({"qbittorrent", "radarr"})


def test_an_explicit_declaration_wins_over_what_was_detected():
    cfg = load_config(ENV | {"LINTARR_SERVICES": "qbittorrent"})
    facts = collect_stack(cfg, transport=_transport(arr_down=True))
    assert declared_services(cfg, facts) == frozenset({"qbittorrent"})


def test_a_failed_arr_declares_the_placeholder_by_default():
    cfg = load_config(ENV)
    facts = collect_stack(cfg, transport=_transport(arr_down=True))
    assert declared_services(cfg, facts) == frozenset({"qbittorrent", "arr"})
