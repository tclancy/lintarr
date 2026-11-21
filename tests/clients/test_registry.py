"""The backend registry, and the qBittorrent backend's place in it.

The label pins are the behaviour-preservation half: moving qBittorrent behind a
``Backend`` must not change a single byte of the labels an alert key is built
from, or every persisting finding re-pages once.
"""

import httpx

from lintarr import checks
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


def test_qbittorrent_runs_queue_liveness_then_orphan_inevitability():
    from lintarr.clients.qbittorrent import orphan_inevitability

    assert len(BACKEND.checks) == 2
    assert BACKEND.checks[1] is orphan_inevitability.check


def test_registry_is_patchable():
    assert type(BACKENDS) is dict


def test_a_wedged_qbittorrent_finding_keeps_its_labels():
    facts = StackFacts(clients=(as_client(wedged_qbt()),), arrs=NO_GOALS)
    findings = run_checks(facts, declared=frozenset({"qbittorrent", "sonarr"}))
    assert [(f.invariant, f.instance) for f in findings if f.invariant == "queue-liveness"] == [
        ("queue-liveness", "qbittorrent[main]")
    ]
    others = [f for f in findings if f.invariant not in {"queue-liveness", "orphan-inevitability"}]
    assert [(f.invariant, f.instance) for f in others] == [
        ("hardlink-futility", "qbittorrent[main]")
    ]
    assert [
        (f.invariant, f.instance) for f in findings if f.invariant == "orphan-inevitability"
    ] == [("orphan-inevitability", "qbittorrent[main]")]


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
        declared_explicit=None,
    )
    facts = collect_stack(cfg, transport=httpx.MockTransport(refuse))
    assert [(e.label, e.kind) for e in facts.errors] == [("qbittorrent[main]", "unreachable")]


def test_no_backend_reuses_a_shared_arr_dependent_invariant_id():
    """The two failed-arr tables are merged per client, so their keys must not meet.

    A backend that names an id the shared checks already own has two
    ``ArrDependency`` values for one finding, and the merge used to take the
    shared one — printing a sentence about a check that never ran. The
    disjointness is the registration rule; ``run._arr_dependent`` refuses the
    pair if a future backend breaks it.
    """
    shared = set(checks.SHARED_ARR_DEPENDENT)
    # Reachability: both sides empty would make every intersection below vacuous.
    assert shared == {"hardlink-futility"}
    assert set(BACKENDS) == {"qbittorrent"}
    for kind, backend in BACKENDS.items():
        assert set(backend.arr_dependent) & shared == set(), kind
