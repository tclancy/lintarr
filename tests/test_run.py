from lintarr.models import StackFacts
from lintarr.outcomes import Outcome
from lintarr.run import run_checks, run_outcome
from tests.fixtures.homelab import repaired_qbt, wedged_qbt
from tests.invariants.test_queue_liveness import NO_GOALS


def test_one_finding_per_qbittorrent_instance():
    facts = StackFacts(qbits=(wedged_qbt("main"), repaired_qbt("vpn")), arrs=NO_GOALS)
    findings = run_checks(facts)
    by_instance = {f.instance: f.outcome for f in findings}
    assert by_instance["qbittorrent[main]"] is Outcome.FAIL
    assert by_instance["qbittorrent[vpn]"] is Outcome.PASS


def test_collect_errors_become_error_findings():
    facts = StackFacts(qbits=(), arrs=(), errors=(("qbittorrent[main]", "banned"),))
    findings = run_checks(facts)
    assert [f.outcome for f in findings] == [Outcome.ERROR]
    assert "banned" in findings[0].detail


def test_an_unreachable_service_does_not_let_the_run_look_clean():
    facts = StackFacts(
        qbits=(repaired_qbt(),), arrs=NO_GOALS, errors=(("sonarr[main]", "unreachable"),)
    )
    assert run_outcome(run_checks(facts)) is Outcome.ERROR


def test_a_wedged_instance_alongside_a_healthy_one_fails_the_run():
    facts = StackFacts(qbits=(repaired_qbt("a"), wedged_qbt("b")), arrs=NO_GOALS)
    assert run_outcome(run_checks(facts)) is Outcome.FAIL


def test_no_qbittorrent_configured_yields_no_findings():
    assert run_checks(StackFacts(qbits=(), arrs=())) == ()
