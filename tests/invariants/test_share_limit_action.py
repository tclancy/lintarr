"""Reaching a share limit frees a slot only if the action stops the torrent.

lintarr#32's second half, and the inverse of every other route in
`queue_liveness`: those say *no limit will ever be reached*, this says a limit
**is** reached and the torrent keeps its slot anyway. It is the one route that
can FAIL a stack whose share limits are all correctly configured, which is why
it was a false PASS rather than a false FAIL.

Measured from `release-5.2.4`. `processTorrentShareLimits` consults the action
*inside* `if (reached)`, after whichever of the three arms fired:

    if (reached) {
        const ShareLimitAction a = torrent->effectiveShareLimitAction();
        if      (a == Remove)             removeTorrent(KeepContent);
        else if (a == RemoveWithContent)  removeTorrent(RemoveContent);
        else if (a == Stop && !stopped)   torrent->stop();
        else if (a == EnableSuperSeeding && ...) torrent->setSuperSeeding(true);
    }

so the action defeats the ratio, seeding-time and inactive-seeding-time limits at
once. `effectiveShareLimitAction()` asks the torrent, then its category, then
the global, and Sonarr sets no per-torrent action — so the chain starts at the
category. `max_ratio_act` decides only for torrents whose category names no
action of its own; despite the name it is the global action for all three arms
and not the ratio arm's, which is historical.

**The category half is lintarr#61** and is the second block of tests below.
Reading `max_ratio_act` alone was a false PASS on a category set to
EnableSuperSeeding beside a draining global, and a FAIL that named no route on
the mirror case. The first block still drives the global-only truth table, and
the pin test crosses it with every way a category can decline to decide.

**Why an allow-list and not `!= 2`.** The dispatch above has four branches, so
`Default = -1` matches *none of them* and the torrent is left running exactly as
super seeding does. `Q_ASSERT(act != ShareLimitAction::Default)` guards the
setter and compiles out of release builds, and `appcontroller.cpp` publishes the
value through a bare `static_cast<int>`, so `-1` is observable over the API. A
denylist would read it as releasing and clear the conflict.
"""

import pytest

from lintarr.clients.qbittorrent.queue_liveness import (
    _RELEASING_ACTIONS,
    ACTION,
    _action_frees_no_slot,
    check,
)
from lintarr.facts import Unknown
from lintarr.outcomes import Outcome
from tests.fixtures.homelab import qbt_with
from tests.invariants.test_queue_liveness import NO_GOALS
from tests.invariants.test_share_limit_override import SLOTS

#: Every value of `ShareLimitAction` from `sharelimits.h`, paired with whether it
#: frees the slot. Written out rather than derived from `_RELEASING_ACTIONS`: a
#: table derived from the thing under test asserts only that it agrees with
#: itself. `7` stands for a value a future release adds — it matches no dispatch
#: branch, so it frees nothing, which is the behaviour the allow-list exists for.
_ACTIONS = [
    pytest.param(0, True, id="Stop"),
    pytest.param(1, True, id="Remove"),
    pytest.param(3, True, id="RemoveWithContent"),
    pytest.param(2, False, id="EnableSuperSeeding"),
    pytest.param(-1, False, id="Default-matches-no-branch"),
    pytest.param(7, False, id="a-value-a-future-release-adds"),
]


@pytest.mark.parametrize(("act", "frees_a_slot"), _ACTIONS)
def test_only_a_stopping_or_removing_action_clears_the_conflict(act, frees_a_slot):
    """The whole truth table, on a stack every other route leaves at PASS.

    `SLOTS` on the repaired fixture means the globals are on and reachable and
    the inactive gate is off, so nothing here is about the limits.
    """
    finding = check(qbt_with(**SLOTS, max_ratio_act=act), NO_GOALS)
    if frees_a_slot:
        assert finding.outcome is Outcome.PASS, (
            f"action {act} stops or removes the torrent, so the slot frees; "
            f"got {finding.outcome}/{finding.conflict}"
        )
    else:
        assert finding.outcome is Outcome.FAIL, (
            f"action {act} leaves the torrent running, so the slot never frees; "
            f"got {finding.outcome}"
        )
        assert finding.conflict == ACTION


def test_the_allow_list_is_the_three_releasing_actions():
    """Pins the constant against the enum, so narrowing it fails here.

    The table above drives behaviour; this pins the mapping. Without it,
    `_RELEASING_ACTIONS` could be widened to include `2` and only the
    EnableSuperSeeding row would notice.
    """
    assert _RELEASING_ACTIONS == frozenset({0, 1, 3})


def test_an_unreadable_action_skips_rather_than_assuming_it_releases():
    """An action nobody read cannot settle whether a slot frees.

    The dangerous default is the optimistic one: assuming it releases turns this
    route off and reports a clean bill of health on a stack nobody measured.
    """
    finding = check(
        qbt_with(**SLOTS, max_ratio_act=Unknown("insufficient-permission", "max_ratio_act")),
        NO_GOALS,
    )
    assert finding.outcome is Outcome.SKIP
    assert "qbt.share_limit_action_frees_no_slot" in {p.label for p in finding.premises}


def test_a_non_integer_action_is_not_coerced():
    """A string or null action is a fact we do not have, not a zero."""
    for junk in ("2", None, True, 1.5):
        finding = check(qbt_with(**SLOTS, max_ratio_act=junk), NO_GOALS)
        assert finding.outcome is Outcome.SKIP, f"{junk!r} was coerced to a limit"


def test_the_action_route_does_not_require_the_inactive_gate_to_be_off():
    """The regression this route's premise set exists to prevent.

    If `ACTION` composed on `_deferring_slot_premises` it would inherit
    `qbt.no_global_inactive_seed_time` and go quiet on a stack whose inactive gate
    is ON — but with a non-releasing action, that gate firing releases nothing
    either. The wedge is real and must still be reported.
    """
    finding = check(
        qbt_with(**SLOTS, max_ratio_act=2, max_inactive_seeding_time_enabled=True),
        NO_GOALS,
    )
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == ACTION
    assert "qbt.no_global_inactive_seed_time" not in {p.label for p in finding.premises}


def test_an_existing_wedge_still_reports_its_own_conflict():
    """ACTION is ordered last, so no stack that already FAILed changes its verdict.

    homelab#393's configuration plus a non-releasing action satisfies both
    `SEEDING` and `ACTION`; the operator must still be told about the limits,
    because that is the remedy that was already correct.
    """
    wedge = SLOTS | {"max_ratio_enabled": False, "max_seeding_time_enabled": False}
    finding = check(qbt_with(**wedge, max_ratio_act=2), NO_GOALS)
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict != ACTION


# ---------------------------------------------------------------------------
# lintarr#61 — a category's own `share_limit_action` overrides the global.
# ---------------------------------------------------------------------------
#
# `effectiveShareLimitAction()` asks the torrent, then its category, then the
# global. qBittorrent 5.x categories carry `share_limit_action`, so the global
# decides only for torrents whose category names no action of its own. Reading
# `max_ratio_act` alone was therefore two bugs at once: a category set to
# EnableSuperSeeding beside a global Stop PASSed, and a FAIL on a global
# EnableSuperSeeding said nothing about the categories that override it.
#
# The wedge argument is unchanged and is what makes one route enough: seeders
# accumulate in `max_active_torrents` and never leave, so a single category
# whose torrents never release fills every slot however well the rest drain.


#: A category body carrying one action, under the name the homelab stack uses.
def _tv(action):
    return {"categories": {"tv": {"share_limit_action": action}}}


#: Every `share_limit_action` string qBittorrent's categories endpoint reports,
#: plus `Default`. Written out rather than read from `ACTION_BY_NAME` for the
#: reason `_ACTIONS` is: a table derived from the code agrees with itself.
_CATEGORY_DEFERS = [
    pytest.param({}, id="no-categories"),
    pytest.param({"categories": {"tv": {}}}, id="category-names-no-action"),
    pytest.param(_tv("Default"), id="category-action-Default"),
    pytest.param({"categories": {"tv": {"ratio_limit": -2}}}, id="category-with-other-keys"),
]


@pytest.mark.parametrize(("act", "frees_a_slot"), _ACTIONS)
@pytest.mark.parametrize("categories", _CATEGORY_DEFERS)
def test_a_category_that_names_no_action_still_follows_the_global(act, frees_a_slot, categories):
    """The pin. Every verdict the global alone used to decide is unchanged.

    Four ways a category can decline to decide — the map is empty, the key is
    absent, the value is the literal `Default`, or the category carries other
    keys and not this one — crossed with the whole action truth table. This is
    the half of lintarr#61 that must NOT move, and it is asserted against the
    same `_ACTIONS` table the global-only test drives, so the two cannot drift.
    """
    finding = check(qbt_with(**SLOTS, max_ratio_act=act, **categories), NO_GOALS)
    expected = Outcome.PASS if frees_a_slot else Outcome.FAIL
    assert finding.outcome is expected, (
        f"a category deferring to global action {act} changed the verdict; "
        f"got {finding.outcome}/{finding.conflict}"
    )


#: `(global action, category action) -> outcome`, written out in full. Both
#: axes are the enum, the expectation is a literal, and nothing here is
#: computed from `_RELEASING_ACTIONS` — the point is to state the cross-product
#: rather than to re-run the implementation's `or` and agree with it.
#:
#: Read the table as: FAIL unless BOTH routes free a slot. The global route is
#: always present because a torrent with no category always takes it, so a
#: non-releasing global is a FAIL whatever the categories say.
_MATRIX = {
    # global Stop — drains, so only the category can wedge it.
    (0, "Stop"): Outcome.PASS,
    (0, "Remove"): Outcome.PASS,
    (0, "RemoveWithContent"): Outcome.PASS,
    (0, "EnableSuperSeeding"): Outcome.FAIL,
    # global Remove — the same shape.
    (1, "Stop"): Outcome.PASS,
    (1, "Remove"): Outcome.PASS,
    (1, "RemoveWithContent"): Outcome.PASS,
    (1, "EnableSuperSeeding"): Outcome.FAIL,
    # global RemoveWithContent — the repaired homelab stack's own value.
    (3, "Stop"): Outcome.PASS,
    (3, "Remove"): Outcome.PASS,
    (3, "RemoveWithContent"): Outcome.PASS,
    (3, "EnableSuperSeeding"): Outcome.FAIL,
    # global EnableSuperSeeding — uncategorised torrents never release, so a
    # releasing category cannot clear it. The FAIL is about a different set of
    # torrents, which is what the finding's detail says.
    (2, "Stop"): Outcome.FAIL,
    (2, "Remove"): Outcome.FAIL,
    (2, "RemoveWithContent"): Outcome.FAIL,
    (2, "EnableSuperSeeding"): Outcome.FAIL,
    # global Default — matches no dispatch branch, so it behaves as above.
    (-1, "Stop"): Outcome.FAIL,
    (-1, "Remove"): Outcome.FAIL,
    (-1, "RemoveWithContent"): Outcome.FAIL,
    (-1, "EnableSuperSeeding"): Outcome.FAIL,
    # global 7 — a value a future release adds, likewise.
    (7, "Stop"): Outcome.FAIL,
    (7, "Remove"): Outcome.FAIL,
    (7, "RemoveWithContent"): Outcome.FAIL,
    (7, "EnableSuperSeeding"): Outcome.FAIL,
}


@pytest.mark.parametrize(("pair", "expected"), sorted(_MATRIX.items(), key=repr), ids=repr)
def test_the_global_and_the_category_action_cross_product(pair, expected):
    """Every (global, category) pair, stated rather than derived."""
    act, name = pair
    finding = check(qbt_with(**SLOTS, max_ratio_act=act, **_tv(name)), NO_GOALS)
    assert finding.outcome is expected, f"global {act} with category tv {name}"
    if expected is Outcome.FAIL:
        assert finding.conflict == ACTION


def test_the_matrix_reaches_both_verdicts():
    """Reachability control: a table of one outcome would prove nothing."""
    assert set(_MATRIX.values()) == {Outcome.PASS, Outcome.FAIL}


def test_a_super_seeding_category_wedges_a_stack_the_global_drains():
    """lintarr#61's first scenario, and the false PASS it names.

    Global Stop with category `tv` on EnableSuperSeeding. Every torrent `tv`
    holds keeps its slot at the limit, so they accumulate until
    `max_active_torrents` is gone — exactly homelab#393's shape, arriving
    through the one setting the ACTION route did not read.
    """
    finding = check(qbt_with(**SLOTS, max_ratio_act=0, **_tv("EnableSuperSeeding")), NO_GOALS)
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == ACTION
    assert finding.detail == "the share-limit action frees no slot for category tv"


def test_a_stopping_category_does_not_clear_a_super_seeding_global():
    """lintarr#61's second scenario, and the half that stays a FAIL.

    A torrent with no category takes the global route whatever the categories
    say, so a global EnableSuperSeeding still wedges. What was wrong was the
    sentence, not the verdict: the finding used to name no route at all, and an
    operator who had set `tv` to Stop had nothing to tell them the FAIL was
    about the torrents `tv` does not cover. Narrowing this further needs the
    arr-side fact of which categories are actually in use, which this route
    does not read.
    """
    finding = check(qbt_with(**SLOTS, max_ratio_act=2, **_tv("Stop")), NO_GOALS)
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == ACTION
    assert finding.detail == (
        "the share-limit action frees no slot for uncategorised torrents and "
        "every category that defers to the global"
    )


def test_both_routes_are_named_when_both_free_no_slot():
    """The detail is a list, not a first match: two settings need two changes."""
    finding = check(qbt_with(**SLOTS, max_ratio_act=2, **_tv("EnableSuperSeeding")), NO_GOALS)
    assert finding.outcome is Outcome.FAIL
    assert finding.detail == (
        "the share-limit action frees no slot for uncategorised torrents and "
        "every category that defers to the global, category tv"
    )


def test_only_the_categories_that_wedge_are_named():
    """A category that drains must not be listed beside one that does not.

    The whole value of the sentence is that it sends an operator to the right
    setting; a list of every category would send them to all of them.
    """
    categories = {
        "categories": {
            "tv": {"share_limit_action": "Stop"},
            "films": {"share_limit_action": "EnableSuperSeeding"},
            "music": {"share_limit_action": "Default"},
        }
    }
    finding = check(qbt_with(**SLOTS, max_ratio_act=0, **categories), NO_GOALS)
    assert finding.outcome is Outcome.FAIL
    assert finding.detail == "the share-limit action frees no slot for category films"


def test_a_category_that_defers_is_not_named_beside_the_global_it_defers_to():
    """`Default` is the global's name here, not a second setting to go and change.

    The measured homelab stack has `tv` and `movies` both on `Default`, so
    naming every route that frees no slot listed all three for one wrong
    setting — and "category movies" reads as an accusation about a category
    configured exactly as intended.
    """
    categories = {
        "categories": {
            "tv": {"share_limit_action": "Default"},
            "movies": {"share_limit_action": "Default"},
        }
    }
    finding = check(qbt_with(**SLOTS, max_ratio_act=2, **categories), NO_GOALS)
    assert finding.outcome is Outcome.FAIL
    assert finding.detail == (
        "the share-limit action frees no slot for uncategorised torrents and "
        "every category that defers to the global"
    )


def test_an_unreadable_category_action_skips_rather_than_assuming_it_releases():
    """A category action nobody read cannot settle whether its torrents release.

    The same rule the global already followed, applied one level down. The
    global here is Stop, so the stack would PASS if this route were read
    optimistically — which is the shape that makes the assertion sharp.
    """
    for junk in (None, 5, True, ["Stop"]):
        finding = check(qbt_with(**SLOTS, max_ratio_act=0, **_tv(junk)), NO_GOALS)
        assert finding.outcome is Outcome.SKIP, f"{junk!r} as a category action was coerced"
        assert "qbt.share_limit_action_frees_no_slot" in {p.label for p in finding.premises}


def test_an_action_name_this_release_does_not_know_is_unknown_not_a_release():
    """A name outside `ACTION_BY_NAME` is a value we failed to read.

    Deliberately NOT the reading the allow-list gives an unmapped *integer*. A
    number is a value we have, and the dispatch we measured has no branch for
    it, so it frees nothing — `_ACTIONS`' `7` row. A name is a value we could
    not map at all, and the branch it would match is one nobody has read. The
    safe answer differs between the two, so the code does too.
    """
    finding = check(qbt_with(**SLOTS, max_ratio_act=0, **_tv("Pause")), NO_GOALS)
    assert finding.outcome is Outcome.SKIP
    assert finding.conflict == ACTION


def test_a_settled_wedge_survives_an_unreadable_neighbouring_category():
    """Three-valued OR: one route that frees nothing settles it.

    The mirror of `_any_of`'s rule everywhere else in this file. A category we
    could not parse must not take back a wedge another category already proved.
    """
    categories = {
        "categories": {
            "tv": {"share_limit_action": "EnableSuperSeeding"},
            "films": {"share_limit_action": None},
        }
    }
    finding = check(qbt_with(**SLOTS, max_ratio_act=0, **categories), NO_GOALS)
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == ACTION
    assert finding.detail == "the share-limit action frees no slot for category tv"


@pytest.mark.parametrize(
    "unreadable",
    [Unknown("field-absent", "categories"), [], "categories"],
    ids=["never-read", "a-list", "a-string"],
)
def test_an_unreadable_category_map_leaves_the_action_premise_unknown(unreadable):
    """A map we could not read may hold a category that frees nothing.

    Asserted on the predicate and not on `check`, which is deliberate and not
    laziness: an unreadable category map also leaves `SEEDING`'s
    `qbt.no_category_limits` unknown, and `SEEDING` is earlier in `_worst_of`,
    so it is the SKIP that surfaces either way. The honest answer is still
    required of this route — the ordering that hides it is not this
    predicate's to rely on, and a mutation that resolved the unread map to
    "no extra routes" survived the whole suite until this test existed.

    The second half is the one that must stay settled: a global that frees
    nothing is a wedge however unreadable the map is, which is the pre-#61
    verdict on every one of these shapes.
    """
    assert _action_frees_no_slot(qbt_with(**SLOTS, max_ratio_act=0, categories=unreadable)) is None
    assert _action_frees_no_slot(qbt_with(**SLOTS, max_ratio_act=2, categories=unreadable)) is True


def test_an_unreadable_category_map_cannot_take_back_a_global_wedge():
    """A global that frees nothing is a FAIL whether or not the map was read.

    Pins the one branch of `_action_frees_no_slot` that an unreadable map
    reaches, and keeps the pre-lintarr#61 verdict on it: the global route is
    readable and settles the premise on its own.
    """
    finding = check(
        qbt_with(**SLOTS, max_ratio_act=2, categories=Unknown("field-absent", "categories")),
        NO_GOALS,
    )
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == ACTION


def test_a_null_category_map_is_no_categories_for_the_action_route_too():
    """The exception `tests/test_properties.py` names, applied to this route.

    A null map is a client with no categories, so the global is the only route
    and a releasing one still PASSes. Reading it as unknown here would undo
    that exception by the back door.
    """
    assert (
        check(qbt_with(**SLOTS, max_ratio_act=0, categories=None), NO_GOALS).outcome is Outcome.PASS
    )
