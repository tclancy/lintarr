import pytest

from lintarr.facts import Unknown
from lintarr.models import ArrInstance, StackFacts
from lintarr.outcomes import Outcome
from lintarr.run import run_checks, run_outcome
from tests.fixtures.homelab import qbt_with, repaired_qbt, wedged_qbt
from tests.invariants.test_queue_liveness import NO_GOALS, UNCLASSIFIABLE_NO_GOALS

_QBT_ONLY = frozenset({"qbittorrent"})
_QBT_AND_SONARR = frozenset({"qbittorrent", "sonarr"})


def test_one_finding_per_qbittorrent_instance():
    facts = StackFacts(qbits=(wedged_qbt("main"), repaired_qbt("vpn")), arrs=NO_GOALS)
    findings = run_checks(facts, declared=_QBT_AND_SONARR)
    by_instance = {f.instance: f.outcome for f in findings}
    assert by_instance["qbittorrent[main]"] is Outcome.FAIL
    assert by_instance["qbittorrent[vpn]"] is Outcome.PASS


def test_collect_errors_become_error_findings():
    facts = StackFacts(qbits=(), arrs=(), errors=(("qbittorrent[main]", "banned"),))
    findings = run_checks(facts, declared=_QBT_ONLY)
    assert [f.outcome for f in findings] == [Outcome.ERROR]
    assert "banned" in findings[0].detail


def test_a_service_that_errored_is_not_also_reported_as_never_collected():
    """One service, one finding, and the louder one.

    A declared service missing from the snapshot is normally a SKIP, but a
    service that answered and failed already has an ERROR. Reporting both would
    make an operator chase two entries for one outage.
    """
    facts = StackFacts(qbits=(), arrs=(), errors=(("qbittorrent[main]", "banned"),))
    findings = run_checks(facts, declared=_QBT_ONLY)
    assert [(f.outcome, f.instance) for f in findings] == [(Outcome.ERROR, "qbittorrent[main]")]


def test_an_unreachable_service_does_not_let_the_run_look_clean():
    facts = StackFacts(
        qbits=(repaired_qbt(),), arrs=NO_GOALS, errors=(("sonarr[main]", "unreachable"),)
    )
    assert run_outcome(run_checks(facts, declared=_QBT_AND_SONARR)) is Outcome.ERROR


@pytest.mark.parametrize("unhealthy_first", [True, False])
def test_a_wedged_instance_alongside_a_healthy_one_fails_the_run(unhealthy_first):
    """Both orders, because ``findings[-1].outcome`` passes only one of them.

    With the wedged instance always constructed last, a run outcome that simply
    returned the final finding would satisfy this test while ignoring every
    other instance in the stack.
    """
    instances = (wedged_qbt("a"), repaired_qbt("b"))
    facts = StackFacts(
        qbits=instances if unhealthy_first else tuple(reversed(instances)), arrs=NO_GOALS
    )
    assert run_outcome(run_checks(facts, declared=_QBT_AND_SONARR)) is Outcome.FAIL


def test_no_service_declared_and_none_collected_yields_no_findings():
    assert run_checks(StackFacts(qbits=(), arrs=()), declared=frozenset()) == ()


def test_a_declared_service_that_never_arrived_is_reported_by_name():
    """ "SONARR_URL was never set" must not read as "sonarr is fine".

    Nothing else in the run can see this: no instance was configured, so no
    request failed and no error was recorded. Without this finding the run is
    silent about a service the operator said they had.
    """
    facts = StackFacts(qbits=(repaired_qbt(),), arrs=())
    findings = run_checks(facts, declared=_QBT_AND_SONARR)
    absent = [f for f in findings if f.invariant == "collect"]
    assert [(f.instance, f.outcome) for f in absent] == [("sonarr", Outcome.SKIP)]
    assert "sonarr" in absent[0].detail


def test_a_qbittorrent_only_run_skips_rather_than_passing_the_seeding_conflict():
    """The homelab#393 shape with no arr configured. This must never be PASS."""
    facts = StackFacts(qbits=(wedged_qbt(),), arrs=())
    findings = run_checks(facts, declared=_QBT_AND_SONARR)
    liveness = next(f for f in findings if f.invariant == "queue-liveness")
    assert liveness.outcome is Outcome.SKIP
    assert [p.label for p in liveness.premises] == ["arr.indexer_without_seed_criteria"]


def test_an_undeclared_arr_makes_the_seeding_conflict_not_applicable():
    """ "I have no Sonarr" is a different answer from "I could not read Sonarr".

    Neither is PASS, and telling an operator with no arr that lintarr "could
    not read" one sends them looking for a service they do not run.
    """
    facts = StackFacts(qbits=(wedged_qbt(),), arrs=())
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
    facts = StackFacts(qbits=(wedged_qbt(),), arrs=UNCLASSIFIABLE_NO_GOALS)
    liveness = next(
        f for f in run_checks(facts, declared=_QBT_ONLY) if f.invariant == "queue-liveness"
    )
    assert liveness.outcome is Outcome.SKIP
    assert [p.label for p in liveness.premises] == ["arr.indexer_without_seed_criteria"]
    assert "not configured" not in liveness.detail
    assert "no sonarr or radarr" not in liveness.detail


def test_the_three_ways_of_having_no_arr_are_distinguishable():
    """The whole point of wiring ``declared`` through: one shape, three answers."""
    wedged = (wedged_qbt(),)
    empty_sonarr = (ArrInstance(name="main", kind="sonarr", version="4.0.19", indexers=()),)

    def liveness(facts, declared):
        return next(
            f for f in run_checks(facts, declared=declared) if f.invariant == "queue-liveness"
        )

    undeclared = liveness(StackFacts(qbits=wedged, arrs=()), _QBT_ONLY)
    declared_absent = liveness(StackFacts(qbits=wedged, arrs=()), _QBT_AND_SONARR)
    answered_empty = liveness(StackFacts(qbits=wedged, arrs=empty_sonarr), _QBT_AND_SONARR)

    assert undeclared.outcome is Outcome.NOT_APPLICABLE
    assert declared_absent.outcome is Outcome.SKIP
    # An arr that answered with no indexers grabs nothing, so it cannot leave a
    # goal-less torrent seeding: that read happened and it exonerates the arr.
    assert answered_empty.outcome is Outcome.PASS


def test_an_unreadable_qbt_preference_is_not_relabelled_as_a_missing_arr():
    """The N/A substitution must fire only on a skip that is *about* the arrs."""
    facts = StackFacts(
        qbits=(qbt_with(max_ratio_enabled=Unknown("field-absent", "max_ratio_enabled")),),
        arrs=NO_GOALS,
    )
    liveness = next(
        f for f in run_checks(facts, declared=_QBT_ONLY) if f.invariant == "queue-liveness"
    )
    assert liveness.outcome is Outcome.SKIP
    assert [p.label for p in liveness.premises] == ["qbt.no_global_ratio"]
