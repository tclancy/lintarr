"""Every declared need must change a verdict somewhere, or the declaration lies.

A `NEEDS` entry nothing depends on is dead weight that makes SKIP fire for no
reason; a fact the predicate reads but does not declare escapes this check
entirely, which is why the pairs below are written by hand rather than derived
from the implementation.
"""

import pytest

from lintarr.invariants.queue_liveness import NEEDS, check
from tests.fixtures.homelab import qbt_with
from tests.invariants.test_queue_liveness import NO_GOALS, WITH_GOALS

# fact -> (overrides that should FAIL, overrides that should PASS)
#
# Each pair must differ in EXACTLY the named fact and produce different
# verdicts. Two traps to avoid, both of which the first draft of this table fell
# into: an override that merely restates what _WEDGE already sets is a no-op, so
# the two halves are identical and the pair proves nothing; and a need that the
# shipped predicate does not actually read cannot have a load-bearing pair at
# all — remove it from NEEDS instead of inventing one.
_PAIRS: dict[str, tuple[dict, dict]] = {
    "qbt.queueing_enabled": ({"queueing_enabled": True}, {"queueing_enabled": False}),
    # 5 binds so seeders can fill it; -1 is unlimited so they never can.
    "qbt.max_active_torrents": ({"max_active_torrents": 5}, {"max_active_torrents": -1}),
    "qbt.dont_count_slow_torrents": (
        {"dont_count_slow_torrents": False},
        {"dont_count_slow_torrents": True},
    ),
    "qbt.max_ratio_enabled": ({"max_ratio_enabled": False}, {"max_ratio_enabled": True}),
    "qbt.max_seeding_time_enabled": (
        {"max_seeding_time_enabled": False},
        {"max_seeding_time_enabled": True},
    ),
    # -2 inherits the global limits, so it does not break the wedge; a
    # category's own (>= 0) ratio limit releases its torrents' slots.
    "qbt.categories": (
        {"categories": {"tv": {"ratio_limit": -2, "seeding_time_limit": -2}}},
        {"categories": {"tv": {"ratio_limit": 2.0, "seeding_time_limit": -2}}},
    ),
}

# The seeding-conjunction wedge: limits bind, nothing releases a seeder.
# max_active_downloads stays at its healthy 6 here, because the shipped
# predicate deliberately excludes it from the seeding path — a finished torrent
# is not downloading, so download slots keep rotating regardless of seeders.
_WEDGE = {
    "max_active_torrents": 5,
    "dont_count_slow_torrents": False,
    "max_ratio_enabled": False,
    "max_seeding_time_enabled": False,
}

# Needs whose load-bearing pair cannot be expressed as a same-base diff against
# _WEDGE, each covered by its own dedicated test instead of an entry in
# _PAIRS: `qbt.max_active_downloads` only feeds the starvation short-circuit
# that runs BEFORE the seeding conjunction and never appears inside it, so any
# pair merged onto _WEDGE is already FAIL on both sides via the conjunction —
# it needs a healthy base instead. `arr.indexer_seed_criteria` needs a second
# arr fixture (WITH_GOALS), not a qbt override.
_TESTED_ELSEWHERE: frozenset[str] = frozenset(
    {"qbt.max_active_downloads", "arr.indexer_seed_criteria"}
)


def test_every_declared_need_has_a_pair():
    """A NEEDS entry with no pair is either untested or a lie. Both matter."""
    missing = [n for n in NEEDS if n not in _PAIRS and n not in _TESTED_ELSEWHERE]
    assert not missing, f"NEEDS entries with no load-bearing pair: {missing}"


def test_no_pair_names_a_fact_the_predicate_does_not_declare():
    """The reverse direction: a stale pair hides that a need was dropped."""
    stale = [n for n in _PAIRS if n not in NEEDS]
    assert not stale, f"_PAIRS entries not in NEEDS: {stale}"


@pytest.mark.parametrize("need", sorted(_PAIRS))
def test_each_need_changes_the_verdict(need):
    failing, passing = _PAIRS[need]
    a = check(qbt_with(**(_WEDGE | failing)), NO_GOALS).outcome
    b = check(qbt_with(**(_WEDGE | passing)), NO_GOALS).outcome
    if failing == passing:
        pytest.fail(f"{need}: the pair does not differ")
    assert a != b, f"{need} is declared in NEEDS but changes no verdict"


def test_the_arr_need_changes_the_verdict():
    wedged = qbt_with(**_WEDGE)
    assert check(wedged, NO_GOALS).outcome != check(wedged, WITH_GOALS).outcome


def test_max_active_downloads_changes_the_verdict():
    """0 starves the queue outright; the repaired default of 6 does not.

    Tested against a healthy base rather than _WEDGE: the seeding conjunction
    already reaches FAIL on max_active_torrents alone, so a pair merged onto
    _WEDGE would be FAIL on both sides regardless of this fact's value.
    """
    a = check(qbt_with(max_active_downloads=0), NO_GOALS).outcome
    b = check(qbt_with(max_active_downloads=6), NO_GOALS).outcome
    assert a != b, "qbt.max_active_downloads is declared in NEEDS but changes no verdict"
