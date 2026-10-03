"""What ``collect_stack`` writes into ``StackFacts.errors``, for both adapters.

Issue #6 recorded the qBittorrent ``except ServiceError`` branch as untested.
Measured on main: replacing it with a bare ``raise`` *is* killed, but only
incidentally, by a CLI test that asserts an exit code and the word ERROR. What
nothing pinned is the **label** — ``label = "WRONG"`` passed all 236 tests.

That is not cosmetic. ``run.py`` recovers a service's kind by splitting the
label on ``[``, and uses it to decide which declared services were never
attempted. A wrong label makes a failed service report an ERROR *and* a
spurious "declared but never collected" SKIP for the same service, so the
operator is told both that the read failed and that it never happened.

Kept out of ``test_stack.py`` deliberately: that file is in flight on two open
PRs (#12, #15), and a residuals fix should not land a conflict in either.
"""

import httpx
import pytest

from lintarr.collect.stack import collect_stack
from lintarr.config import load_config
from lintarr.outcomes import Outcome
from lintarr.run import run_checks

ENV = {
    "QBIT_URL": "http://qbt:8080",
    "QBIT_USER": "admin",
    "QBIT_PASS": "pw",
    "QBIT_URL__VPN": "http://vpnqbt:8080",
    "QBIT_USER__VPN": "admin",
    "QBIT_PASS__VPN": "pw",
    "SONARR_URL": "http://sonarr:8989",
    "SONARR_API_KEY": "k",
}


def _transport(*, down: frozenset[str] = frozenset()) -> httpx.MockTransport:
    """Every host answers healthily unless named in *down*."""

    def handle(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host in down:
            raise httpx.ConnectError("refused", request=request)
        match path:
            case "/api/v2/auth/login":
                return httpx.Response(200, text="Ok.")
            case "/api/v2/app/version":
                return httpx.Response(200, text="v5.2.4")
            case "/api/v2/app/preferences":
                return httpx.Response(200, json={"queueing_enabled": True})
            case "/api/v2/torrents/categories":
                return httpx.Response(200, json={})
            case "/api/v3/system/status":
                return httpx.Response(200, json={"version": "4.0.0"})
            case "/api/v3/indexer":
                return httpx.Response(200, json=[])
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def test_a_failing_qbittorrent_is_recorded_under_its_own_label():
    """``qbittorrent[name]`` — the kind, then the instance name in brackets."""
    facts = collect_stack(load_config(ENV), transport=_transport(down=frozenset({"qbt"})))
    assert facts.errors == (("qbittorrent[main]", "unreachable"),)


def test_the_label_names_the_instance_that_failed_and_not_its_healthy_sibling():
    """Two qBittorrents, one down. A label built from the wrong one is useless.

    An operator running a VPN-bound client beside a direct one gets told to go
    and look at a service that is working.
    """
    facts = collect_stack(load_config(ENV), transport=_transport(down=frozenset({"vpnqbt"})))
    assert facts.errors == (("qbittorrent[vpn]", "unreachable"),)
    assert [q.name for q in facts.qbits] == ["main"]


def test_an_arr_is_recorded_under_its_kind_not_the_word_arr():
    """``sonarr[main]``, because ``run.py`` matches the label's kind against a
    declaration, and nothing ever declares ``arr``."""
    facts = collect_stack(load_config(ENV), transport=_transport(down=frozenset({"sonarr"})))
    assert facts.errors == (("sonarr[main]", "unreachable"),)


@pytest.mark.parametrize(
    ("down", "label"),
    [
        (frozenset({"qbt", "vpnqbt"}), "qbittorrent"),
        (frozenset({"sonarr"}), "sonarr"),
    ],
)
def test_a_service_that_failed_is_not_also_reported_as_never_collected(down, label):
    """The consequence that makes the label load-bearing rather than decorative.

    ``run._attempted`` splits each error label on ``[`` to recover the kind and
    subtracts it from the declared set, so a label the declaration cannot match
    produces a second, contradictory finding about the same service: ERROR
    ("could not read this") alongside SKIP ("nothing was read from it, and
    nothing tried"). One failure, two findings, and the louder one is the
    accurate one.
    """
    cfg = load_config(ENV)
    facts = collect_stack(cfg, transport=_transport(down=down))
    findings = run_checks(facts, declared=cfg.declared)

    skipped_as_absent = [
        f for f in findings if f.outcome is Outcome.SKIP and f.invariant == "collect"
    ]
    assert [f.instance for f in skipped_as_absent] == [], (
        f"{label} failed to answer and was ALSO reported as never collected"
    )
    assert any(f.outcome is Outcome.ERROR for f in findings), "the failure must still be reported"


def test_a_declared_service_that_was_never_attempted_still_skips():
    """Reachability control for the assertion above, which asserts an absence.

    ``skipped_as_absent == []`` is also what a ``_absent_service_findings``
    that had stopped firing altogether would produce, so the empty list only
    means something alongside a case that fills it. Radarr is declared here and
    has no URL, so it is never attempted and must SKIP.
    """
    cfg = load_config(ENV)
    facts = collect_stack(cfg, transport=_transport())
    findings = run_checks(facts, declared=cfg.declared | {"radarr"})
    absent = [f for f in findings if f.outcome is Outcome.SKIP and f.invariant == "collect"]
    assert [f.instance for f in absent] == ["radarr"]
