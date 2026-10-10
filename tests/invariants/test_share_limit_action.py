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
once. `effectiveShareLimitAction()` redirects `Default` through
`categoryShareLimitAction()` to the global, and Sonarr sets no per-torrent
action, so the single `max_ratio_act` preference decides it — despite the name,
which is about the ratio arm only historically.

**Why an allow-list and not `!= 2`.** The dispatch above has four branches, so
`Default = -1` matches *none of them* and the torrent is left running exactly as
super seeding does. `Q_ASSERT(act != ShareLimitAction::Default)` guards the
setter and compiles out of release builds, and `appcontroller.cpp` publishes the
value through a bare `static_cast<int>`, so `-1` is observable over the API. A
denylist would read it as releasing and clear the conflict.
"""

import pytest

from lintarr.facts import Unknown
from lintarr.invariants.queue_liveness import _RELEASING_ACTIONS, ACTION, check
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
