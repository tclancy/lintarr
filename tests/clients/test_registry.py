"""The backend registry, and the qBittorrent backend's place in it.

The label pins are the behaviour-preservation half: moving qBittorrent behind a
``Backend`` must not change a single byte of the labels an alert key is built
from, or every persisting finding re-pages once.
"""

import httpx

from lintarr.clients import BACKENDS
from lintarr.clients.qbittorrent import BACKEND
from lintarr.collect.stack import collect_stack
from lintarr.config import ClientConfig, LintarrConfig
from lintarr.models import StackFacts
from lintarr.run import run_checks
from tests.fixtures.clients import as_client
from tests.fixtures.homelab import wedged_qbt
from tests.invariants.test_queue_liveness import NO_GOALS


def test_qbittorrent_is_registered_under_its_kind():
    assert BACKENDS["qbittorrent"] is BACKEND
    assert BACKEND.kind == "qbittorrent"


def test_qbittorrent_needs_user_and_pass():
    assert BACKEND.credentials == frozenset({"user", "pass"})


def test_queue_liveness_is_qbittorrents_only_check():
    assert len(BACKEND.checks) == 1


def test_registry_is_patchable():
    assert type(BACKENDS) is dict


def test_a_wedged_qbittorrent_finding_keeps_its_labels():
    facts = StackFacts(clients=(as_client(wedged_qbt()),), arrs=NO_GOALS)
    findings = run_checks(facts, declared=frozenset({"qbittorrent", "sonarr"}))
    assert [(f.invariant, f.instance) for f in findings] == [
        ("queue-liveness", "qbittorrent[main]")
    ]


def test_a_failing_qbittorrent_keeps_its_error_label():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    cfg = LintarrConfig(
        clients=(
            ClientConfig(
                name="main", kind="qbittorrent", url="http://qbt", username="u", password="p"
            ),
        ),
        arrs=(),
        declared=frozenset({"qbittorrent"}),
    )
    facts = collect_stack(cfg, transport=httpx.MockTransport(refuse))
    assert [(e.label, e.kind) for e in facts.errors] == [("qbittorrent[main]", "unreachable")]
