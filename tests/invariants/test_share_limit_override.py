"""A negative indexer goal overrides the global limit, so the global cannot save it.

lintarr#28, and the residual #14 left behind rather than created. #14 settled
*range*: every negative ``seedCriteria`` value is "no limit", so none of them is
an indexer goal and all of them arm ``arr.indexer_without_seed_criteria``. What
it could not settle is that ``-2`` and every other negative are no-limit in
**physically different ways**, and only one of them can be rescued by a setting
the operator makes globally:

- ``-2``, absent and null all mean *defer*. ``effectiveRatioLimit()`` redirects
  ``-2`` to ``categoryRatioLimit(category())``, which is the only route to
  ``globalMaxRatio()`` — so a global ratio limit does release the slot.
- Every **other** negative means *override to unlimited*. It is returned
  verbatim into a ``>= 0`` gate it can never pass, the redirect never happens,
  and no global or category setting is ever consulted.

``_seeding_conflict`` is a conjunction that also requires ``qbt.no_global_ratio``
and ``qbt.no_global_seed_time``, which is exactly right for *defer* and wrong
for *override*: the override case is the one where those conjuncts are false and
the torrents never stop seeding anyway. Three shapes fall through it, and all
three reported PASS before this module existed.

Measured from qBittorrent ``release-5.2.4``
(``docs/measurements/2026-10-05-sonarr-seed-ratio-range.md``). A torrent is
released when **either** limit is reached, so a wedge needs *both* to be
unreachable — which is why the shape of each test below is a pair of criteria,
not a single one.
"""

import pytest

from lintarr.invariants.queue_liveness import check
from lintarr.outcomes import Outcome
from tests.fixtures.homelab import qbt_with
from tests.invariants.test_queue_liveness import _NO_RATIO, _arrs, _fact, _indexer

#: Slots that can fill. Everything else comes from the *repaired* fixture, so
#: each test below differs from a healthy stack only in the share-limit facts it
#: names — which is what makes the globals the thing under test.
SLOTS = {"max_active_torrents": 5, "dont_count_slow_torrents": False}
_SLOTS = SLOTS

#: One indexer per override route, exported so ``tests/test_properties.py`` can
#: reach the three new premise labels from the same fixtures the behaviour tests
#: use. A reachability control that builds its own inputs cannot see these
#: narrow, and these are narrow by construction: each needs a stack the
#: deferring conjunction leaves at PASS.
OVERRIDES_BOTH = _arrs(_indexer(seed_ratio=_fact(-1), seed_time=_fact(-1)))
OVERRIDES_RATIO = _arrs(_indexer(seed_ratio=_fact(-1), seed_time=_NO_RATIO))
OVERRIDES_SEED_TIME = _arrs(_indexer(seed_ratio=_NO_RATIO, seed_time=_fact(-1)))

#: Values that override: readable, negative, and not the ``-2`` that defers.
#: ``-inf`` belongs here and not in the deferring set — ``-inf != -2``, so it is
#: never redirected, and ``-inf >= 0`` never passes.
_OVERRIDING = [-1, -5, -0.5, float("-inf")]


def _stack(ratio, seed_time, **qbt):
    return qbt_with(**(_SLOTS | qbt)), _arrs(_indexer(seed_ratio=ratio, seed_time=seed_time))


# ---------------------------------------------------------------------------
# Shape 1 — both criteria override. No global setting can reach either limit.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", _OVERRIDING, ids=repr)
def test_overriding_both_criteria_wedges_whatever_the_globals_say(value):
    """The strongest shape: both globals ON and the stack still cannot recover.

    ``qbt_with()`` is the repaired fixture, so ``max_ratio_enabled`` and
    ``max_seeding_time_enabled`` are both True with real values behind them
    (1.5 and 20160). Neither is consulted: the torrent carries its own limit on
    both axes and ``effectiveRatioLimit`` / ``effectiveSeedingTimeLimit``
    redirect ``-2`` alone.

    This is the one shape no amount of global configuration fixes, which is why
    it gets its own conflict and its own remedy sentence.
    """
    qbt, arrs = _stack(_fact(value), _fact(value))
    assert check(qbt, arrs).outcome is Outcome.FAIL


def test_overriding_both_criteria_names_no_global_premise():
    """The premise set is the explanation, so it must not blame the globals.

    A finding that listed ``qbt.no_global_ratio`` here would be stating a false
    premise — the global ratio limit is ON in this fixture — and sending the
    operator to a setting that cannot help. The absence of those labels is the
    assertion.
    """
    qbt, arrs = _stack(_fact(-1), _fact(-1))
    labels = {p.label for p in check(qbt, arrs).premises}
    assert "arr.indexer_overrides_both_share_limits" in labels
    assert not labels & {"qbt.no_global_ratio", "qbt.no_global_seed_time"}


# ---------------------------------------------------------------------------
# Shape 2 — the ratio overrides, the seed time defers to a global that is off.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", _OVERRIDING, ids=repr)
def test_an_overriding_ratio_wedges_when_the_seed_time_global_is_off(value):
    """Ratio unreachable by override, seed time unreachable by an off global.

    The likely shape in the field, and the one the ticket meant: an operator
    types ``-1`` into one indexer's seed ratio, has a *global* ratio limit set,
    and has no global seeding-time limit. Nothing releases the slot, and
    ``_seeding_conflict`` cannot say so because ``qbt.no_global_ratio`` is
    false — about a limit this torrent overrides.
    """
    qbt, arrs = _stack(_fact(value), None, max_seeding_time_enabled=False)
    assert check(qbt, arrs).outcome is Outcome.FAIL


def test_an_overriding_ratio_is_released_by_the_seed_time_global():
    """The control that keeps the shape above honest, and the ticket's own error.

    lintarr#28 reported ``seed_ratio=-1`` on a bare ``qbt_with()`` as a false
    PASS. It is not: the repaired fixture has ``max_seeding_time_enabled=True``
    with 20160 minutes behind it, an unset ``seedTime`` reaches qBittorrent as
    ``-2``, and ``effectiveSeedingTimeLimit()`` redirects ``-2`` to the global.
    The slot is released after 14 days, so PASS is the right verdict.

    The ticket reasoned about the ratio axis alone. Without this control the
    fix would be free to FAIL here too, and that is a false FAIL on a stack
    that recovers by itself.
    """
    qbt, arrs = _stack(_fact(-1), None)
    assert check(qbt, arrs).outcome is Outcome.PASS


def test_an_overriding_ratio_beside_a_real_seed_time_goal_passes():
    """One axis overridden is not a wedge while the other sets a real goal.

    The indexer's own ``seedTime`` of 2880 minutes releases the slot without
    consulting anything global, so this must PASS with both globals off — the
    configuration that FAILs on every other input in this file.
    """
    qbt, arrs = _stack(
        _fact(-1), _fact(2880), max_ratio_enabled=False, max_seeding_time_enabled=False
    )
    assert check(qbt, arrs).outcome is Outcome.PASS


# ---------------------------------------------------------------------------
# Shape 3 — the mirror. Criteria are only symmetrical by convention here.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", _OVERRIDING, ids=repr)
def test_an_overriding_seed_time_wedges_when_the_ratio_global_is_off(value):
    """Separate from the ratio shape because a rule can be got wrong one field
    at a time — the reason ``test_a_negative_seed_time_is_not_a_seed_goal``
    gives, which found a live defect in #14's first draft.

    ``DEFAULT_SEEDING_TIME_LIMIT`` is ``-2`` and ``effectiveSeedingTimeLimit()``
    redirects it identically, so the override rule applies to this field too.
    """
    qbt, arrs = _stack(_NO_RATIO, _fact(value), max_ratio_enabled=False)
    assert check(qbt, arrs).outcome is Outcome.FAIL


def test_an_overriding_seed_time_is_released_by_the_ratio_global():
    """The mirror control. The global ratio limit of 1.5 releases the slot."""
    qbt, arrs = _stack(_NO_RATIO, _fact(-1))
    assert check(qbt, arrs).outcome is Outcome.PASS


# ---------------------------------------------------------------------------
# What must NOT change.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [-2, -2.0])
def test_a_deferring_criterion_still_defers(value):
    """``-2`` is not an override, and treating it as one is a false FAIL.

    This is the single value that separates the override rule from "every
    negative", and it is the one the whole ticket turns on. With both globals
    ON and both criteria at ``-2``, every limit is the global's and the global
    releases the slot.

    Both spellings, because ``seedRatio`` is a ``double?`` in Sonarr and
    ``seedTime`` an ``int?``, so the value arrives as a float on one axis and
    an int on the other.
    """
    qbt, arrs = _stack(_fact(value), _fact(value))
    assert check(qbt, arrs).outcome is Outcome.PASS


@pytest.mark.parametrize("value", ["-1", True, None])
def test_an_unreadable_criterion_is_not_an_override(value):
    """A value nobody can read is not evidence that a limit was overridden.

    ``"-1"`` above all: coercing it would invent the very claim this file makes
    the loudest — that the global setting cannot help — out of a string. Both
    globals are ON here, so reading any of these as an override is a FAIL on a
    stack that recovers.
    """
    qbt, arrs = _stack(_fact(value), _fact(value))
    assert check(qbt, arrs).outcome is Outcome.PASS


def test_a_usenet_indexer_cannot_override_a_share_limit():
    """Not a torrent source, so its seed criteria cannot wedge a torrent queue.

    The same escape hatch ``_seeding_conflict`` has, asserted against the new
    conflicts: a predicate added without ``_is_a_torrent_source`` would FAIL
    every stack with a usenet indexer carrying a stale ``-1``.
    """
    qbt, arrs = _stack(_fact(-1), _fact(-1))
    usenet = _arrs(_indexer(seed_ratio=_fact(-1), seed_time=_fact(-1), protocol="usenet"))
    assert check(qbt, usenet).outcome is Outcome.PASS


def test_a_disabled_indexer_cannot_override_a_share_limit():
    """Every grab toggle off, so nothing from it ever enters the queue."""
    qbt, _ = _stack(_fact(-1), _fact(-1))
    off = _arrs(_indexer(seed_ratio=_fact(-1), seed_time=_fact(-1), enabled=False))
    assert check(qbt, off).outcome is Outcome.PASS


def test_an_unclassifiable_indexer_does_not_turn_an_override_into_a_pass():
    """An indexer nobody could classify must not be read as harmless.

    ``_seeding_conflict`` already SKIPs on this shape, and the new conflicts
    quantify through the same three-valued existential, so the verdict stays
    SKIP rather than becoming the PASS that a two-valued ``for`` loop would
    produce.
    """
    qbt, _ = _stack(_fact(-1), _fact(-1))
    weird = _arrs(_indexer(seed_ratio=_fact(-1), seed_time=_fact(-1), protocol=None))
    assert check(qbt, weird).outcome is Outcome.SKIP


def test_an_override_does_not_claim_the_criterion_was_unreadable():
    """``-1`` was read perfectly; the note must not say otherwise.

    The ``_note_unreadable_seed_criteria`` guard keys on the conflict, so
    widening it to the new conflicts is what could regress this. An operator
    told their ``-1`` "is not a number" goes and re-types a value lintarr
    understood.
    """
    qbt, arrs = _stack(_fact(-1), _fact(-1))
    finding = check(qbt, arrs)
    assert finding.outcome is Outcome.FAIL
    assert "not a number" not in finding.detail


def test_an_unreadable_criterion_beside_an_override_is_still_named():
    """The note still has to fire on the new conflicts when it is true.

    ``seedTime`` here is junk and the ratio overrides, so the FAIL rests on a
    value nobody could read as well as on one that was read exactly right. A
    guard that simply excluded the new conflicts would drop that sentence.
    """
    qbt, _ = _stack(_fact(-1), None)
    junk = _arrs(_indexer(seed_ratio=_fact(-1), seed_time=_fact("soon")))
    finding = check(qbt_with(**(_SLOTS | {"max_seeding_time_enabled": False})), junk)
    assert finding.outcome is Outcome.FAIL
    assert "not a number" in finding.detail


@pytest.mark.parametrize("value", [float("inf"), float("nan")], ids=repr)
def test_a_non_finite_criterion_overrides_rather_than_defers(value):
    """The two values a ``value < 0`` spelling of the override rule gets wrong.

    Both are unreachable limits that are *not* ``-2``, so qBittorrent returns
    them verbatim and never calls ``categoryRatioLimit()``: ``realRatio()``
    never reaches ``inf``, and every comparison against ``nan`` is False. They
    override exactly as ``-1`` does, and the global limit cannot save either —
    which is why ``_overrides_the_share_limit`` is written as "not a goal"
    rather than as a sign test.

    Both globals are ON here, so a ``< 0`` implementation reports PASS on a
    stack that never frees a slot. ``json`` decodes ``1e400`` and the
    non-standard ``Infinity``/``NaN`` tokens to these, so they arrive through
    collect rather than only through a test.
    """
    qbt, arrs = _stack(_fact(value), _fact(value))
    assert check(qbt, arrs).outcome is Outcome.FAIL


def test_the_override_routes_do_not_displace_the_deferring_ones_explanation():
    """homelab#393 itself must keep the explanation it has always had.

    ``wedged_qbt()`` + ``NO_GOALS`` matches the deferring conjunction and the
    ratio override route is irrelevant to it. If the routes were ordered the
    other way, or if ``_overrides_the_share_limit`` read an absent criterion as
    an override, the flagship FAIL would start blaming an indexer value nobody
    set and stop naming the two globals the operator actually has to turn on.
    """
    from tests.fixtures.homelab import wedged_qbt
    from tests.invariants.test_queue_liveness import NO_GOALS

    finding = check(wedged_qbt(), NO_GOALS)
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == "seeders-absorb-every-slot"
    assert {p.label for p in finding.premises} >= {
        "qbt.no_global_ratio",
        "qbt.no_global_seed_time",
        "arr.indexer_without_seed_criteria",
    }


def test_an_overriding_seed_time_beside_a_real_ratio_goal_passes():
    """The mirror of ``..._overriding_ratio_beside_a_real_seed_time_goal_passes``.

    Added because its absence was the single survivor of this change's mutation
    round: dropping ``not _is_a_seed_goal(indexer.seed_ratio)`` from
    ``_overrides_the_seed_time_limit`` left all 845 tests green, while the
    identical cut on the ratio route was killed. The two routes are only
    symmetrical by convention and the suite has to set *both* pairs into
    conflict, or one of them is graded by a test that cannot see it.

    The indexer's own ratio of 2.0 releases the slot with no global setting
    involved, so this must PASS with both globals off.
    """
    mixed = _arrs(_indexer(seed_ratio=_fact(2.0), seed_time=_fact(-1)))
    both_off = qbt_with(
        **(_SLOTS | {"max_ratio_enabled": False, "max_seeding_time_enabled": False})
    )
    assert check(both_off, mixed).outcome is Outcome.PASS


def test_a_long_premise_label_still_lines_up_in_the_human_render():
    """The premise column is measured, not pinned to a constant.

    ``arr.indexer_overrides_the_seed_time_limit`` is 41 characters and the
    renderer's old width was 36, so the state column jumped right on the one
    premise that carries this conflict's whole explanation. Asserted as "every
    state starts at the same column" rather than against a number, so the
    assertion cannot go stale the next time a label grows.
    """
    from lintarr.cli import _render_findings

    findings = [
        check(qbt_with(**_SLOTS), OVERRIDES_BOTH),
        check(qbt_with(max_ratio_enabled=False, **_SLOTS), OVERRIDES_SEED_TIME),
    ]
    rows = [line for line in _render_findings(findings).splitlines() if line.startswith("    arr.")]
    assert len(rows) == 2, rows
    assert len({row.index(" holds") for row in rows}) == 1, rows


#: The cross-product table in
#: ``docs/measurements/2026-10-05-sonarr-seed-ratio-range.md``, row for row:
#: ``(seed_ratio, seed_time, global ratio on, global seed time on, verdict)``.
#: Pinned in code because the thing that made #28 wrong in the first place was
#: a worked example in that document which nothing executed — and because the
#: last stale table in this fleet survived a hand correction and needed a guard
#: (22parsons-circuits#26).
_DOC_TABLE = [
    ("defer", "defer", False, False, Outcome.FAIL),
    ("defer", "defer", True, False, Outcome.PASS),
    ("defer", "defer", False, True, Outcome.PASS),
    ("override", "defer", True, False, Outcome.FAIL),
    ("defer", "override", False, True, Outcome.FAIL),
    ("override", "override", True, True, Outcome.FAIL),
    ("override", "defer", True, True, Outcome.PASS),
    ("override", "goal", False, False, Outcome.PASS),
    ("goal", "override", False, False, Outcome.PASS),
]

_RATIO_VALUES = {"defer": _NO_RATIO, "override": _fact(-1), "goal": _fact(2.0)}
_TIME_VALUES = {"defer": None, "override": _fact(-1), "goal": _fact(2880)}


@pytest.mark.parametrize(
    ("ratio", "seed_time", "global_ratio", "global_time", "expected"), _DOC_TABLE
)
def test_the_documented_cross_product_is_what_the_code_reports(
    ratio, seed_time, global_ratio, global_time, expected
):
    """Nine rows, three of which were PASS before #28 and are FAIL now.

    The three deferring PASS rows are the controls that keep the fix from being
    "FAIL on any negative": they are stacks a global setting genuinely rescues,
    and an over-eager override rule turns each of them red.
    """
    qbt = qbt_with(max_ratio_enabled=global_ratio, max_seeding_time_enabled=global_time, **_SLOTS)
    arrs = _arrs(_indexer(seed_ratio=_RATIO_VALUES[ratio], seed_time=_TIME_VALUES[seed_time]))
    assert check(qbt, arrs).outcome is expected


# ---------------------------------------------------------------------------
# qbt.no_category_limits on the two single-axis routes. Added after a code
# review measured BOTH of them as unverifiable conjuncts: deleting the premise
# from either route left all 856 tests green, which is exactly what
# tests/test_needs_are_load_bearing.py exists to prevent one layer up.
# ---------------------------------------------------------------------------

#: A category whose own limits both inherit. Does not break either wedge.
_INHERITS = {"tv-sonarr": {"ratio_limit": -2, "seeding_time_limit": -2}}


def test_a_categorys_own_seed_time_limit_releases_an_overridden_ratio():
    """The ratio is out of reach, so the category's seeding-time limit is the fix.

    1440 minutes is the category's own limit rather than an inherited one, so
    ``categorySeedingTimeLimit()`` returns it without consulting the global —
    which is off here. The slot is released after a day and this must PASS.
    """
    own = {"tv-sonarr": {"ratio_limit": -2, "seeding_time_limit": 1440}}
    qbt = qbt_with(max_seeding_time_enabled=False, categories=own, **_SLOTS)
    assert check(qbt, OVERRIDES_RATIO).outcome is Outcome.PASS
    inherits = qbt_with(max_seeding_time_enabled=False, categories=_INHERITS, **_SLOTS)
    assert check(inherits, OVERRIDES_RATIO).outcome is Outcome.FAIL


def test_a_categorys_own_ratio_limit_releases_an_overridden_seed_time():
    """The mirror. Its own test for the same reason the whole file is doubled up."""
    own = {"tv-sonarr": {"ratio_limit": 2.0, "seeding_time_limit": -2}}
    qbt = qbt_with(max_ratio_enabled=False, categories=own, **_SLOTS)
    assert check(qbt, OVERRIDES_SEED_TIME).outcome is Outcome.PASS
    inherits = qbt_with(max_ratio_enabled=False, categories=_INHERITS, **_SLOTS)
    assert check(inherits, OVERRIDES_SEED_TIME).outcome is Outcome.FAIL


def test_the_combined_category_premise_makes_the_ratio_route_miss():
    """The documented over-requirement, pinned so it stays documented.

    ``qbt.no_category_limits`` reads a category's ``ratio_limit`` as well as its
    ``seeding_time_limit``. On ``OVERRIDE_RATIO`` the ratio half is irrelevant —
    the torrent's own ratio limit is not ``-2``, so no category ratio limit is
    ever consulted — yet a category that sets one suppresses the FAIL. This
    stack wedges and lintarr reports PASS.

    Asserted rather than left implicit because the alternative imprecision is a
    false FAIL, and this project prefers a miss it has written down. Splitting
    the premise per criterion is what closes it; when that lands, this test
    flips to FAIL and is the thing that says so.
    """
    ratio_only = {"tv-sonarr": {"ratio_limit": 2.0, "seeding_time_limit": -2}}
    qbt = qbt_with(max_seeding_time_enabled=False, categories=ratio_only, **_SLOTS)
    assert check(qbt, OVERRIDES_RATIO).outcome is Outcome.PASS
