"""The four properties the spec asks for, generatively.

`hypothesis` has been a declared dev dependency since the first commit with no
property test behind it, which is issue #6's last residual. These are those
tests. Where the spec's wording turned out not to describe a true property of
the shipped code, the true neighbouring property is tested and the difference
is recorded here rather than silently narrowed — see
``test_an_unread_arr_fact_can_still_PASS_and_that_is_deliberate``.

Every subset or "no finding ever" assertion in this file is paired with a
**reachability control**: an aggregate over an empty collection is vacuously
true, so a property that only ever sees PASS findings with no premises would
report green while proving nothing. The controls assert the states being
quantified over are actually produced.
"""

from dataclasses import replace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lintarr.facts import Known, Unknown
from lintarr.invariants import queue_liveness
from lintarr.invariants.queue_liveness import NEEDS, check
from lintarr.models import StackFacts
from lintarr.outcomes import Finding, Outcome
from lintarr.run import run_checks
from tests.fixtures.homelab import qbt_with, repaired_qbt, wedged_qbt
from tests.invariants.test_queue_liveness import NO_GOALS, WITH_GOALS
from tests.strategies import ARR_INSTANCES, DECLARED, READ_AT, STACK_FACTS, qbt_instances

#: Slower than the rest of the suite by design, and allowed to be: these are
#: the only tests here that search rather than assert a named case.
_SETTINGS = settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# ---------------------------------------------------------------------------
# Property 1 — a run never crashes.
# ---------------------------------------------------------------------------


@_SETTINGS
@given(STACK_FACTS, DECLARED)
def test_run_checks_never_raises(facts, declared):
    """No snapshot, however malformed, may abort the run with a traceback.

    This is the property the collect layer's error handling exists to uphold
    from the other side: ``collect_stack`` promises one bad service cannot kill
    the others, and this promises the *predicates* cannot either. A limit read
    back as a string, a category map that is a list, a protocol that is a
    float — all of them reach here, because ``read()`` records whatever the
    service said without inspecting it.
    """
    findings = run_checks(facts, declared=declared)
    assert isinstance(findings, tuple)
    assert all(isinstance(f, Finding) for f in findings)


@_SETTINGS
@given(qbt_instances(), st.lists(ARR_INSTANCES, max_size=2).map(tuple))
def test_check_never_raises(qbt, arrs):
    """The same property one layer down, where the predicates actually live."""
    assert isinstance(check(qbt, arrs).outcome, Outcome)


def test_a_malformed_snapshot_is_actually_reachable():
    """Reachability control for the two properties above.

    Without this, a strategy that silently degraded to generating only
    well-formed facts would leave both tests passing over an input class that
    no longer contains anything interesting.
    """
    a_limit_read_as_a_string = Known(
        value="5", source="GET /generated", read_at=READ_AT, service_version="v5.2.4"
    )
    junk = qbt_with(max_active_torrents=a_limit_read_as_a_string)
    assert check(junk, NO_GOALS).outcome is Outcome.SKIP


# ---------------------------------------------------------------------------
# Property 2 — an unread required fact must not produce PASS.
# ---------------------------------------------------------------------------

#: The ``NEEDS`` entries that name a single qBittorrent preference, mapped to
#: the ``QbtInstance`` field each is read from. Written by hand for the same
#: reason ``test_needs_are_load_bearing._PAIRS`` is: deriving it from the
#: implementation would make the test agree with whatever the code does.
_QBT_NEED_FIELDS: dict[str, str] = {
    "qbt.queueing_enabled": "queueing_enabled",
    "qbt.max_active_downloads": "max_active_downloads",
    "qbt.max_active_torrents": "max_active_torrents",
    "qbt.dont_count_slow_torrents": "dont_count_slow_torrents",
    "qbt.max_ratio_enabled": "max_ratio_enabled",
    "qbt.max_seeding_time_enabled": "max_seeding_time_enabled",
    "qbt.categories": "categories",
}

#: A fact that was never read at all. Unambiguous for every need.
_NEVER_READ = Unknown(reason="field-absent", detail="generated")

#: A fact read back as ``null``. For six of the seven needs this is a value we
#: cannot use and must not settle a premise from. ``qbt.categories`` is the
#: documented exception and is excluded below.
_READ_AS_NULL = Known(
    value=None,
    source="GET /generated",
    read_at=READ_AT,
    service_version="v5.2.4",
)

#: The one need for which ``null`` is information rather than a gap: a client
#: reporting a null category map has no categories, so none of them can
#: override the global share limits. Measured and pinned by
#: ``test_a_null_category_map_is_no_categories_not_an_unknown``; this name is
#: what keeps the generative property below from asserting the opposite.
_NULL_IS_A_VALUE: frozenset[str] = frozenset({"qbt.categories"})


def test_every_qbt_need_is_covered_by_the_table():
    """A need added to NEEDS with no entry here would escape the property."""
    uncovered = [n for n in NEEDS if n.startswith("qbt.") and n not in _QBT_NEED_FIELDS]
    assert not uncovered, f"qbt NEEDS entries absent from _QBT_NEED_FIELDS: {uncovered}"
    stale = [n for n in _QBT_NEED_FIELDS if n not in NEEDS]
    assert not stale, f"covered here but no longer in NEEDS: {stale}"


@given(
    st.sets(st.sampled_from(sorted(_QBT_NEED_FIELDS)), min_size=1),
    st.sampled_from([NO_GOALS, WITH_GOALS]),
)
@settings(max_examples=200, deadline=None)
def test_a_qbt_preference_that_was_never_read_never_passes(needs, arrs):
    """Every qBittorrent-side need is load-bearing against PASS.

    Both conflicts are evaluated and combined as a disjunction, so each of
    these facts is required by at least one of them; an unread one must
    surface as SKIP (or as a FAIL the *other* conflict proved without it), and
    never as a clean bill of health. The healthy ``repaired_qbt`` base is what
    makes this sharp — on a wedged base every outcome is FAIL anyway and the
    property would hold for the wrong reason.
    """
    overrides = {_QBT_NEED_FIELDS[n]: _NEVER_READ for n in needs}
    outcome = check(qbt_with(**overrides), arrs).outcome
    assert outcome is not Outcome.PASS, f"PASS with {sorted(needs)} never read"


@given(
    st.sets(st.sampled_from(sorted(set(_QBT_NEED_FIELDS) - _NULL_IS_A_VALUE)), min_size=1),
    st.sampled_from([NO_GOALS, WITH_GOALS]),
)
@settings(max_examples=200, deadline=None)
def test_a_qbt_preference_read_as_null_never_passes(needs, arrs):
    """A key present and null is a value we cannot use, not a value.

    Distinct from the property above, and not merely a second spelling of it:
    ``facts.py`` keeps "never read" and "read back as null" apart on purpose,
    so each has to be shown separately to be load-bearing. Coercing a null
    through ``bool()`` or ``int()`` would settle a premise from a setting
    nobody ever set, which is the defaulting this project refuses.

    ``qbt.categories`` is excluded — see ``_NULL_IS_A_VALUE`` and the test
    below it, which is where that exception is stated rather than assumed.
    """
    overrides = {_QBT_NEED_FIELDS[n]: _READ_AS_NULL for n in needs}
    outcome = check(qbt_with(**overrides), arrs).outcome
    assert outcome is not Outcome.PASS, f"PASS with {sorted(needs)} read as null"


def test_a_null_category_map_is_the_one_null_that_can_still_pass():
    """The exception ``_NULL_IS_A_VALUE`` names, asserted rather than assumed.

    Found by the generative property above, which failed on exactly this case
    before the exclusion existed. It is not a defect: a client reporting a
    null category map has no categories, so none of them can carry a share
    limit of their own, and ``_own_share_limit``'s docstring records what
    reading that as unknown cost — a permanent SKIP for every stack using
    qBittorrent's default categories, including homelab#393 itself.

    Here so that the exclusion in the property cannot quietly widen: if a
    second need ever joins ``_NULL_IS_A_VALUE``, this test is where it has to
    justify itself.
    """
    assert _NULL_IS_A_VALUE == {"qbt.categories"}
    assert check(qbt_with(categories=_READ_AS_NULL), WITH_GOALS).outcome is Outcome.PASS
    # And the contrast that makes it an exception rather than a blanket rule:
    # never read at all is still undecidable.
    assert check(qbt_with(categories=_NEVER_READ), WITH_GOALS).outcome is Outcome.SKIP


def test_the_healthy_base_really_does_pass():
    """Reachability control for the property above.

    If ``repaired_qbt`` + ``WITH_GOALS`` had stopped passing, every assertion
    in that property would hold trivially and tell us nothing about the needs.
    """
    assert check(repaired_qbt(), WITH_GOALS).outcome is Outcome.PASS


def test_an_unread_arr_fact_can_still_PASS_and_that_is_deliberate():
    """The spec's property 2 is false for the three ``arr.*`` needs, by design.

    The spec says "never PASS with an unknown required fact". Two shapes break
    it, and both are correct:

    * An indexer with seed goals set is dropped from the predicate before its
      protocol is ever consulted — with goals set it cannot leave a torrent
      seeding whatever protocol it speaks, so an unreadable protocol changes
      nothing. ``_indexer_without_seed_criteria`` says as much.
    * ``_lacks_seed_criteria`` is deliberately total rather than three-valued:
      Sonarr reports "no goal set" by omitting the value, so an ``Unknown``
      seed criterion is the ordinary case and not a gap. Treating it as
      undecidable would SKIP on essentially every real stack, including the
      one that motivated the project.

    Encoded as a test rather than left as a comment so that *narrowing* the
    property later is a deliberate act with a failing test behind it.
    """
    with_goals = WITH_GOALS[0].indexers[0]
    unreadable_protocol = replace(
        WITH_GOALS[0],
        indexers=(replace(with_goals, protocol=Unknown("field-absent", "protocol")),),
    )
    assert check(repaired_qbt(), (unreadable_protocol,)).outcome is Outcome.PASS


# ---------------------------------------------------------------------------
# Property 3 — every FAIL carries a non-empty premise set.
# ---------------------------------------------------------------------------


@_SETTINGS
@given(qbt_instances(), st.lists(ARR_INSTANCES, max_size=2).map(tuple))
def test_every_fail_explains_itself(qbt, arrs):
    """A FAIL with no premises is an accusation with no evidence.

    The premise set *is* the explanation in this design — there is no separate
    reasoning trace to fall back on — so an empty one means the CLI prints a
    conflict an operator cannot check. The same holds for SKIP: "could not
    read" has to say what.
    """
    finding = check(qbt, arrs)
    if finding.outcome in (Outcome.FAIL, Outcome.SKIP):
        assert finding.premises, f"{finding.outcome} with no premises"
        assert finding.conflict, f"{finding.outcome} naming no conflict"


@pytest.mark.parametrize(
    ("qbt", "arrs", "expected"),
    [
        (wedged_qbt(), NO_GOALS, Outcome.FAIL),
        (qbt_with(categories=Unknown("field-absent", "categories")), NO_GOALS, Outcome.SKIP),
    ],
)
def test_both_explained_outcomes_are_reachable(qbt, arrs, expected):
    """Reachability control: the property above quantifies over FAIL and SKIP.

    A generator drift that only ever produced PASS would leave it green having
    checked nothing, which is the aggregate-over-empty trap this file opens by
    naming.
    """
    finding = check(qbt, arrs)
    assert finding.outcome is expected
    assert finding.premises


# ---------------------------------------------------------------------------
# Property 4 — every premise a finding reports is a declared one.
# ---------------------------------------------------------------------------

#: Every premise label ``queue_liveness`` may emit. Hand-written: a catalogue
#: derived from the implementation cannot disagree with it.
#:
#: The spec asks for "premises are a subset of declared needs ∪ uses", which is
#: not checkable as written — a premise label is a *derived* name
#: (``qbt.no_global_ratio``), not a need (``qbt.max_ratio_enabled``), and the
#: code publishes no mapping between the two. Closing the label set is the
#: checkable neighbour: it catches a premise invented at runtime, a typo, and a
#: premise added without being declared anywhere.
_PREMISE_LABELS: frozenset[str] = frozenset(
    {
        "qbt.queueing_enabled",
        "qbt.no_slot_for_a_first_download",
        "qbt.max_active_torrents_binds",
        "qbt.slow_exempt_off",
        "qbt.no_global_ratio",
        "qbt.no_global_seed_time",
        "qbt.no_category_limits",
        "arr.indexer_without_seed_criteria",
    }
)


@_SETTINGS
@given(qbt_instances(), st.lists(ARR_INSTANCES, max_size=2).map(tuple))
def test_no_finding_reports_an_undeclared_premise(qbt, arrs):
    labels = {p.label for p in check(qbt, arrs).premises}
    undeclared = sorted(labels - _PREMISE_LABELS)
    assert not undeclared, f"undeclared premise labels: {undeclared}"


@_SETTINGS
@given(qbt_instances(), st.lists(ARR_INSTANCES, max_size=2).map(tuple))
def test_a_findings_premise_labels_are_unique(qbt, arrs):
    """``conflict_if`` raises on a duplicate label; nothing may reach that."""
    labels = [p.label for p in check(qbt, arrs).premises]
    assert len(labels) == len(set(labels)), f"duplicate premise labels: {labels}"


def test_every_declared_premise_label_is_reachable():
    """Reachability control, and the one that matters most in this file.

    ``labels <= _PREMISE_LABELS`` is true for the empty set, so the property
    above passes unchanged if ``check`` stopped reporting premises entirely.
    Asserting the catalogue is *exhausted* by two known configurations is what
    makes the subset check mean something — and it is the reverse direction
    too: a label deleted from the code but left here fails on this test.
    """
    seen: set[str] = set()
    for qbt, arrs in (
        (wedged_qbt(), NO_GOALS),
        (qbt_with(categories=Unknown("field-absent", "categories")), NO_GOALS),
        (qbt_with(max_active_torrents=0), NO_GOALS),
    ):
        seen |= {p.label for p in check(qbt, arrs).premises}
    assert seen == _PREMISE_LABELS, (
        f"never reported: {sorted(_PREMISE_LABELS - seen)}; "
        f"reported but undeclared: {sorted(seen - _PREMISE_LABELS)}"
    )


def test_the_conflict_names_are_the_two_the_cli_explains():
    """A third conflict added without a "Therefore" line prints a bare FAIL.

    Not one of the spec's four, but the same class: the CLI keys its
    explanation on ``(invariant, conflict)``, so the set of conflicts a finding
    can carry has to stay in step with the set the CLI can explain.
    """
    from lintarr.cli import _THEREFORE

    explained = {c for inv, c in _THEREFORE if inv == queue_liveness.INVARIANT_ID}
    assert explained == {queue_liveness.STARVATION, queue_liveness.SEEDING}


@_SETTINGS
@given(STACK_FACTS, DECLARED)
def test_a_findings_instance_is_never_empty(facts, declared):
    """Every finding has to say which instance it is about.

    An operator with a 4K and an anime Sonarr cannot act on a verdict that
    does not name one, and ``StackFacts`` is multi-instance from day one.
    """
    for finding in run_checks(facts, declared=declared):
        assert finding.instance, f"{finding.invariant} finding with no instance"
        assert finding.invariant


def test_findings_are_reachable_from_a_generated_snapshot():
    """Reachability control for the property above, which loops over findings."""
    facts = StackFacts(qbits=(wedged_qbt(),), arrs=NO_GOALS, errors=(("sonarr[x]", "unreachable"),))
    findings = run_checks(facts, declared=frozenset({"radarr"}))
    assert len(findings) >= 3, [f.invariant for f in findings]
