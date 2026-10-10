"""The third release gate: a global inactive-seeding-time limit drains the queue.

lintarr#32. ``processTorrentShareLimits`` (``release-5.2.4``,
``src/base/bittorrent/sessionimpl.cpp``) has **three** arms, not the two this
invariant modelled:

    if      (ratioLimit >= 0)             && (ratio >= ratioLimit)              -> reached
    else if (seedingTimeLimit >= 0)       && (seedingTimeInMinutes >= ...)       -> reached
    else if (inactiveSeedingTimeLimit >= 0) && (inactiveSeedingTimeInMinutes >= ...) -> reached

and ``TorrentImpl::effectiveInactiveSeedingTimeLimit()`` redirects the
``DEFAULT_SEEDING_TIME_LIMIT`` sentinel (``-2``) to
``categoryInactiveSeedingTimeLimit()``, which bottoms out at
``globalMaxInactiveSeedingMinutes()`` — the identical chain the other two axes
use. Sonarr sets no per-torrent inactive limit, so that arm is **always**
deferring to the global.

That makes this gate the one axis no indexer can take out of reach, which is why
``qbt.no_global_inactive_seed_time`` is in ``_slot_premises`` and reaches all
four seeder-absorption routes rather than sitting under any one of them. Before
it existed, each of those four could FAIL a stack the global drains.

**The exposure was latent on the recorded stack, not live.** Read off the live
homelab instance on 2026-10-06 (``GET /api/v2/app/preferences``, v5.2.3):
``max_inactive_seeding_time_enabled`` is ``False``. So homelab#393's FAIL was
*correct* — the gate was off there and released nothing. These tests pin both
directions, because only the pair distinguishes "models the gate" from "ignores
the gate".
"""

import pytest

from lintarr.facts import Unknown
from lintarr.invariants.queue_liveness import (
    OVERRIDE_BOTH,
    OVERRIDE_RATIO,
    OVERRIDE_SEED_TIME,
    SEED_CRITERIA_CONFLICTS,
    SEEDING,
    check,
)
from lintarr.outcomes import Outcome
from tests.fixtures.homelab import qbt_with, wedged_qbt
from tests.invariants.test_queue_liveness import _NO_RATIO, NO_GOALS, _arrs, _fact, _indexer
from tests.invariants.test_share_limit_override import SLOTS

#: One (arrs, conflict) pair per seeder-absorption route, each on a stack whose
#: *other* share-limit facts already satisfy that route. Built from the same
#: indexers the behaviour tests use so this cannot drift from them.
#:
#: ``SEEDING`` defers on every axis, so it needs both globals off; the three
#: override routes need the global they do not override left on, or they would
#: be reachable through ``SEEDING`` instead and the test would not be about the
#: route it names.
_ROUTES = [
    pytest.param(
        NO_GOALS,
        {"max_ratio_enabled": False, "max_seeding_time_enabled": False},
        SEEDING,
        id="seeding-defers-on-every-axis",
    ),
    pytest.param(
        _arrs(_indexer(seed_ratio=_fact(-1), seed_time=_fact(-1))),
        {},
        OVERRIDE_BOTH,
        id="override-both",
    ),
    pytest.param(
        _arrs(_indexer(seed_ratio=_fact(-1), seed_time=_NO_RATIO)),
        {"max_seeding_time_enabled": False},
        OVERRIDE_RATIO,
        id="override-ratio",
    ),
    pytest.param(
        _arrs(_indexer(seed_ratio=_NO_RATIO, seed_time=_fact(-1))),
        {"max_ratio_enabled": False},
        OVERRIDE_SEED_TIME,
        id="override-seed-time",
    ),
]


@pytest.mark.parametrize(("arrs", "globals_", "conflict"), _ROUTES)
def test_the_gate_off_still_reaches_the_route(arrs, globals_, conflict):
    """The control. With the third gate off, every route FAILs exactly as before.

    Without this half, disabling the routes entirely would pass the test below.
    """
    qbt = qbt_with(**(SLOTS | globals_ | {"max_inactive_seeding_time_enabled": False}))
    finding = check(qbt, arrs)
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == conflict


@pytest.mark.parametrize(("arrs", "globals_", "conflict"), _ROUTES)
def test_a_global_inactive_seed_time_limit_clears_every_route(arrs, globals_, conflict):
    """The fix. The same stack with the gate ON drains, so no route may FAIL it.

    ``conflict`` is unused in the assertion deliberately — the point is that
    *nothing* fires, not that this particular route stopped firing.
    """
    qbt = qbt_with(**(SLOTS | globals_ | {"max_inactive_seeding_time_enabled": True}))
    finding = check(qbt, arrs)
    assert finding.outcome is Outcome.PASS, (
        f"{conflict} should PASS a stack whose global inactive-seeding-time "
        f"limit releases the seeders, got {finding.outcome} with "
        f"{[p.label for p in finding.premises]}"
    )


@pytest.mark.parametrize(("arrs", "globals_", "conflict"), _ROUTES)
def test_the_premise_is_reported_by_name_on_every_route(arrs, globals_, conflict):
    """A premise that fires but is never published cannot be read by an operator."""
    qbt = qbt_with(**(SLOTS | globals_ | {"max_inactive_seeding_time_enabled": False}))
    labels = {p.label for p in check(qbt, arrs).premises}
    assert "qbt.no_global_inactive_seed_time" in labels, conflict


def test_routes_covers_every_conflict_that_shares_the_premise():
    """A fifth seeder-absorption route must fail HERE, not escape silently.

    `_ROUTES` is hand-written, so without this a route added to
    `_deferring_slot_premises` would inherit the premise with nothing above
    exercising it. `SEED_CRITERIA_CONFLICTS` is published and is exactly the four
    routes that compose on it; ACTION is deliberately not among them, because it
    takes `_slot_premises` and must not inherit this premise.
    """
    assert {p.values[2] for p in _ROUTES} == SEED_CRITERIA_CONFLICTS


def test_homelab_393_is_unaffected_because_its_gate_was_off():
    """The flagship evidence still FAILs, and the live read is why.

    `max_inactive_seeding_time_enabled` was not recorded in #393; it is carried
    as the live-read `False` under the fixture's own `max_active_uploads`
    precedent. Had it been `True`, this premise would have turned the one
    configuration known to have wedged production into a PASS — so this is the
    test that makes the fixture value load-bearing rather than cosmetic.
    """
    finding = check(wedged_qbt(), NO_GOALS)
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == SEEDING
    assert "qbt.no_global_inactive_seed_time" in {p.label for p in finding.premises}


_WEDGE_GLOBALS = {"max_ratio_enabled": False, "max_seeding_time_enabled": False}


def _gate(reason):
    return Unknown(reason, "max_inactive_seeding_time_enabled")


def test_a_permission_error_on_the_gate_skips_rather_than_deciding():
    """`insufficient-permission` means the gate may well be armed and we cannot see it.

    Undecidable, so the route must SKIP — not PASS (which would claim a release
    path we never read) and not FAIL (which would claim there is none).
    """
    qbt = qbt_with(
        **(
            SLOTS
            | _WEDGE_GLOBALS
            | {"max_inactive_seeding_time_enabled": _gate("insufficient-permission")}
        )
    )
    finding = check(qbt, NO_GOALS)
    assert finding.outcome is Outcome.SKIP
    assert "qbt.no_global_inactive_seed_time" in {p.label for p in finding.premises}


def test_a_pre_4_6_client_still_reports_the_393_wedge():
    """`field-absent` is resolved, not undecidable — and this is why it must be.

    `max_inactive_seeding_time_enabled` landed in qBittorrent 4.6. On any older
    client the key is simply absent, which the collector turns into
    `Unknown(field-absent)`. Were that read as undecidable, all four
    seeder-absorption routes would SKIP on every pre-4.6 client — Debian bookworm
    ships 4.5.2 — and lintarr would go quiet on the exact configuration
    homelab#393 recorded. A client with no third arm cannot be released by it, so
    the gate is provably off and the FAIL stands.
    """
    qbt = qbt_with(
        **(SLOTS | _WEDGE_GLOBALS | {"max_inactive_seeding_time_enabled": _gate("field-absent")})
    )
    finding = check(qbt, NO_GOALS)
    assert finding.outcome is Outcome.FAIL, (
        f"a pre-4.6 client went {finding.outcome} on the #393 wedge shape"
    )
    assert finding.conflict == SEEDING
