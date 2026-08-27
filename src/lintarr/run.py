"""Run every invariant over a collected snapshot.

Findings are per-instance: a stack with one healthy and one wedged download
client must show both, not an aggregate that hides either.
"""

from collections.abc import Iterable

from lintarr.invariants import queue_liveness
from lintarr.models import StackFacts
from lintarr.outcomes import Finding, Outcome, worst


def run_checks(facts: StackFacts) -> tuple[Finding, ...]:
    findings = [queue_liveness.check(qbt, facts.arrs) for qbt in facts.qbits]
    findings += [
        Finding(
            invariant="collect",
            instance=instance,
            outcome=Outcome.ERROR,
            detail=f"could not read this service: {kind}",
        )
        for instance, kind in facts.errors
    ]
    return tuple(findings)


def run_outcome(findings: Iterable[Finding]) -> Outcome:
    return worst(f.outcome for f in findings)
