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
  ``max_ratio=inf`` is reachable, and ``realRatio() >= inf`` is never true.

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

import math

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
    assert check(qbt, arrs).outcome is Outcome.PASS


# ---------------------------------------------------------------- claim 2


def test_an_infinite_global_ratio_limit_releases_nothing():
    """``inf`` is ``-1`` wearing a positive sign, and the flag reads True on it."""
    qbt, arrs = _stack(_NO_RATIO, max_ratio=float("inf"))
    finding = check(qbt, arrs)
    assert finding.outcome is Outcome.FAIL, (
        f"max_ratio=inf with the flag on gave {finding.outcome.name}; "
        "realRatio() can never reach it"
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
    assert check(qbt, _arrs(_indexer(seed_ratio=_NO_RATIO))).outcome is Outcome.FAIL


def test_a_negative_global_ratio_is_handled_though_it_is_unreachable():
    """Recorded as unreachable rather than asserted as a live shape.

    ``appcontroller.cpp`` derives ``max_ratio_enabled`` from
    ``globalMaxRatio() >= 0.``, so this pair cannot come off a real client, and
    ``setGlobalMaxRatio`` clamps every negative to ``NO_RATIO_LIMIT`` so no
    other negative can be stored either. The range rule covers it for free;
    this pins the direction as "no limit" rather than leaving it to chance.
    """
    qbt, arrs = _stack(_NO_RATIO, max_ratio=-1)
    assert check(qbt, arrs).outcome is Outcome.FAIL


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


def test_qbittorrents_own_max_ratio_constant_is_not_a_reachable_limit():
    """``Torrent::MAX_RATIO`` is ``std::numeric_limits<qreal>::infinity()``.

    So "the maximum ratio" in qBittorrent's own vocabulary is the one value a
    ratio can never reach. Pinned here because the rule's whole justification
    is that this is a real configured value and not a junk payload.
    """
    assert math.isinf(float("inf"))
    qbt, arrs = _stack(_NO_RATIO, max_ratio=float("inf"))
    assert check(qbt, arrs).outcome is Outcome.FAIL
