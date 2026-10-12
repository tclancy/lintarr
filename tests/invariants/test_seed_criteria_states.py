"""Does lintarr file a seed criterion in the state qBittorrent resolves it to?

The soundness property the suite did not have. ``tests/test_properties.py``
holds four properties — a run never crashes, an unread need never PASSes, every
FAIL explains itself, every premise is declared — and not one of them is about
whether a verdict is *right*. All three of lintarr#10, #14 and #28 were false
PASSes that cleared every one of them, because each was a seed-criterion value
filed under the wrong state while the machinery around it behaved impeccably.

So this file compares ``queue_liveness``'s classification of a criterion against
``tests/model/share_limit``, which is the measurement doc executable and knows
nothing about ``queue_liveness``. Level 1 validation on the value axis, exactly
as ``test_queue_liveness_sweep.py`` is on the slot-count axis — and with the
same limit: agreeing with the model cannot discover a state the model is also
missing. What it does discover is a value in the wrong state, which is the
entire false-PASS family to date.

Why generative rather than a parametrize over the measurement doc's table: both,
and they do different jobs. ``test_the_measurement_docs_table_still_reads_as_written``
anchors the model to the document. The ``@given`` property is what covers the
values nobody thought to tabulate — ``-0.0``, ``2**1400``, ``inf`` as a seed
time, a dict where a ratio belongs — which is where #10 and #14 actually lived.
"""

import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lintarr.facts import Fact, Known, Unknown
from lintarr.invariants.queue_liveness import (
    _is_a_seed_goal,
    _is_an_unusable_seed_criterion,
    _overrides_the_share_limit,
)
from tests.model.share_limit import CriterionState, classify
from tests.strategies import JUNK_VALUES, LIMIT_VALUES, READ_AT

#: Searches rather than asserts a named case, so it is allowed to be slower
#: than the rest of the suite — the same allowance ``test_properties`` takes.
_SETTINGS = settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

#: The values the existing strategies cannot draw, each a measured case rather
#: than a curiosity. ``inf``/``nan`` override (``inf != -2`` so the redirect
#: never fires, and ``realRatio() >= inf`` is never true) and ``JUNK_VALUES``
#: builds its floats with ``allow_nan=False, allow_infinity=False``. The huge
#: ints are the ``OverflowError`` path both sides have to survive: ``json``
#: decodes a 401-digit literal to a Python ``int``, and ``math.isfinite`` raises
#: on one instead of answering. ``-0.0`` is a goal and reads like a negative.
MEASURED_EDGE_VALUES: tuple[float | int, ...] = (
    math.inf,
    -math.inf,
    math.nan,
    -0.0,
    0.0,
    2**1400,
    -(2**1400),
)

#: Everything a ``seedCriteria`` field has been seen or reasoned to carry.
#: Deliberately a union of the two existing strategies plus the edge values, so
#: narrowing either of those narrows this too and the breadth control below is
#: what notices.
CRITERION_VALUES = st.one_of(
    LIMIT_VALUES,
    JUNK_VALUES,
    st.sampled_from(MEASURED_EDGE_VALUES),
    st.floats(allow_nan=True, allow_infinity=True, width=32),
)


def _known(value: object) -> Known:
    return Known(
        value=value, source="GET /api/v3/indexer", read_at=READ_AT, service_version="4.0.0"
    )


#: A criterion the arr never reported. Sonarr omits the ``value`` key entirely
#: when no goal is set, so this is the ordinary case and not a gap.
_NEVER_READ = Unknown(reason="field-absent", detail="seedCriteria.seedRatio absent")


def lintarr_state(fact: Fact[object]) -> CriterionState:
    """Read ``queue_liveness``'s classification back out of its predicates.

    The three predicates are the public face of the classification even though
    they are underscore-private: ``_lacks_seed_criteria``,
    ``_overrides_both_share_limits`` and the two single-axis override routes are
    all built from exactly these, so a value misfiled here is misfiled in every
    conflict at once.

    **The question order is load-bearing and is why this adapter exists at all.**
    ``_is_an_unusable_seed_criterion`` is asked first, before
    ``_overrides_the_share_limit`` is consulted, because the two sides of
    ``queue_liveness`` want opposite defaults for an unreadable criterion and
    the file says so in its own module docstring. Asking in this order also
    makes the adapter stable across lintarr#33: PR #40 turns
    ``_overrides_the_share_limit`` three-valued, returning ``None`` for exactly
    the unusable case — the one case this adapter has already resolved and never
    asks it about. Every other value keeps its ``bool`` on both sides of that
    change, so this property means the same thing before and after it lands.
    """
    if _is_an_unusable_seed_criterion(fact):
        return CriterionState.UNREADABLE
    if _is_a_seed_goal(fact):
        return CriterionState.GOAL
    if _overrides_the_share_limit(fact):
        return CriterionState.OVERRIDING
    return CriterionState.DEFERRING


def _expected(fact: Fact[object]) -> CriterionState:
    if isinstance(fact, Unknown):
        return classify(None, was_read=False)
    return classify(fact.value)


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


@_SETTINGS
@given(CRITERION_VALUES)
def test_a_read_criterion_is_filed_in_the_state_qbittorrent_resolves_it_to(value):
    """The soundness property, over every value shape a criterion can carry.

    Each historical false PASS is one disagreement here: #10 put a junk value in
    ``GOAL``, #14 put ``-1``/``-5`` in ``GOAL``, #28 put them in ``DEFERRING``.
    """
    fact = _known(value)
    assert lintarr_state(fact) is _expected(fact), (
        f"{value!r} ({type(value).__name__}): lintarr says "
        f"{lintarr_state(fact).value}, qBittorrent resolves it to {_expected(fact).value}"
    )


@pytest.mark.parametrize(
    "fact",
    [_NEVER_READ, _known(None)],
    ids=["never-read", "read-as-null"],
)
def test_an_unset_criterion_defers_however_it_was_reported(fact):
    """Absent and null are one state, and it is ``DEFERRING``, not ``GOAL``.

    Kept out of the generative property because the two are the *same* answer
    reached by different routes — ``QBittorrentProxyV2.cs`` substitutes ``-2``
    for an unset ``Ratio``, and lintarr reads a null the same way — and because
    ``facts.py`` keeps "never read" and "read back as null" apart on purpose, so
    each has to be named to be pinned.
    """
    assert lintarr_state(fact) is CriterionState.DEFERRING
    assert _expected(fact) is CriterionState.DEFERRING


# ---------------------------------------------------------------------------
# Controls. Without these the property above is an aggregate over whatever the
# generator happens to produce, which is how a narrowed strategy goes green.
# ---------------------------------------------------------------------------


def test_the_criterion_strategy_reaches_every_state():
    """Reachability control, and the one that makes the property mean anything.

    Equality against the full membership of ``CriterionState``, not a ``>=`` or
    a subset: a strategy that stopped drawing junk — or stopped drawing
    negatives, which is #14's and #28's whole input class — would leave the
    property green over the two states that remain. The enum is read rather than
    retyped, so a fifth state added to the model fails here until the generator
    can reach it.
    """
    drawn: set[CriterionState] = set()

    @settings(max_examples=500, deadline=None, suppress_health_check=list(HealthCheck))
    @given(CRITERION_VALUES)
    def draw(value):
        drawn.add(classify(value))

    draw()
    assert drawn == set(CriterionState), f"never drawn: {set(CriterionState) - drawn}"


def test_the_model_partitions_every_value_it_is_given():
    """The model is total and its states are mutually exclusive.

    ``classify`` is a chain of early returns so this is true by construction —
    which is the point: it is asserted here so that rewriting it as independent
    predicates, the shape ``queue_liveness`` actually uses, cannot introduce a
    value belonging to two states or none. A value in two states is how #28's
    ``OVERRIDING`` and ``DEFERRING`` overlapped.
    """
    for value in (*MEASURED_EDGE_VALUES, -5, -2, -1, 0, 1, 2.5, "x", True, [1], {"a": 1}, None):
        assert isinstance(classify(value), CriterionState), f"{value!r} resolved to nothing"


def test_the_measurement_docs_table_still_reads_as_written():
    """The model anchored to the document it claims to execute.

    Every row of the ``seedCriteria.seedRatio`` table in
    ``docs/measurements/2026-10-05-sonarr-seed-ratio-range.md``, plus the two
    rows its prose adds and the table omits (``inf`` and ``nan``, which the
    "What is still not expressed" section measures as overriding).

    This is the control on the *model*, where the property above is the control
    on the *code*. Without it a wrong model and a wrong implementation could
    agree and the property would pass — which is precisely how #28's own worked
    example shipped wrong in this very document and was inherited by the ticket
    that cited it.
    """
    table: tuple[tuple[object, CriterionState], ...] = (
        (None, CriterionState.DEFERRING),  # null (cleared)
        (-5, CriterionState.OVERRIDING),
        (-1, CriterionState.OVERRIDING),
        (-0.5, CriterionState.OVERRIDING),
        (-2, CriterionState.DEFERRING),
        (0, CriterionState.GOAL),
        (0.5, CriterionState.GOAL),
        (2.0, CriterionState.GOAL),  # the document's own control row
        (math.inf, CriterionState.OVERRIDING),
        (math.nan, CriterionState.OVERRIDING),
    )
    wrong = [
        (value, classify(value).value, expected.value)
        for value, expected in table
        if classify(value) is not expected
    ]
    assert not wrong, f"model disagrees with the measurement doc: {wrong}"
    # And the absent row, which cannot be expressed as a value.
    assert classify(None, was_read=False) is CriterionState.DEFERRING


def test_a_criterion_that_is_not_a_number_is_unreadable_whatever_python_thinks():
    """The type half of the model, anchored to its own citation.

    Deliberately *not* folded into the table control above, and the separation
    is a finding rather than a preference. The table is a table of
    ``seedRatio`` **numbers**, so it has no row for a string or a bool — and a
    mutation round proved that blindness is real: dropping the model's ``bool``
    exclusion left ``test_the_measurement_docs_table_still_reads_as_written``
    green, because there is no row it could have contradicted. Widening that
    control with rows the document does not contain would have cured the
    symptom and destroyed what the control is for, which is agreeing with a
    document rather than with an opinion.

    So the type rule gets the control its own citation can support.
    ``SeedCriteriaSettings.cs`` declares::

        public double? SeedRatio { get; set; }      // nullable: a cleared field is null
        public int? SeedTime { get; set; }

    A ``double?`` holds a number or nothing. It cannot hold ``"-1"``, ``true``,
    ``[1]`` or ``{"a": 1}``, so none of those can be a limit the operator set
    and none may be resolved to one — which is lintarr#10 exactly.

    ``True`` is listed first because it is the only member that needs the rule
    *written*: ``isinstance(True, int)`` is True in Python, so a bool falls into
    the number case on its own and has to be excluded rather than filtered out
    by the type test. It is also the one the round above caught.
    """
    # Every member is a shape ``json.loads`` can actually produce, so each is a
    # value a service could really send. A tuple or a ``bytes`` would widen the
    # control past what the citation covers without widening what it can catch.
    for value in (True, False, "-1", "2.0", "unlimited", [1], {"a": 1}):
        assert classify(value) is CriterionState.UNREADABLE, (
            f"{value!r} ({type(value).__name__}) is not a number a double? can hold, "
            f"but the model resolved it to {classify(value).value}"
        )


def test_every_state_is_reachable_through_the_real_predicates():
    """The adapter's four branches are all live.

    ``lintarr_state`` is a chain of ``if``s, so a predicate that started
    answering True for everything would collapse it to one state and the
    property would still pass on whatever the model agreed about. One witness
    per state, each read off the measurement doc rather than chosen to fit.
    """
    witnesses: tuple[tuple[object, CriterionState], ...] = (
        (2.0, CriterionState.GOAL),
        (-2, CriterionState.DEFERRING),
        (-1, CriterionState.OVERRIDING),
        ("-1", CriterionState.UNREADABLE),
    )
    seen = {lintarr_state(_known(value)) for value, _ in witnesses}
    assert seen == set(CriterionState), (
        f"unreachable through the predicates: {set(CriterionState) - seen}"
    )
    for value, expected in witnesses:
        assert lintarr_state(_known(value)) is expected, (
            f"{value!r} is not the {expected.value} witness"
        )
