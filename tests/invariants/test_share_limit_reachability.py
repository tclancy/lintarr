"""A limit on the far side of a defer chain must be range-checked too (lintarr#33).

#14 settled the range rule for ``seedCriteria`` and #28 taught the invariant to
tell *defer* from *override*. Both then trusted the values the defer chain hands
back without applying the same rule to them. Two false PASSes follow, and they
share one root: ``processTorrentShareLimits`` gates every arm on
``(limit >= 0)``, so whichever value arrives there decides, wherever it came
from.

**1. An unreadable criterion disarmed the three override routes, silently.**
``_overrides_the_share_limit`` was total and resolved "could not read" to *not
overriding*. For ``SEEDING`` that direction arms the check; for the override
routes it disarms it. So ``seedRatio: -1`` FAILed and ``seedRatio: "-1"`` — the
numeric string ``_is_a_readable_seed_criterion`` exists for — PASSed with an
**empty** detail, because the surviving finding was the starvation
fall-through and ``_note_unreadable_seed_criteria`` keys on
``SEED_CRITERIA_CONFLICTS``. An absent or null criterion is a different case
and still reads as *defer*: the measurement doc has ``-2``, absent and null all
redirecting through ``categoryRatioLimit()``.

**2. The global ratio value got none of the rules.** ``qbt.no_global_ratio``
read the ``max_ratio_enabled`` flag alone. Measured on ``release-5.2.4``:

- ``appcontroller.cpp:316`` derives the flag — ``data["max_ratio_enabled"] =
  (session->globalMaxRatio() >= 0.)`` — so it is not stored separately and
  ``enabled=True`` means exactly ``max_ratio >= 0``.
- ``Torrent::MAX_RATIO`` **is** ``infinity``, ``inf >= 0.`` is True, and
  ``setGlobalMaxRatio`` applies no upper clamp. So ``enabled=True`` with
  ``max_ratio=inf`` is reachable, and no finished arr-grabbed torrent's ratio
  reaches it. Stated with its condition on purpose: ``realRatio()`` *can*
  return ``MAX_RATIO`` — ``torrentimpl.cpp:1516-1531`` does so when effective
  download is ``0`` and upload is not — but ``processTorrentShareLimits`` runs
  only on a finished torrent, whose ``total_done`` is its size, so ``download``
  is non-zero and the ratio is finite.

That is the whole of the gap, and the ticket's own headline example is outside
it: ``enabled=True, max_ratio=-1`` **cannot be read from a live client**,
because the flag is derived from the value and ``setGlobalMaxRatio`` clamps
every negative to ``NO_RATIO_LIMIT``. It is covered below anyway, in the
defensive direction, with its unreachability recorded rather than asserted.

**The seeding-time arm provably needs nothing**, which is why only one new need
is declared. ``globalMaxSeedingMinutes()`` is an ``int``, so no non-finite value
can be stored, and ``setGlobalMaxSeedingMinutes`` is
``std::max(minutes, NO_SEEDING_TIME_LIMIT)`` — the only reachable negative is
``-1``, and ``-1`` makes the derived flag read False. This is the same argument
``_deferring_slot_premises`` already makes for the inactive gate; it holds for
the seeding-time preference and fails for the ratio alone.
"""

import pytest

from lintarr.invariants.queue_liveness import (
    SEED_CRITERIA_CONFLICTS,
    STARVATION,
    check,
)
from lintarr.outcomes import Outcome
from tests.fixtures.homelab import qbt_with
from tests.invariants.test_queue_liveness import _NO_RATIO, _arrs, _fact, _indexer

#: Slots that can fill, so every test below differs from a healthy stack only in
#: the share-limit facts it names.
SLOTS = {"max_active_torrents": 5, "dont_count_slow_torrents": False}

#: The stack the three override routes are reachable on: a global ratio limit is
#: ON, so ``SEEDING``'s conjunction cannot fire and only an override can.
_RATIO_ON = SLOTS | {
    "max_ratio_enabled": True,
    "max_ratio": 2.0,
    "max_seeding_time_enabled": False,
}

#: Present, non-null, and not a number. Each is a value an arr reported and
#: lintarr cannot read — distinct from absent/null, which mean *defer*.
_UNREADABLE = ["-1", "2.0", "unlimited", True, [], {}]

#: Readable and unreachable: returned verbatim into a ``>= 0`` gate. ``-2`` is
#: excluded on purpose — it is the one negative that defers.
_OVERRIDING = [-1, -5, -0.5, float("-inf"), float("inf")]


def _stack(ratio, seed_time=None, **qbt):
    seed_time = _NO_RATIO if seed_time is None else seed_time
    return (
        qbt_with(**(_RATIO_ON | qbt)),
        _arrs(_indexer(seed_ratio=ratio, seed_time=seed_time)),
    )


# ---------------------------------------------------------------- claim 1


@pytest.mark.parametrize("value", _UNREADABLE)
def test_an_unreadable_criterion_skips_the_override_routes(value):
    """Not a PASS. We could not read the criterion, so we cannot clear the stack."""
    finding = check(*_stack(_fact(value)))
    assert finding.outcome is Outcome.SKIP, (
        f"seed_ratio={value!r} reported {finding.outcome.name} on a stack whose "
        "override routes could not be decided"
    )


@pytest.mark.parametrize("value", _UNREADABLE)
def test_an_unreadable_criterion_says_which_indexer_could_not_be_read(value):
    """The silence was the worse half: the operator got an empty detail."""
    finding = check(*_stack(_fact(value)))
    assert finding.conflict in SEED_CRITERIA_CONFLICTS, (
        f"seed_ratio={value!r} surfaced {finding.conflict!r}, which carries no "
        "seed-criterion sentence"
    )
    assert "not a number" in finding.detail, f"seed_ratio={value!r} gave detail {finding.detail!r}"
    assert "1337x" in finding.detail


@pytest.mark.parametrize("value", _OVERRIDING)
def test_a_readable_override_still_fails(value):
    """The control for the above: #28's verdicts are unchanged."""
    finding = check(*_stack(_fact(value)))
    assert finding.outcome is Outcome.FAIL, f"seed_ratio={value!r} gave {finding.outcome.name}"
    assert finding.conflict in SEED_CRITERIA_CONFLICTS


def test_an_absent_criterion_still_defers_rather_than_skipping():
    """``-2``, absent and null all redirect, so none of them is undecidable.

    The direction guard on the change: resolving *unreadable* to unknown must
    not drag the deferring cases along with it, or every ordinary stack — Sonarr
    reports an unset goal by omitting it — becomes a SKIP.
    """
    for seed_ratio in (_NO_RATIO, _fact(None), _fact(-2)):
        finding = check(*_stack(seed_ratio))
        assert finding.outcome is Outcome.PASS, (
            f"{seed_ratio!r} gave {finding.outcome.name}; a deferring criterion is decidable"
        )


def test_an_unreadable_criterion_still_arms_the_deferring_route():
    """With both globals off, ``SEEDING`` FAILs as it did before #33.

    ``_lacks_seed_criteria`` is deliberately total and reads an unreadable
    criterion as "no goal". A FAIL outranks the override routes' SKIP in
    ``_worst_of``, so the flagship verdict is untouched.
    """
    qbt = qbt_with(**(SLOTS | {"max_ratio_enabled": False, "max_seeding_time_enabled": False}))
    finding = check(qbt, _arrs(_indexer(seed_ratio=_fact("-1"), seed_time=_NO_RATIO)))
    assert finding.outcome is Outcome.FAIL
    assert "not a number" in finding.detail


def test_a_usenet_indexer_with_an_unreadable_criterion_does_not_skip():
    """Unknown-ness must not leak past the quantifier's torrent-source test.

    An indexer that grabs nothing could not have contributed either way, so it
    is not allowed to force a SKIP however unreadable its criteria are.
    """
    qbt, _ = _stack(_NO_RATIO)
    arrs = _arrs(_indexer(seed_ratio=_fact("-1"), protocol="usenet"))
    finding = check(qbt, arrs)
    assert finding.outcome is Outcome.PASS
    # The half `test_a_clean_run_carries_no_note` can no longer reach: this is
    # now the only shape where a PASS coexists with an unreadable criterion, so
    # it is where "a PASS has nothing to explain" has to be pinned.
    assert finding.detail == "", f"a PASS carried a seed-criterion note: {finding.detail!r}"


# ------------------------------------------------- claim 1, surviving slice


def test_an_unreadable_companion_criterion_still_settles_the_route():
    """Recorded rather than fixed: the *other* position is still total.

    ``_overrides_the_ratio_limit`` is ``_all_of`` over a three-valued
    ``_overrides_the_share_limit(seed_ratio)`` and a **total**
    ``not _is_a_seed_goal(seed_time)``. So an unreadable *seed time* beside an
    overriding ratio resolves to "sets no goal" and the route reaches FAIL —
    where the honest answer, if that payload meant five minutes, is PASS.

    Left as it is for two reasons. It leans FAIL rather than PASS, which is the
    direction this project prefers, and the note still names the indexer so the
    operator is not told a value was read that was not. Making the companion
    three-valued is not a one-liner either: ``_lacks_seed_criteria`` depends on
    ``_is_a_seed_goal`` staying total, so it needs a second predicate rather
    than a widened one.

    This test exists so the behaviour is pinned and the claim in
    ``docs/measurements/2026-10-05-sonarr-seed-ratio-range.md`` gap 3 — closed
    in the ``seed_ratio`` position, surviving in the companion — is checkable
    rather than prose.
    """
    finding = check(*_stack(_fact(-1), seed_time=_fact("5")))
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict in SEED_CRITERIA_CONFLICTS
    assert "not a number" in finding.detail
    # The control: read as a number, the same value is a goal and clears it.
    assert check(*_stack(_fact(-1), seed_time=_fact(5))).outcome is Outcome.PASS


# ---------------------------------------------------------------- claim 2


def test_an_infinite_global_ratio_limit_releases_nothing():
    """``inf`` is ``-1`` wearing a positive sign, and the flag reads True on it.

    ``Torrent::MAX_RATIO`` is ``std::numeric_limits<qreal>::infinity()``
    (``torrent.cpp:47``), so this is a real configured value in qBittorrent's
    own vocabulary rather than a junk payload — which is the whole
    justification for reading the paired value at all. A test cannot see a C++
    constant, so that citation lives here rather than in an assertion; an
    earlier draft asserted ``math.isinf(float("inf"))``, which is a property of
    Python's float and pins nothing about qBittorrent.
    """
    qbt, arrs = _stack(_NO_RATIO, max_ratio=float("inf"))
    finding = check(qbt, arrs)
    assert finding.outcome is Outcome.FAIL, (
        f"max_ratio=inf with the flag on gave {finding.outcome.name}; "
        "no finished torrent's ratio reaches it"
    )
    assert finding.conflict in SEED_CRITERIA_CONFLICTS


def test_a_finite_global_ratio_limit_still_releases():
    """The control. A real global limit drains the seeders and clears the stack."""
    qbt, arrs = _stack(_NO_RATIO, max_ratio=2.0)
    assert check(qbt, arrs).outcome is Outcome.PASS


def test_an_unreadable_global_ratio_skips_rather_than_passing():
    """The flag says a limit is set; without the value we cannot tell if it is finite."""
    qbt, arrs = _stack(_NO_RATIO, max_ratio="unlimited")
    assert check(qbt, arrs).outcome is Outcome.SKIP


def test_the_global_ratio_value_is_not_read_when_the_flag_is_off():
    """A flag that is off settles the premise, so an unreadable value cannot SKIP it.

    Short-circuit rather than tidiness: ``max_ratio`` is only load-bearing when
    ``max_ratio_enabled`` is True, and reading it unconditionally would turn
    every client that omits the key into a SKIP on the flagship route.
    """
    qbt = qbt_with(
        **(
            SLOTS
            | {
                "max_ratio_enabled": False,
                "max_ratio": "unlimited",
                "max_seeding_time_enabled": False,
            }
        )
    )
    finding = check(qbt, _arrs(_indexer(seed_ratio=_NO_RATIO)))
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict in SEED_CRITERIA_CONFLICTS


def test_a_negative_global_ratio_is_handled_though_it_is_unreachable():
    """Recorded as unreachable rather than asserted as a live shape.

    ``appcontroller.cpp`` derives ``max_ratio_enabled`` from
    ``globalMaxRatio() >= 0.``, so this pair cannot come off a real client, and
    ``setGlobalMaxRatio`` clamps every negative to ``NO_RATIO_LIMIT`` so no
    other negative can be stored either. The range rule covers it for free;
    this pins the direction as "no limit" rather than leaving it to chance.
    """
    finding = check(*_stack(_NO_RATIO, max_ratio=-1))
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict in SEED_CRITERIA_CONFLICTS


def test_a_boolean_global_ratio_is_not_a_number():
    """``isinstance(True, int)`` is True in Python, so ``bool`` needs excluding.

    Added because a mutation round survived without it: dropping the ``bool``
    guard from ``_as_number`` left ``max_ratio=True`` reading as the limit
    ``1``, which ``_can_be_reached`` then clears as a perfectly good ratio of
    1.0 — a PASS invented out of a payload nobody can read. The same exclusion
    is already load-bearing in ``_as_limit`` and
    ``_is_a_readable_seed_criterion`` for this reason; this is the third
    instance of one rule, and the first to be driven.

    ``False`` is the sharper of the two: it would read as ``0``, and ``0`` is a
    *reachable* limit — satisfied the moment the torrent finishes — so coercing
    it manufactures the most aggressive release goal there is out of a bool.
    """
    for value in (True, False):
        qbt, arrs = _stack(_NO_RATIO, max_ratio=value)
        assert check(qbt, arrs).outcome is Outcome.SKIP, (
            f"max_ratio={value!r} was coerced to {int(value)} rather than refused"
        )


def test_the_starvation_route_is_untouched_by_the_global_ratio_rule():
    """A client that cannot start a first download still leads, limits or none."""
    qbt, arrs = _stack(_NO_RATIO, max_ratio=float("inf"), max_active_downloads=0)
    finding = check(qbt, arrs)
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == STARVATION


# ------------------------------------------------- claim 3, the inf sub-slice


def test_an_infinite_category_ratio_limit_is_not_the_categorys_own_limit():
    """``_own_share_limit`` used its own sign test, and ``inf`` passed it.

    lintarr#33's third claim is about the category leg of the defer chain, and
    most of it is still open — see the module's ``_own_share_limit`` docstring
    and gap 5 in the measurement doc. This slice is not, because it is exactly
    what the hoisted range rule fixes: ``value >= 0`` read ``inf`` as "this
    category releases its own torrents", which suppressed the ``SEEDING`` FAIL
    on a stack where nothing releases anything.

    Measured on this fixture with **both globals off**, so the only thing that
    could clear the stack is the category: before the hoist,
    ``ratio_limit: inf`` reported PASS. ``nan`` landed on FAIL already, for the
    accidental reason that every comparison against it is False; it is on the
    rule now rather than on luck.
    """
    both_globals_off = {"max_ratio_enabled": False, "max_seeding_time_enabled": False}

    def verdict(ratio_limit):
        qbt = qbt_with(
            **(
                SLOTS
                | both_globals_off
                | {
                    "categories": {
                        "tv-sonarr": {"ratio_limit": ratio_limit, "seeding_time_limit": -2}
                    }
                }
            )
        )
        return check(qbt, _arrs(_indexer(seed_ratio=_NO_RATIO, seed_time=_NO_RATIO))).outcome

    assert verdict(float("inf")) is Outcome.FAIL, "an infinite category limit releases nothing"
    assert verdict(float("nan")) is Outcome.FAIL
    # The control, and the reason this cannot just be `not value >= 0`: a real
    # category limit still breaks the wedge for everything filed under it.
    assert verdict(2.0) is Outcome.PASS
    assert verdict(0) is Outcome.PASS
