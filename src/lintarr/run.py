"""Run every invariant over a collected snapshot.

Findings are per-instance: a stack with one healthy and one wedged download
client must show both, not an aggregate that hides either.

This layer is also the only one that knows which services were *declared*.
An invariant sees facts, so "the operator has no Sonarr", "SONARR_URL was
never set" and "Sonarr answered but we could not use its answer" all reach it
as the same empty tuple. Left there they collapse into one verdict, and the
cheapest verdict to reach is PASS — a green run over a service nobody looked
at, which is the worst thing this tool can produce. The declared set is what
tells them apart.
"""

from collections.abc import Iterable
from dataclasses import replace

from lintarr.invariants import queue_liveness
from lintarr.models import StackFacts
from lintarr.outcomes import Finding, Outcome, worst

#: The services an ``arr.*`` premise can be read from.
ARR_KINDS = frozenset({"sonarr", "radarr"})

#: The premise that has no answer without arr data. Named here so the
#: substitution below fires only on a SKIP that is actually about missing arrs,
#: and never relabels a SKIP caused by an unreadable qBittorrent preference.
_ARR_PREMISE = "arr.indexer_without_seed_criteria"


def _collected(facts: StackFacts) -> frozenset[str]:
    """The services that actually produced usable facts."""
    present = {"qbittorrent"} if facts.qbits else set()
    return frozenset(present | {arr.kind for arr in facts.arrs})


def _attempted(facts: StackFacts) -> frozenset[str]:
    """The services a collection was tried against and failed on.

    Error labels are ``kind[name]``; the kind is what a declaration names.
    """
    return frozenset(label.split("[")[0] for label, _ in facts.errors)


def _absent_service_findings(facts: StackFacts, declared: frozenset[str]) -> list[Finding]:
    """One SKIP per declared service that never turned up in the snapshot.

    A service that failed to answer already produces an ERROR finding from
    ``facts.errors`` and is excluded here, so it is reported once and with the
    louder outcome. This covers the quieter case: declared, not configured,
    never attempted, and so invisible to every other layer.
    """
    return [
        Finding(
            invariant="collect",
            instance=service,
            outcome=Outcome.SKIP,
            detail=f"{service} was declared but never collected — nothing was read from it",
        )
        for service in sorted(declared - _collected(facts) - _attempted(facts))
    ]


def _resolve_missing_arrs(finding: Finding, declared: frozenset[str]) -> Finding:
    """Say *why* an arr-shaped SKIP had no arr data, and when it is not a SKIP at all.

    Only a SKIP whose single unknown premise is the arr one is touched, so a
    run that also failed to read a qBittorrent preference keeps reporting that
    instead. With no arr declared anywhere, the seeding conflict is not
    unreadable but inapplicable: there is no service that could ever answer it,
    and reporting "could not read" for a service the operator does not run
    tells them to go fix nothing.
    """
    if finding.outcome is not Outcome.SKIP:
        return finding
    if {p.label for p in finding.premises} != {_ARR_PREMISE}:
        return finding
    if declared & ARR_KINDS:
        return finding
    return replace(
        finding,
        outcome=Outcome.NOT_APPLICABLE,
        detail="no sonarr or radarr is configured, so no indexer's seed criteria "
        "can be read — this conflict needs them and cannot be decided",
    )


def run_checks(facts: StackFacts, *, declared: frozenset[str]) -> tuple[Finding, ...]:
    """Every finding this snapshot supports.

    *declared* has no default on purpose. A caller that has not thought about
    which services were meant to be present is exactly the caller that gets a
    vacuous PASS, so it has to say.
    """
    findings = [
        _resolve_missing_arrs(queue_liveness.check(qbt, facts.arrs), declared)
        for qbt in facts.qbits
    ]
    findings += _absent_service_findings(facts, declared)
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
