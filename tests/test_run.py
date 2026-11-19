import pytest

from lintarr.facts import Unknown
from lintarr.models import ErrorRow, StackFacts
from lintarr.outcomes import Outcome, exit_code
from lintarr.run import run_checks, run_outcome
from tests.fixtures.clients import as_client
from tests.fixtures.homelab import qbt_with, repaired_qbt, wedged_qbt
from tests.fixtures.homelab_paths import arr_with, homelab_arr
from tests.invariants.test_queue_liveness import NO_GOALS, UNCLASSIFIABLE_NO_GOALS

_QBT_ONLY = frozenset({"qbittorrent"})
_QBT_AND_SONARR = frozenset({"qbittorrent", "sonarr"})


#: A banned qBittorrent, with the detail a real one would carry. Named rather
#: than inlined because three tests need the same row, and because a row with
#: an empty detail is a different case with its own test — see
#: ``tests/test_error_detail_route.py``.
_BANNED = ErrorRow(
    "qbittorrent[main]",
    "banned",
    "/api/v2/auth/login: HTTP 403 — wait out WebUI\\BanDuration or restart qBittorrent",
)


def test_one_finding_per_qbittorrent_instance():
    facts = StackFacts(
        clients=(as_client(wedged_qbt("main")), as_client(repaired_qbt("vpn"))), arrs=NO_GOALS
    )
    findings = run_checks(facts, declared=_QBT_AND_SONARR)
    by_instance = {f.instance: f.outcome for f in findings if f.invariant == "queue-liveness"}
    assert by_instance == {"qbittorrent[main]": Outcome.FAIL, "qbittorrent[vpn]": Outcome.PASS}
    # The shared check also runs once per instance. homelab#393's evidence never
    # recorded save paths, so it cannot decide and SKIPs for each.
    hardlink = {f.instance: f.outcome for f in findings if f.invariant == "hardlink-futility"}
    assert hardlink == {"qbittorrent[main]": Outcome.SKIP, "qbittorrent[vpn]": Outcome.SKIP}


def test_collect_errors_become_error_findings():
    facts = StackFacts(clients=(), arrs=(), errors=(_BANNED,))
    findings = run_checks(facts, declared=_QBT_ONLY)
    assert [f.outcome for f in findings] == [Outcome.ERROR]
    assert "banned" in findings[0].detail


def test_a_service_that_errored_is_not_also_reported_as_never_collected():
    """One service, one finding, and the louder one.

    A declared service missing from the snapshot is normally a SKIP, but a
    service that answered and failed already has an ERROR. Reporting both would
    make an operator chase two entries for one outage.
    """
    facts = StackFacts(clients=(), arrs=(), errors=(_BANNED,))
    findings = run_checks(facts, declared=_QBT_ONLY)
    assert [(f.outcome, f.instance) for f in findings] == [(Outcome.ERROR, "qbittorrent[main]")]


def test_an_unreachable_service_does_not_let_the_run_look_clean():
    facts = StackFacts(
        clients=(as_client(repaired_qbt()),),
        arrs=NO_GOALS,
        errors=(ErrorRow("arr[main]", "unreachable", "/api/v3/system/status: ConnectError"),),
    )
    assert run_outcome(run_checks(facts, declared=_QBT_AND_SONARR)) is Outcome.ERROR


@pytest.mark.parametrize("unhealthy_first", [True, False])
def test_a_wedged_instance_alongside_a_healthy_one_fails_the_run(unhealthy_first):
    """Both orders, because ``findings[-1].outcome`` passes only one of them.

    With the wedged instance always constructed last, a run outcome that simply
    returned the final finding would satisfy this test while ignoring every
    other instance in the stack.
    """
    instances = (as_client(wedged_qbt("a")), as_client(repaired_qbt("b")))
    facts = StackFacts(
        clients=instances if unhealthy_first else tuple(reversed(instances)), arrs=NO_GOALS
    )
    findings = run_checks(facts, declared=_QBT_AND_SONARR)
    queue = [f for f in findings if f.invariant == "queue-liveness"]
    assert run_outcome(queue) is Outcome.FAIL
    # The full run also carries hardlink-futility's SKIPs (homelab#393 recorded no
    # save paths), which outrank FAIL in severity but never in the exit code.
    assert exit_code(f.outcome for f in findings) == 1


def test_no_service_declared_and_none_collected_yields_no_findings():
    assert run_checks(StackFacts(clients=(), arrs=()), declared=frozenset()) == ()


def test_a_declared_service_that_never_arrived_is_reported_by_name():
    """ "ARR_URL was never set" must not read as "sonarr is fine".

    Nothing else in the run can see this: no instance was configured, so no
    request failed and no error was recorded. Without this finding the run is
    silent about a service the operator said they had.
    """
    facts = StackFacts(clients=(as_client(repaired_qbt()),), arrs=())
    findings = run_checks(facts, declared=_QBT_AND_SONARR)
    absent = [f for f in findings if f.invariant == "collect"]
    assert [(f.instance, f.outcome) for f in absent] == [("sonarr", Outcome.SKIP)]
    assert "sonarr" in absent[0].detail


def test_a_qbittorrent_only_run_skips_rather_than_passing_the_seeding_conflict():
    """The homelab#393 shape with no arr configured. This must never be PASS."""
    facts = StackFacts(clients=(as_client(wedged_qbt()),), arrs=())
    findings = run_checks(facts, declared=_QBT_AND_SONARR)
    liveness = next(f for f in findings if f.invariant == "queue-liveness")
    assert liveness.outcome is Outcome.SKIP
    assert [p.label for p in liveness.premises] == ["arr.indexer_without_seed_criteria"]


def test_an_undeclared_arr_makes_the_seeding_conflict_not_applicable():
    """ "I have no Sonarr" is a different answer from "I could not read Sonarr".

    Neither is PASS, and telling an operator with no arr that lintarr "could
    not read" one sends them looking for a service they do not run.
    """
    facts = StackFacts(clients=(as_client(wedged_qbt()),), arrs=())
    findings = run_checks(facts, declared=_QBT_ONLY)
    liveness = next(f for f in findings if f.invariant == "queue-liveness")
    assert liveness.outcome is Outcome.NOT_APPLICABLE
    assert "no sonarr or radarr" in liveness.detail


def test_an_arr_that_was_collected_is_never_called_not_configured():
    """The relabel states a cause, and a collected arr disproves it.

    Sonarr is running, answered, and its indexers were read — one of them just
    could not be classified. Consulting ``declared`` alone reported "no sonarr
    or radarr is configured" over facts collected from Sonarr in the same run:
    a conclusion its own premises deny, which is the defect the renderer's
    "Therefore" line had, one layer up.
    """
    facts = StackFacts(clients=(as_client(wedged_qbt()),), arrs=UNCLASSIFIABLE_NO_GOALS)
    liveness = next(
        f for f in run_checks(facts, declared=_QBT_ONLY) if f.invariant == "queue-liveness"
    )
    assert liveness.outcome is Outcome.SKIP
    assert [p.label for p in liveness.premises] == ["arr.indexer_without_seed_criteria"]
    assert "not configured" not in liveness.detail
    assert "no sonarr or radarr" not in liveness.detail


def test_the_three_ways_of_having_no_arr_are_distinguishable():
    """The whole point of wiring ``declared`` through: one shape, three answers."""
    wedged = (as_client(wedged_qbt()),)
    empty_sonarr = (arr_with(homelab_arr(name="main"), indexers=()),)

    def liveness(facts, declared):
        return next(
            f for f in run_checks(facts, declared=declared) if f.invariant == "queue-liveness"
        )

    undeclared = liveness(StackFacts(clients=wedged, arrs=()), _QBT_ONLY)
    declared_absent = liveness(StackFacts(clients=wedged, arrs=()), _QBT_AND_SONARR)
    answered_empty = liveness(StackFacts(clients=wedged, arrs=empty_sonarr), _QBT_AND_SONARR)

    assert undeclared.outcome is Outcome.NOT_APPLICABLE
    assert declared_absent.outcome is Outcome.SKIP
    # An arr that answered with no indexers grabs nothing, so it cannot leave a
    # goal-less torrent seeding: that read happened and it exonerates the arr.
    assert answered_empty.outcome is Outcome.PASS


def test_an_unreadable_qbt_preference_is_not_relabelled_as_a_missing_arr():
    """The N/A substitution must fire only on a skip that is *about* the arrs."""
    facts = StackFacts(
        clients=(
            as_client(qbt_with(max_ratio_enabled=Unknown("field-absent", "max_ratio_enabled"))),
        ),
        arrs=NO_GOALS,
    )
    liveness = next(
        f for f in run_checks(facts, declared=_QBT_ONLY) if f.invariant == "queue-liveness"
    )
    assert liveness.outcome is Outcome.SKIP
    assert [p.label for p in liveness.premises] == ["qbt.no_global_ratio"]


def test_a_backend_arr_dependent_check_gets_the_failed_arr_skip(monkeypatch):
    import dataclasses

    from lintarr.clients import BACKENDS
    from lintarr.config import ClientConfig
    from tests.fakes.backend import FAKE, collect

    fake = dataclasses.replace(FAKE, arr_dependent={"fake-check": "arr.fake_premise"})
    monkeypatch.setitem(BACKENDS, "fake", fake)
    cfg = ClientConfig(name="main", kind="fake", url="http://fake", api_key="k")
    failed = ErrorRow("arr[radarr]", "unreachable", "connection refused")
    facts = StackFacts(clients=(collect(cfg, transport=None),), arrs=(), errors=(failed,))
    (finding,) = [
        f for f in run_checks(facts, declared=frozenset({"fake"})) if f.invariant == "fake-check"
    ]
    assert finding.outcome is Outcome.SKIP
    assert [p.label for p in finding.premises] == ["arr.fake_premise"]
    assert finding.detail == "an arr could not be read, so its downloads could not be checked"
