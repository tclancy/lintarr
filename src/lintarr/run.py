"""Run every invariant over a collected snapshot.

Findings are per-instance: a stack with one healthy and one wedged download
client must show both, not an aggregate that hides either.

This layer is also the only one that knows which services were *declared*.
An invariant sees facts, so "the operator has no Sonarr", "ARR_URL was
never set" and "Sonarr answered but we could not use its answer" all reach it
as the same empty tuple. Left there they collapse into one verdict, and the
cheapest verdict to reach is PASS — a green run over a service nobody looked
at, which is the worst thing this tool can produce. The declared set is what
tells them apart.
"""

from collections.abc import Iterable
from dataclasses import replace

from lintarr import checks
from lintarr.checks import hardlink_futility
from lintarr.clients import BACKENDS
from lintarr.collect.arr import ARR_KINDS, ARR_LABEL_KIND
from lintarr.config import LintarrConfig
from lintarr.models import ClientInstance, ErrorRow, StackFacts
from lintarr.outcomes import Finding, Outcome, Premise, worst

#: The premise that has no answer without arr data. Named here so the
#: substitution below fires only on a SKIP that is actually about missing arrs,
#: and never relabels a SKIP caused by an unreadable qBittorrent preference.
_ARR_PREMISE = "arr.indexer_without_seed_criteria"


def _error_detail(row: ErrorRow) -> str:
    """The operator-facing sentence for a service that could not be read.

    The kind leads because it is the stable, matchable half. The detail is
    appended only when there is one: ``ServiceError`` does not forbid an empty
    detail, and an unconditional separator renders as truncated output.

    Joined with a full stop rather than a dash. The details are not all terse
    locators — qBittorrent's 403 note is a sentence containing its own em dash —
    and ``kind — detail — clause`` leaves a reader guessing which dash was the
    join. It stays one line because ``Finding.detail`` is also emitted into
    ``check --json``, and ``_render_findings`` indents it with a bare two
    spaces, so an embedded newline would render unindented in one surface and
    leak a newline into the other.
    """
    head = f"could not read this service: {row.kind}"
    return f"{head}. {row.detail}" if row.detail else head


def _label_kind(row: ErrorRow) -> str:
    """Error labels are ``kind[name]``; the kind is what a declaration names."""
    return row.label.split("[")[0]


def _an_arr_failed(facts: StackFacts) -> bool:
    return any(_label_kind(row) == ARR_LABEL_KIND for row in facts.errors)


def declared_services(cfg: LintarrConfig, facts: StackFacts) -> frozenset[str]:
    """The services this run is answerable for.

    ``LINTARR_SERVICES`` wins when set. Otherwise it is every configured client
    kind, every *detected* arr kind, and the placeholder ``arr`` when an arr
    failed before its kind was read — that arr already reports an ERROR, so the
    placeholder only keeps it counted, never silent.
    """
    if cfg.declared_explicit is not None:
        return cfg.declared_explicit
    declared = {client.kind for client in cfg.clients} | {arr.kind for arr in facts.arrs}
    if _an_arr_failed(facts):
        declared.add(ARR_LABEL_KIND)
    return frozenset(declared)


def _collected(facts: StackFacts) -> frozenset[str]:
    """The services that actually produced usable facts."""
    present = {client.kind for client in facts.clients}
    return frozenset(present | {arr.kind for arr in facts.arrs})


def _attempted(facts: StackFacts) -> frozenset[str]:
    """The services a collection was tried against and failed on.

    A failed arr never said which arr it was, so its attempt covers every arr
    kind: ``LINTARR_SERVICES=sonarr`` beside an unreachable arr is one ERROR,
    not an ERROR and a "never collected" SKIP for the same service.
    """
    attempted = {_label_kind(row) for row in facts.errors}
    if ARR_LABEL_KIND in attempted:
        attempted |= ARR_KINDS
    return frozenset(attempted)


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


def _resolve_missing_arrs(
    finding: Finding, facts: StackFacts, *, declared: frozenset[str]
) -> Finding:
    """Say *why* an arr-shaped SKIP had no arr data, and when it is not a SKIP at all.

    Only a SKIP whose single unknown premise is the arr one is touched, so a
    run that also failed to read a qBittorrent preference keeps reporting that
    instead. With no arr declared anywhere, the seeding conflict is not
    unreadable but inapplicable: there is no service that could ever answer it,
    and reporting "could not read" for a service the operator does not run
    tells them to go fix nothing.

    ``facts.arrs`` is consulted alongside *declared* because the relabel states
    a cause — "no sonarr or radarr is configured" — and a collected arr
    disproves it outright. An arr that answered but whose indexers could not be
    classified reaches this function as the same arr-shaped SKIP, and telling
    the operator the service they are running is not configured is the same
    defect as a "Therefore" line contradicting its own premises. A failed arr
    disproves it just as well, and carries no kind for *declared* to name.
    """
    if finding.outcome is not Outcome.SKIP:
        return finding
    if {p.label for p in finding.premises} != {_ARR_PREMISE}:
        return finding
    if declared & ARR_KINDS or facts.arrs or _an_arr_failed(facts):
        return finding
    return replace(
        finding,
        outcome=Outcome.NOT_APPLICABLE,
        detail="no sonarr or radarr is configured, so no indexer's seed criteria "
        "can be read — this conflict needs them and cannot be decided",
    )


def _resolve_failed_arr(finding: Finding, facts: StackFacts) -> Finding:
    """A ``hardlink-futility`` PASS or N/A is a SKIP when an arr failed to collect.

    The check never sees collection errors, and an arr that could not be read
    cannot be ruled out: it may import across mounts. A FAIL found elsewhere
    stands, and so does a SKIP the check reached itself.
    """
    if finding.outcome not in (Outcome.PASS, Outcome.NOT_APPLICABLE):
        return finding
    if not _an_arr_failed(facts):
        return finding
    return replace(
        finding,
        outcome=Outcome.SKIP,
        premises=(Premise(hardlink_futility.PREMISE, None),),
        detail="an arr could not be read, so its imports could not be checked",
    )


def _resolve(finding: Finding, facts: StackFacts, *, declared: frozenset[str]) -> Finding:
    """Every run-level correction that applies to *finding*."""
    finding = _resolve_missing_arrs(finding, facts, declared=declared)
    if finding.invariant == hardlink_futility.INVARIANT_ID:
        finding = _resolve_failed_arr(finding, facts)
    return finding


def _client_findings(
    instance: ClientInstance, facts: StackFacts, *, declared: frozenset[str]
) -> list[Finding]:
    """Every check that applies to *instance*: its backend's own, then the shared ones."""
    return [
        _resolve(check(instance, facts.arrs), facts, declared=declared)
        for check in (*BACKENDS[instance.kind].checks, *checks.SHARED_CHECKS)
    ]


def run_checks(facts: StackFacts, *, declared: frozenset[str]) -> tuple[Finding, ...]:
    """Every finding this snapshot supports.

    *declared* has no default on purpose. A caller that has not thought about
    which services were meant to be present is exactly the caller that gets a
    vacuous PASS, so it has to say.
    """
    findings = [
        finding
        for instance in facts.clients
        for finding in _client_findings(instance, facts, declared=declared)
    ]
    findings += _absent_service_findings(facts, declared)
    findings += [
        Finding(
            invariant="collect",
            instance=row.label,
            outcome=Outcome.ERROR,
            detail=_error_detail(row),
        )
        for row in facts.errors
    ]
    return tuple(findings)


def run_outcome(findings: Iterable[Finding]) -> Outcome:
    return worst(f.outcome for f in findings)
