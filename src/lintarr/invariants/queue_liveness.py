"""Can any queued download ever start?

The motivating incident, homelab#393: qBittorrent had max_active_torrents=5
with both share limits disabled. Seeding torrents count against that limit, so
once five torrents completed they held every slot permanently. 52 torrents,
zero active downloads, zero kB/s, no error anywhere, for weeks.

This is a claim about traces, but its reasoning is a monotone quantity with an
absorbing state — completed count only rises, seeders never release slots, and
past a threshold no slot is ever free again — so it collapses to a closed form.
The derivation is validated against an executable model in
tests/invariants/test_queue_liveness_sweep.py.

Two distinct ways a client can end up unable to start a download, and they need
different shapes:

1. **No slot for a first download.** ``max_active_downloads`` or
   ``max_active_torrents`` is set at or below zero, so nothing starts even on an
   idle client. Nothing about seeding is required, so no seeding premise may be
   allowed to excuse it.
2. **Seeders absorb every slot.** ``max_active_torrents`` binds, seeding
   torrents count against it, and nothing ever makes a seeder stop. This is
   #393.

They are reported as one invariant because they answer the same operator
question. A conjunction cannot express "or", so each is decided separately and
they are combined as a three-valued disjunction: FAIL if any proves the wedge,
SKIP only if none proves it and something could not be read, PASS otherwise.
Anything cruder loses verdicts it had already proved — a single unreadable
preference must not silence a wedge established without it.

**(2) is four conjunctions, not one** (lintarr#28). "Nothing ever makes a
seeder stop" is not a single claim, because a torrent carries its own ratio and
seeding-time limits and is released as soon as *either* is reached. Each limit
is in one of three states, measured on qBittorrent ``release-5.2.4``:

- a **goal** (within ``[0, MAX_REACHABLE_SEED_RATIO]``) — the slot is
  released, no wedge;
- **deferring** (``-2``, absent, or null) — ``effectiveRatioLimit()`` redirects
  it to ``categoryRatioLimit(category())``, which is the only route to
  ``globalMaxRatio()``, so the global or category setting decides;
- **overriding** (any other negative, ``nan``, and anything *above* the
  measured ceiling — ``inf`` included) — returned verbatim into a gate it can
  never pass, at one end or the other. The redirect never happens, so the
  global is never consulted and **cannot** release the slot. The upper end is
  lintarr#43; see ``MAX_REACHABLE_SEED_RATIO``.

A criterion nobody can read is a **fourth** state, not one of these, and the
distinction matters because the two sides want opposite defaults for it.
``_lacks_seed_criteria`` reads it as "no goal", which *arms* ``SEEDING``;
``_overrides_the_share_limit`` reads it as "not an override", which *disarms*
the three routes below. So an indexer reporting ``-1`` as the string ``"-1"`` —
the shape ``_is_a_readable_seed_criterion`` exists for — was a silent PASS on a
stack a readable ``-1`` FAILs, with no note to explain it.

**Closed by lintarr#33.** ``_overrides_the_share_limit`` is three-valued now:
present-but-unreadable returns ``None``, so the override routes SKIP instead of
PASSing, and the SKIP carries an ``OVERRIDE_``… conflict — which is in
``SEED_CRITERIA_CONFLICTS``, so the note reaches it and names the indexer. The
``None`` is scoped to that one state; absent and null still read as *defer*,
because they are. The cost is stated rather than hidden: a stack that is
otherwise healthy now SKIPs when an indexer reports a criterion nobody can
read, where it used to PASS. That is this project's standing rule for an unread
input applied to seed criteria at last — see
``tests/test_properties.py::test_a_qbt_preference_that_was_never_read_never_passes``,
which states it on the same healthy base — and not a new policy.

The deferring and overriding cases therefore need *different premises about
qBittorrent*, and the two limits can be in different states at once. Hence
``SEEDING`` plus three ``OVERRIDE_``… conflicts. Folding them into one
conjunction is what left three wedging configurations at PASS until #28 — and
collapsing them into one premise instead would cost the explanation, since in
this project the premise set that fired *is* the remedy. A finding that listed
``qbt.no_global_ratio`` on an overridden ratio would be naming a setting that
never runs.

Two things this file assumes and cannot yet prove, both P2 conformance work
against a live client:

- **The wedge axiom.** That seeders can absorb *every* ``max_active_torrents``
  slot is taken from documentation plus one observed incident. It has never
  been validated against a running qBittorrent, and ``max_active_uploads``
  together with libtorrent's allocation order may bound how many slots seeders
  actually reach. If it does, this check is too eager in a way no sweep here
  can see, because the model shares the assumption.
- **Absent seed criteria are read as unset.** Sonarr 4.0.19 lists
  ``seedCriteria.seedRatio`` in an indexer's fields but omits the ``value`` key
  entirely when no goal is set, so "operator left it unset" and "this build does
  not expose it" arrive as the same thing: ``Unknown(field-absent)``. This file
  reads that as "no goal", because on every real stack measured so far it is.
  The cost of the alternative is total: treating it as undecidable would make
  ``queue-liveness`` SKIP on essentially every stack, including the one that
  motivated it.
"""

from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

from lintarr.facts import Fact, Unknown, is_known
from lintarr.invariants.combinator import conflict_if, premise
from lintarr.models import ArrInstance, IndexerFacts, QbtInstance
from lintarr.outcomes import Finding, Outcome, Premise

INVARIANT_ID = "queue-liveness"

NEEDS: tuple[str, ...] = (
    "qbt.queueing_enabled",
    "qbt.max_active_downloads",
    "qbt.max_active_torrents",
    "qbt.dont_count_slow_torrents",
    "qbt.max_ratio_enabled",
    # The paired VALUE, declared because the flag is derived from it and the two
    # disagree on one reachable input. `appcontroller.cpp` computes
    # `max_ratio_enabled` as `globalMaxRatio() >= 0.`, and `inf >= 0.` is True —
    # so the flag reads "a limit is set" on a limit no finished torrent's ratio
    # reaches. Read only when the flag is on; see `_no_global_ratio` for why
    # `max_seeding_time` gets no such entry (lintarr#33).
    "qbt.max_ratio",
    "qbt.max_seeding_time_enabled",
    # The action applied when any gate fires. Declared because an unread value
    # is genuinely undecidable — `conflict_if` lets an unknown premise dominate,
    # so `ACTION` SKIPs — and an undeclared fact the predicate reads escapes
    # tests/test_needs_are_load_bearing.py entirely.
    "qbt.max_ratio_act",
    #
    # `qbt.max_inactive_seeding_time_enabled` is read by every seeder-absorption
    # route and is deliberately NOT declared here. A need asserts "this cannot be
    # decided without the fact", and `_not_inactive_gate` resolves the
    # `field-absent` case on purpose: the preference postdates qBittorrent 4.6,
    # so a client that omits it has no third arm and the gate is provably off.
    # Declaring it would make that resolution a lie — and
    # tests/test_properties.py's never-read property, whose sentinel is
    # `Unknown("field-absent")`, would fail against exactly the behaviour
    # lintarr#32 wanted. The `insufficient-permission` case does still SKIP, and
    # is pinned by tests/invariants/test_inactive_seed_time_gate.py rather than
    # by the NEEDS table.
    "qbt.categories",
    # Three separate reads, not one. An indexer's protocol, its enable toggles
    # and its seed criteria each independently flip the verdict, so declaring
    # only the last would leave two facts undeclared and outside the
    # load-bearing gate.
    "arr.indexer_protocol",
    "arr.indexer_enabled",
    "arr.indexer_seed_criteria",
)

#: The only value meaning "no limit". Every other value binds, including ``0``
#: and every other negative number: a limit of ``-2`` can never be satisfied by
#: a count, so it wedges immediately. Writing ``limit < 0`` here instead would
#: silently disagree with the model on every negative-but-not-``-1`` value.
UNLIMITED = -1

#: A category share limit of ``-2`` means "use the global setting", so it is not
#: a limit of the category's own. Measured on qBittorrent 5.2.3.
USE_GLOBAL = -2

#: The largest seed ratio a torrent that holds data can ever reach, and so the
#: largest ratio limit one can ever satisfy. ``realRatio()``'s finite branch is
#: ``all_time_upload / (qreal)download`` with both ``int64_t`` and
#: ``download >= 1``, so its supremum is ``(double)INT64_MAX`` — which is
#: exactly ``2.0**63`` as a double, and is *attained* (``INT64_MAX / 1.0`` is
#: that double), which is why the bound using it is inclusive.
#:
#: Measured, not guessed: lintarr#43 asked for this number before any magnitude
#: rule could be written, and
#: ``docs/measurements/2026-10-12-qbittorrent-real-ratio-ceiling.md`` is the
#: read of qBittorrent ``release-5.2.4`` that answers it.
MAX_REACHABLE_SEED_RATIO = 2.0**63


def _binds(limit: int) -> bool:
    """True when *limit* constrains anything at all."""
    return limit != UNLIMITED


def _any_of(states: Iterable[bool | None]) -> bool | None:
    """Three-valued OR: one True settles it however much else is unknown.

    ``True or Unknown`` is True, not Unknown. Checking for unknowns first — the
    obvious shape, and the one this file shipped with — throws away a verdict
    already proved, which for a checker means reporting "could not look" at a
    configuration it could see was broken.
    """
    seen = tuple(states)
    if any(state is True for state in seen):
        return True
    return None if any(state is None for state in seen) else False


def _all_of(states: Iterable[bool | None]) -> bool | None:
    """Three-valued AND: one False settles it however much else is unknown.

    The mirror of ``_any_of``, and needed for the same reason: a conjunct we
    could not read must not be silently resolved to the value that clears the
    check. Where ``_any_of`` lets one True settle a disjunction, one False
    settles a conjunction — a route that cannot fire does not care which of its
    other inputs were unreadable.

    An empty iterable is vacuously True, matching the ``all()`` it replaces.

    **This dominates the opposite way from ``conflict_if``, and the two are not
    in conflict.** ``conflict_if`` lets an *unknown* premise dominate a False
    one, so a route with an unreadable input SKIPs rather than certifying a
    stack. That is a policy about what we are willing to publish. This is
    arithmetic about what a premise's value *is*, one layer below: a conjunct
    read as False means the shape genuinely does not match, so the premise is
    False and ``conflict_if`` then applies its own rule to it. Reordering
    ``conflict_if`` to match this function turns 143 tests red, and
    ``tests/test_properties.py::test_a_qbt_preference_read_as_null_never_passes``
    is the one that says why.
    """
    seen = tuple(states)
    if any(state is False for state in seen):
        return False
    return None if any(state is None for state in seen) else True


def _can_be_reached(limit: int | float) -> bool:
    """True when a count can ever satisfy qBittorrent's ``(limit >= 0)`` gate.

    The range rule, hoisted out of ``_is_a_seed_goal`` so that every place a
    limit arrives applies the same one (lintarr#33). Every arm of
    ``processTorrentShareLimits`` is ``(limit >= 0) && (count >= limit)``, and
    whichever value reaches it decides. **Four call sites, five field
    shapes** — the indexer's own ``seedCriteria.seedRatio`` and
    ``seedCriteria.seedTime``, ``globalMaxRatio()``, and a category's
    ``ratio_limit`` *and* ``seeding_time_limit``, the last two both through
    ``_own_share_limit``. An earlier version of this paragraph said "three
    places" and enumerated only the ratio ones, which is the omission that
    made the category seeding-time path easy to miss below. Hand-written sign
    tests at each site were that many places for one rule to drift, and that
    drift is what lintarr#33 reported.

    ``inf`` is the case a bare ``>= 0`` gets wrong, and it is not theoretical:
    ``Torrent::MAX_RATIO`` **is** ``std::numeric_limits<qreal>::infinity()``, so
    qBittorrent's own "maximum ratio" is a limit no torrent this project lints
    can reach. ``json`` also decodes both ``1e400`` and the non-standard
    ``Infinity`` token to ``inf``.

    **Stated with its condition, because the absolute is false.**
    ``realRatio()`` *can* return ``MAX_RATIO`` — ``torrentimpl.cpp:1516-1531``
    returns it when effective download is ``0`` and upload is not::

        if (download == 0)
            return (upload == 0) ? 0 : MAX_RATIO;

    so ``inf >= inf`` holds and the ratio arm fires. What makes ``inf``
    unreachable *here* is the population rather than the arithmetic:
    ``processTorrentShareLimits`` only runs on a finished torrent, whose
    ``total_done`` is its size, and ``download`` is derived from
    ``total_done`` when ``all_time_download`` is implausibly small — so an
    arr-grabbed torrent that finished has a finite ratio. An earlier draft of
    this docstring asserted the absolute in eight places; a reviewer read
    ``realRatio()``'s body and it says otherwise.

    ``nan`` needs nothing added: every comparison against it is False, so it
    fails the range test already. Recorded so that stays a known property
    rather than a lucky one.

    **The upper bound arrived here in a merge resolve, not in a reviewed diff
    — read this paragraph before trusting it.** lintarr#43 (PR #44) measured
    the ceiling and applied it inline in ``_is_a_seed_goal``; lintarr#33
    (PR #40) hoisted the range rule into this function. The two landed in the
    same week against the same line and neither review saw the other. Taking
    either side whole was wrong: main's body has no ceiling, so ``1e308`` reads
    as a goal again and #44's entire fix is lost; #44's inline form keeps the
    fix but leaves the *other two* arrival points — ``globalMaxRatio()`` and
    ``categoryRatioLimit()`` — unbounded above, which is lintarr#33's drift
    re-created and the identical false PASS arriving by a different route.

    It belongs here because the ceiling is a property of ``realRatio()``, so it
    is exact for the **ratio** arm wherever that value came from — the
    indexer's criterion, ``globalMaxRatio()`` or a category's ``ratio_limit``
    alike — exactly like the sign rule this function already owns.

    **Only the first arm compares against ``realRatio()``, so the seeding-time
    arms need their own argument and get one below.** An earlier version of
    this paragraph said *every* arm compares against ``realRatio()``, which is
    false against the source it cites: ``sessionimpl.cpp:2383-2404`` on
    ``release-5.2.4`` has three arms, and the second and third compare
    ``finishedTime() / 60`` and ``timeSinceActivity() / 60``. The sibling claim
    one paragraph up — that every arm has the *shape*
    ``(limit >= 0) && (count >= limit)`` — is the true one. Getting these two
    confused is what makes the next paragraph look redundant when it is
    load-bearing.

    **It is inert for both seeding-time arms that come through here**, and for
    both of them that is measured rather than assumed. Each is bounded by
    Int32, and ``MAX_REACHABLE_SEED_RATIO`` is ``4294967298.0`` times larger
    than ``2**31 - 1`` — more than nine orders of magnitude:

    - the indexer's ``seedCriteria.seedTime``, where Sonarr declares
      ``public int? SeedTime`` (``SeedCriteriaSettings.cs``). Pinned by
      ``test_the_largest_representable_seed_time_is_still_a_seed_goal``.
    - a category's ``seeding_time_limit``, which is **not** a Sonarr value and
      which an earlier version of this paragraph did not reach at all:
      qBittorrent declares ``int seedingTimeLimit``
      (``categoryoptions.h:49``) and reads it back as ``const int``
      (``sessionimpl.cpp:2381``). Pinned by
      ``test_the_largest_category_seeding_time_limit_is_still_the_categorys_own``.

    If this bound ever has to drop near ``2**31``, the ratio and time criteria
    need splitting into two predicates with one shared sign rule — and both
    pins above are what will go red to say so.

    ``nan`` and ``inf`` need nothing added beyond the bound. Every comparison
    against ``nan`` is False, so it fails both ends; ``inf`` fails the upper
    end. Both were previously handled by a separate ``math.isfinite`` guard,
    which the bound subsumes — and that retired the guard's own
    ``isinstance(..., float)`` workaround with it. (That workaround existed
    because ``math.isfinite`` raises ``OverflowError`` rather than answering on
    an ``int`` too large for a ``float``, and ``json`` decodes a 401-digit
    literal to exactly such an ``int``. A Python ``int`` compares against a
    ``float`` exactly and never raises, so the comparison form has no such
    hole.) This is why the merge's auto-resolved ``import math`` removal was
    *correct* rather than a landmine: #44 deleted the import because its bound
    subsumes the guard, and that holds wherever the guard lives.
    """
    return 0 <= limit <= MAX_REACHABLE_SEED_RATIO


def _truth(fact: Fact[bool]) -> bool | None:
    """A boolean fact as a three-valued state, or ``None`` if it has no value.

    ``Known(None)`` collapses to unknown alongside ``Unknown``. A key read back
    as null is a value we cannot use, and ``combinator.premise`` already treats
    the two the same way at the premise layer; letting ``None`` fall through to
    ``bool()`` here would settle a premise from a setting nobody ever set —
    exactly the defaulting this project refuses.
    """
    if not is_known(fact) or fact.value is None:
        return None
    return bool(fact.value)


def _not(fact: Fact[bool]) -> bool | None:
    """Negate a boolean fact, preserving unknown-ness."""
    state = _truth(fact)
    return None if state is None else not state


def _not_inactive_gate(qbt: QbtInstance) -> bool | None:
    """True when no global inactive-seeding-time limit is armed.

    ``field-absent`` reads as "not armed" rather than undecidable, and that
    distinction is load-bearing: the preference landed in qBittorrent **4.6**, so
    a client that does not report the key has no third arm to arm. Treating the
    absence as unknown would turn all four seeder-absorption routes into SKIP on
    every pre-4.6 client — Debian bookworm ships 4.5.2 — including on the exact
    configuration homelab#393 recorded, which is the one incident this invariant
    exists to catch.

    ``insufficient-permission`` stays undecidable, because there the gate may
    well be armed and we simply cannot see it. ``facts.UnknownReason`` exists to
    tell those two apart and nothing else in this file had needed it;
    ``_own_share_limit`` already resolves absence-as-information the same way for
    categories, so this is the second instance of one rule rather than a one-off.
    """
    fact = qbt.max_inactive_seeding_time_enabled
    if isinstance(fact, Unknown) and fact.reason == "field-absent":
        return True
    return _not(fact)


def _no_global_ratio(qbt: QbtInstance) -> bool | None:
    """True when the global ratio limit can never release a slot.

    The flag alone was not enough, and lintarr#33 is where that was measured.
    ``appcontroller.cpp`` **derives** the flag rather than storing it::

        data[u"max_ratio_enabled"_s] = (session->globalMaxRatio() >= 0.);
        data[u"max_ratio"_s] = session->globalMaxRatio();

    So ``max_ratio_enabled`` means exactly ``max_ratio >= 0`` — which is almost
    the arm's own firing condition, and differs from it on one reachable value.
    ``Torrent::MAX_RATIO`` is ``std::numeric_limits<qreal>::infinity()``,
    ``setGlobalMaxRatio`` applies no upper clamp, and ``inf >= 0.`` is True. So
    the flag reads True on a global limit no finished arr-grabbed torrent's
    ratio reaches — see ``_can_be_reached`` for why that is a claim about the
    population and not about ``inf`` arithmetic — and ``qbt.no_global_ratio``
    read it as a setting that drains the queue.

    **The reads are ordered, and that is load-bearing rather than tidy.** The
    value is only needed when the flag is on, so a flag that is off settles the
    premise without it. Reading ``max_ratio`` unconditionally would make every
    client that omits the key SKIP the flagship route — the same false-SKIP this
    file refuses for ``_not_inactive_gate``'s ``field-absent`` case.

    **No sibling for ``max_seeding_time``, and that is measured too.**
    ``globalMaxSeedingMinutes()`` is an ``int``, so it can hold no non-finite
    value, and ``setGlobalMaxSeedingMinutes`` is
    ``std::max(minutes, NO_SEEDING_TIME_LIMIT)`` — the only reachable negative
    is ``-1``, and ``-1`` makes the derived flag read False. The derivation
    argument ``_deferring_slot_premises`` already makes for the inactive gate
    therefore holds exactly for the seeding-time preference and fails for the
    ratio alone. Adding a second rule would declare a need nothing can drive.

    A negative ``max_ratio`` beside an on flag is **unreachable** in both
    directions — the flag is derived from the value, and ``setGlobalMaxRatio``
    clamps ``ratio < 0`` to ``NO_RATIO_LIMIT``. ``_can_be_reached`` covers it
    for free; no premise is built on its reachability.
    """
    armed = _truth(qbt.max_ratio_enabled)
    if armed is not True:
        return None if armed is None else True
    limit = _as_number(qbt.max_ratio)
    return None if limit is None else not _can_be_reached(limit)


def _as_number(fact: Fact[Any]) -> float | None:
    """The number a limit was read as, or ``None`` if it was not read as one.

    ``_as_limit``'s float-tolerant sibling. The two are kept apart rather than
    widened into one because what counts as readable differs by field:
    ``max_ratio`` is a ``qreal`` and ``max_ratio_act`` is an enum, so accepting
    a float there would let ``1.5`` be compared against an action.
    """
    if not is_known(fact) or isinstance(fact.value, bool):
        return None
    return fact.value if isinstance(fact.value, (int, float)) else None


#: ``ShareLimitAction`` values that actually free an active slot, read from
#: ``src/base/bittorrent/sharelimits.h`` on ``release-5.2.4``:
#: ``Default = -1``, ``Stop = 0``, ``Remove = 1``, ``EnableSuperSeeding = 2``,
#: ``RemoveWithContent = 3``.
#:
#: An **allow-list**, not ``!= 2``. Two values free nothing and only one of them
#: is ``EnableSuperSeeding``: ``processTorrentShareLimits``' action dispatch is a
#: four-branch ``if``/``else if`` over Stop, Remove, RemoveWithContent and
#: EnableSuperSeeding, so ``Default`` — and any value a future release adds —
#: matches **no branch at all** and the torrent is left running. ``Default`` is
#: guarded at the setter by ``Q_ASSERT(act != ShareLimitAction::Default)``, which
#: is compiled out of release builds, and ``appcontroller.cpp`` publishes the
#: value through a bare ``static_cast<int>`` — so ``-1`` is observable over the
#: API. A denylist would read ``-1`` as releasing and clear the conflict.
_RELEASING_ACTIONS: frozenset[int] = frozenset({0, 1, 3})


def _action_frees_no_slot(qbt: QbtInstance) -> bool | None:
    """True when the configured share-limit action leaves the torrent running.

    ``max_ratio_act`` is misleadingly named: it is the **global** action for all
    three arms, not the ratio arm's. ``effectiveShareLimitAction()`` redirects
    ``Default`` through ``categoryShareLimitAction()`` to it, and Sonarr sets no
    per-torrent action, so this one value decides what reaching *any* limit does.
    """
    act = _as_limit(qbt.max_ratio_act)
    return None if act is None else act not in _RELEASING_ACTIONS


def _as_limit(fact: Fact[int]) -> int | None:
    """The integer a limit was read as, or ``None`` if it was not read as one.

    A limit that came back as a string, a null or a bool is a fact we do not
    have. Coercing it would invent a number the client never reported.
    """
    if not is_known(fact) or isinstance(fact.value, bool) or not isinstance(fact.value, int):
        return None
    return fact.value


def _blocks_a_first_start(limit: Fact[int]) -> bool | None:
    """True when this limit alone stops an idle client starting anything.

    With zero torrents running, a limit blocks the first start when it binds and
    sits at or below zero.
    """
    value = _as_limit(limit)
    if value is None:
        return None
    return _binds(value) and value <= 0


def _no_slot_for_a_first_download(qbt: QbtInstance) -> bool | None:
    """True when an *idle* client still cannot start anything.

    Either limit is enough on its own, so an unreadable one cannot take back a
    verdict the other already settled: ``max_active_torrents=0`` is conclusive
    whether or not ``max_active_downloads`` could be read.

    ``max_active_uploads`` is not consulted: it gates seeding slots, and a
    seeder that cannot get one does not hold a download back.
    """
    return _any_of(
        (
            _blocks_a_first_start(qbt.max_active_torrents),
            _blocks_a_first_start(qbt.max_active_downloads),
        )
    )


def _max_active_torrents_binds(qbt: QbtInstance) -> bool | None:
    """True when seeders can accumulate into a slot shortage.

    Only ``max_active_torrents`` can do that. Seeding torrents count against it
    and never against ``max_active_downloads``, so a bounded download limit
    keeps rotating however many seeders pile up — flagging it would fail almost
    every healthy stack. ``max_active_uploads`` gates seeding slots, not
    downloads, and cannot wedge the queue either.
    """
    total = _as_limit(qbt.max_active_torrents)
    if total is None:
        return None
    return _binds(total)


#: Every top-level toggle that can put a torrent from this indexer into the
#: queue. All three count. Interactive search is not a lesser one: an operator
#: can hand-pick a release from an indexer whose RSS and automatic search are
#: both off, and that torrent then seeds exactly like any other. Reading only
#: the first two classified such an indexer as not-a-torrent-source and dropped
#: it from the predicate, which is a PASS on a stack that can wedge.
TORRENT_TOGGLES: tuple[str, ...] = (
    "enable_rss",
    "enable_automatic_search",
    "enable_interactive_search",
)


def _is_a_torrent_source(indexer: IndexerFacts) -> bool | None:
    """True when this indexer can put seeding torrents in the queue.

    ``None`` when that could not be decided, which covers every way the facts
    can fail to answer: an ``Unknown`` protocol, a protocol read back as null
    or as something that is not a string, and a toggle set where nothing is on
    and something could not be read. Guessing "not a torrent" for any of them
    drops the indexer from the predicate and reports PASS on the very failure
    this check exists to find — see the contract comment on ``IndexerFacts``.

    The toggles are a three-valued OR: one that is on settles the answer
    however unreadable the rest are.
    """
    protocol = indexer.protocol
    if not is_known(protocol) or not isinstance(protocol.value, str):
        return None
    if protocol.value != "torrent":
        return False
    return _any_of(tuple(_truth(getattr(indexer, name)) for name in TORRENT_TOGGLES))


def _seed_criteria(indexer: IndexerFacts) -> tuple[Fact[Any], ...]:
    """The criteria that can release a seeder's slot.

    ``season_pack_seed_time`` is collected but deliberately not among them. It
    bounds season-pack grabs only, so an indexer that sets it and nothing else
    still seeds every single-episode torrent forever — counting it as a goal
    would excuse exactly the indexer that can still wedge the queue.
    """
    return (indexer.seed_ratio, indexer.seed_time)


def _is_a_readable_seed_criterion(fact: Fact[Any]) -> bool:
    """True when this criterion was read as a number the arr could have meant.

    A seed ratio is a ratio and a seed time is a count of minutes, so anything
    that is not a number is a value we do not have — the same judgement
    ``_as_limit`` already makes on qBittorrent's side of this question, and the
    asymmetry between them was a false PASS on the flagship check (#10).

    Three shapes this rejects, each for its own reason:

    - ``Unknown`` and ``Known(None)``: never read, or read back as null. Both
      already meant "no goal" before this guard existed.
    - A **numeric string** such as ``"2.0"``. Coercing it would invent a goal
      the arr never reported, which is this project's cardinal sin wearing a
      plausible face.
    - A ``bool``. ``isinstance(True, int)`` is True in Python, so ``bool`` has
      to be excluded explicitly rather than left to fall out of the ``int``
      test, which admits it. (Either order works; omitting it does not.)

    This decides *type* and deliberately says nothing about *range*: ``-1`` is a
    perfectly readable number. ``_is_a_seed_goal`` is what adds the range rule,
    and keeping the two apart is load-bearing rather than tidy — a negative
    ratio must read as "no goal set" and must NOT collect
    ``_note_unreadable_seed_criteria``'s "not a number" note, which would send
    an operator to re-read a field whose value lintarr understood perfectly.
    """
    if not is_known(fact) or isinstance(fact.value, bool):
        return False
    return isinstance(fact.value, (int, float))


def _is_a_seed_goal(fact: Fact[Any]) -> bool:
    """True when this criterion is a number that can ever release the slot.

    Type first (``_is_a_readable_seed_criterion``), then range. The range rule
    is ``0 <= value <= MAX_REACHABLE_SEED_RATIO`` and neither end is a guess:
    see ``docs/measurements/2026-10-05-sonarr-seed-ratio-range.md`` for the
    lower end and
    ``docs/measurements/2026-10-12-qbittorrent-real-ratio-ceiling.md`` for the
    upper, both measured from Sonarr's and qBittorrent's own source rather than
    reasoned. The rule itself lives in ``_can_be_reached`` so that the global
    and category arms get the same one; this function only calls it.

    The short version, for ``seedCriteria.seedRatio`` and ``seedCriteria.seedTime``:

    - Sonarr neither clamps nor interprets the value. ``SeedConfigProvider``
      assigns ``Ratio = seedCriteria.SeedRatio`` verbatim, and its validator
      ``AsWarning()``s a non-positive number rather than refusing it — so a
      negative goal saves, and arrives at the download client intact.
    - qBittorrent's ``processTorrentShareLimits`` enforces a ratio only under
      ``(ratioLimit >= 0)``, and gates ``seedingTimeLimit`` on the identical
      test in the same function. **Every** negative is therefore "no limit",
      not just the ``-1`` sentinel — a ``-5`` seeds forever exactly as ``-1``
      does, which is the half the ticket had wrong.
    - ``0`` stays a goal, for BOTH criteria. ``ratio >= 0`` is satisfied the
      moment the torrent finishes, and ``finishedTime() / 60 >= 0`` is
      satisfied at that same instant, so the slot is released immediately.
      That is an aggressive goal, not an absent one.
    - **The range is bounded above as well as below**, at
      ``MAX_REACHABLE_SEED_RATIO``. A limit no torrent can reach is a limit
      that never releases a slot, which is the ``-1`` case wearing a large
      positive sign. ``1e308``, ``3.4e38``, a 401-digit ``int`` and ``inf``
      all fail it, and that is the whole of the magnitude rule.

      **This bound is measured and the number is not arbitrary.** lintarr#43
      refused to prescribe one until someone established the largest ratio
      ``realRatio()`` can return, on the grounds that a guessed threshold is
      this project's cardinal sin. The read is in
      ``docs/measurements/2026-10-12-qbittorrent-real-ratio-ceiling.md``:
      ``realRatio()`` has two branches, and for every torrent that can wedge a
      download queue — one holding data, so ``download >= 1`` — the reachable
      range is the finite branch's ``[0, 2**63]``. See
      ``MAX_REACHABLE_SEED_RATIO`` for the derivation.

      The other branch returns ``Torrent::MAX_RATIO``, which really is
      ``std::numeric_limits<qreal>::infinity()`` — so the earlier wording
      here, "a ratio limit of ``inf`` can never be reached by
      ``realRatio()``", was false and is gone. It needs
      ``total_done == 0 && all_time_download == 0`` with
      ``all_time_upload > 0``, i.e. a finished torrent holding no pieces at
      all that has nevertheless uploaded payload. Whether any sequence of
      operations reaches that is **not settled** by a source read and does not
      need to be: such a torrent holds nothing, so it is not the homelab#393
      wedge this check is about, and it is excluded either way.

    - ``nan`` and the ``inf`` case need nothing added beyond the bound. Every
      comparison against ``nan`` is False, so it fails both ends; ``inf`` fails
      the upper end. Both were previously handled by a separate
      ``math.isfinite`` guard, which the bound subsumes — and that retired the
      guard's own ``isinstance(..., float)`` workaround with it. (That
      workaround existed because ``math.isfinite`` raises ``OverflowError``
      rather than answering on an ``int`` too large for a ``float``, and
      ``json`` decodes a 401-digit literal to exactly such an ``int``. A
      Python ``int`` compares against a ``float`` exactly and never raises, so
      the comparison form has no such hole.)

    - **The bound is safe for ``seed_time``**, which goes through this same
      predicate. Sonarr's ``SeedTime`` is an ``int?`` of minutes, so
      ``2**31 - 1`` is the largest value that field can carry, and
      ``MAX_REACHABLE_SEED_RATIO`` is ``4294967298.0`` times larger than that
      — more than nine orders of magnitude. Every representable
      seed-time limit therefore stays a goal, which is what
      ``test_the_largest_representable_seed_time_is_still_a_seed_goal`` pins.
      That is a measured coincidence rather than a design, so it is asserted
      rather than assumed: the seeding-time arm has no ceiling of its own
      (``finishedTime() / 60`` grows with the wall clock), and if this bound
      ever has to drop near ``2**31`` the two criteria need splitting.

    So a negative criterion is an indexer whose torrents never stop seeding,
    and before this guard it disarmed the wedge check byte-identically to a
    real ratio of 2.0. Deliberately a weaker claim than this docstring first
    made: a negative goal is not *by itself* the homelab#393 wedge, because
    ``_seeding_conflict`` is a conjunction that still wants the global and
    category limits off — and a negative goal is precisely the case where
    those conjuncts can be false while the torrents still never stop. See
    lintarr#28.
    """
    if not _is_a_readable_seed_criterion(fact):
        return False
    return _can_be_reached(fact.value)


def _is_an_unusable_seed_criterion(fact: Fact[Any]) -> bool:
    """True when a criterion carried a value and that value is not a number.

    Narrower than ``not _is_a_seed_goal(...)``, in two different ways now:

    - An absent or null criterion is not unusable, it is *unset*, and Sonarr
      reports an unset goal exactly that way on every stack measured so far.
    - A **negative** criterion is not unusable either. It is a number, it is
      the number the operator typed, and its meaning is known and measured:
      no limit. It must arm the check without claiming nobody could read it,
      which is why this delegates to ``_is_a_readable_seed_criterion`` rather
      than to ``_is_a_seed_goal``. Written against the latter, every negative
      would collect the "not a number" note and send the operator to look at a
      field lintarr read correctly.

    Only the remaining class — present, non-null, and not a number — is a
    payload nobody can read.
    """
    return is_known(fact) and fact.value is not None and not _is_a_readable_seed_criterion(fact)


def _lacks_seed_criteria(indexer: IndexerFacts) -> bool:
    """No usable seed goal — unreadable, unset, or read as something unusable.

    Deliberately total rather than three-valued: Sonarr reports "unset" by
    omitting the value, so an Unknown here is the ordinary case, not a gap. See
    the module docstring for what that costs and why the alternative costs more.

    A criterion read back as ``null`` is not a goal either. Sonarr reports a
    cleared criterion that way, and reading "present but null" as a goal would
    clear the indexer whose goals an operator had explicitly removed.

    An unusable value lands in this same bucket rather than forcing a SKIP of
    its own. It cannot FAIL a stack by itself — it is one conjunct of seven, so
    everything else still has to hold — and of the two ways to be wrong about a
    payload nobody can read, leaving the wedge check armed is the recoverable
    one. ``_note_unreadable_seed_criteria`` is what keeps the resulting finding
    from claiming more than that.
    """
    return not any(_is_a_seed_goal(fact) for fact in _seed_criteria(indexer))


def _overrides_the_share_limit(fact: Fact[Any]) -> bool | None:
    """True when this criterion is a limit qBittorrent can neither reach nor redirect.

    Two conditions, and the second is the whole of lintarr#28. The value has to
    be unreachable — ``_is_a_seed_goal`` says it is not a goal — *and* it has to
    not be the one value that hands the question back to the global setting.

    Measured on ``release-5.2.4``
    (``docs/measurements/2026-10-05-sonarr-seed-ratio-range.md``)::

        qreal TorrentImpl::effectiveRatioLimit() const
        {
            if (m_ratioLimit == DEFAULT_RATIO_LIMIT)   // -2, and only -2
                return m_session->categoryRatioLimit(category());
            return m_ratioLimit;
        }

    ``categoryRatioLimit()`` is the only route to ``globalMaxRatio()``, and
    ``effectiveSeedingTimeLimit()`` redirects ``DEFAULT_SEEDING_TIME_LIMIT`` —
    also ``-2`` — in exactly the same shape. So for every negative *except*
    ``-2`` the redirect never happens, the global is never consulted, and
    ``qbt.no_global_ratio`` is a premise about a setting this torrent has put
    out of reach.

    Written as "not a goal" rather than ``value < 0`` deliberately, and it is
    not a tidy-up: ``inf`` is not a goal either (it exceeds
    ``MAX_REACHABLE_SEED_RATIO``; lintarr#43 corrected the reason this line
    used to give, which was that ``realRatio()`` never reaches
    it) and ``inf != -2``, so a positive infinity overrides exactly as ``-1``
    does. ``nan`` lands here for the same two reasons. A ``< 0`` spelling would
    read both as deferring and PASS a stack that cannot recover.

    **Three-valued, and the ``None`` is lintarr#33.** A criterion nobody can
    read is a *fourth* state — neither goal, deferring, nor overriding — and the
    two sides of this file want opposite defaults for it. ``_lacks_seed_criteria``
    reads it as "no goal", which *arms* ``SEEDING``; this predicate used to read
    it as "not an override", which *disarmed* all three override routes. So an
    indexer reporting ``-1`` as the string ``"-1"`` — the exact shape
    ``_is_a_readable_seed_criterion`` exists for — was a silent PASS on a stack
    a readable ``-1`` FAILs, and no note explained it, because the surviving
    finding was the starvation fall-through and ``_note_unreadable_seed_criteria``
    keys on ``SEED_CRITERIA_CONFLICTS``. Returning ``None`` SKIPs those routes
    instead, which also lets the note reach the finding.

    The ``None`` is scoped to ``_is_an_unusable_seed_criterion`` — present,
    non-null and not a number — and deliberately **not** to absent or null.
    Those two mean *defer*, exactly as ``-2`` does, and resolving them to
    unknown would turn every ordinary stack into a SKIP: Sonarr reports an unset
    goal by omitting it.
    """
    if _is_an_unusable_seed_criterion(fact):
        return None
    if not _is_a_readable_seed_criterion(fact):
        return False
    if fact.value == USE_GLOBAL:
        return False
    return not _can_be_reached(fact.value)


def _overrides_both_share_limits(indexer: IndexerFacts) -> bool | None:
    """Both criteria overridden, so no global or category setting is consulted at all.

    Quantified over ``_seed_criteria`` rather than naming the two fields, so a
    third criterion added there cannot leave this predicate claiming more than
    it checked. That protection stops here and does not reach its two
    single-axis siblings, which name ``seed_ratio`` and ``seed_time`` directly
    because the axis *is* what they are about — a third criterion would narrow
    this predicate correctly and leave those two firing without consulting it.
    ``all()`` over an empty ``_seed_criteria`` would also be vacuously True;
    unreachable today, since the tuple is a literal pair.
    """
    return _all_of(_overrides_the_share_limit(fact) for fact in _seed_criteria(indexer))


def _overrides_the_ratio_limit(indexer: IndexerFacts) -> bool | None:
    """Ratio overridden, and the seed time cannot release the slot on its own.

    "Sets no goal" is the weaker half on purpose. The seed time may be
    overridden too, or absent, or junk — in every one of those the *indexer*
    has not asked for a release, so whether the slot is ever freed comes down
    to the global seeding-time limit, which is the premise the conflict adds.
    A real ``seedTime`` goal beside an overridden ratio is not a wedge and must
    not match here.
    """
    return _all_of(
        (_overrides_the_share_limit(indexer.seed_ratio), not _is_a_seed_goal(indexer.seed_time))
    )


def _overrides_the_seed_time_limit(indexer: IndexerFacts) -> bool | None:
    """The mirror of ``_overrides_the_ratio_limit``, and not symmetrical by assumption.

    Its own predicate because the two criteria share a loop by convention and
    nothing else in this file trusts that convention — a ``> 0`` rule applied
    to ``seed_time`` alone survived the whole suite once already (#14). The
    redirect is measured on both fields: ``DEFAULT_SEEDING_TIME_LIMIT`` is
    ``-2`` and ``effectiveSeedingTimeLimit()`` treats it exactly as the ratio's.
    """
    return _all_of(
        (_overrides_the_share_limit(indexer.seed_time), not _is_a_seed_goal(indexer.seed_ratio))
    )


def _any_torrent_indexer(
    arrs: tuple[ArrInstance, ...], matches: Callable[[IndexerFacts], bool | None]
) -> bool | None:
    """Any ONE enabled torrent indexer satisfying *matches* is enough to wedge.

    Torrents grabbed from it never release their slot and accumulate in them.
    Requiring every indexer to match would miss the mixed case.

    An indexer that could not be classified only makes the answer unknown when
    it *also* matches — one that does not match could not have contributed
    either way, so it is not allowed to force a SKIP.

    No arr instances at all is unknown, never False. "We looked at every arr
    and found no such torrent indexer" and "there was no arr to look at" are
    different claims, and only the first of them can support a PASS. An arr
    that answered with an empty indexer list *is* the first claim: that read
    happened and it grabs nothing, so it stays False. Which of the ways there
    can be no arr this is — none configured, one declared but never collected —
    is decided in ``run.py``, which is the layer that knows what was declared.

    Lifted out of ``_indexer_without_seed_criteria`` by #28 rather than copied:
    every seeder-absorption route quantifies over indexers the same way, and
    four near-identical loops would be four places for the three-valued rule
    above to drift. The *shape* each route looks for is the parameter; the
    quantifier is not.

    *matches* is three-valued as of lintarr#33, and expressing this as
    ``_any_of`` over a per-indexer ``_all_of`` is what keeps the rule in the
    docstring above true of **both** inputs rather than only of the protocol
    read. An indexer contributes a wedge when it matches *and* grabs torrents,
    so either conjunct being False means it could not have contributed and must
    not force a SKIP — which is the old loop's ``if not matches(indexer):
    continue`` generalised, not weakened. The case that short-circuit got
    wrong is a **torrent** indexer whose shape is ``None``: ``not None`` is
    True, so it ``continue``d and the quantifier answered False where the
    honest answer is "undecided". A usenet indexer it already got right, and
    ``_all_of((None, False))`` keeps that right.
    """
    if not arrs:
        return None
    return _any_of(
        _all_of((matches(indexer), _is_a_torrent_source(indexer)))
        for arr in arrs
        for indexer in arr.indexers
    )


def _indexer_without_seed_criteria(arrs: tuple[ArrInstance, ...]) -> bool | None:
    """Any enabled torrent indexer that sets no usable seed goal at all."""
    return _any_torrent_indexer(arrs, _lacks_seed_criteria)


def _own_share_limit(category: dict[str, Any], key: str) -> bool | None:
    """True when *key* is this category's own limit rather than an inherited one.

    An absent key is **information, not a missing value**, and decides this as
    False. A qBittorrent that does not expose per-category share limits has no
    category that *can* override the global setting, so the answer is knowable
    and refusing to give it is wrong. Reading absence as unknown made the
    seeding conflict permanently SKIP for any client whose categories lack
    these keys — which includes homelab#393's own preferences, the incident
    this project exists to detect, and every stack using qBittorrent's default
    ``tv-sonarr``/``radarr`` categories. Do not "fix" this back to ``None``.

    This is not the defaulting the project refuses. That rule guards against
    inventing a value we might misread; here the key's absence positively tells
    us the feature is unavailable on this client.

    A value that is present but not a number — a string, a null, a bool — is a
    different thing entirely: the key IS there and we cannot read what it says,
    so that stays unknown.

    **A category that overrides its limit to unreachable still reads as
    "inherits" here, and that is lintarr#33's remaining slice.** Only ``-2``
    defers: ``categoryRatioLimit()`` returns any other value verbatim, so a
    ``ratio_limit`` of ``-1`` never reaches ``globalMaxRatio()`` and releases
    nothing, exactly as ``-5`` does. Both are filed as False below. ``-1``
    matters more than ``-5`` because it is qBittorrent's own named sentinel for
    "no limit" (``NO_RATIO_LIMIT``, ``sharelimits.h``) rather than an
    arbitrary negative, so it is the one a stack is likely to carry. Fixing it wants a premise
    this one cannot express, because an overriding category makes
    ``qbt.no_global_ratio`` a statement about a setting that never runs — the
    same shape as the indexer ``OVERRIDE_`` routes, and so its own conflict
    rather than a widened boolean. See
    ``docs/measurements/2026-10-05-sonarr-seed-ratio-range.md`` gap 5, which
    also records that this reads a flat map while
    ``categoryRatioLimit()`` recurses through ``parentCategoryName()``.
    """
    if key not in category:
        return False
    value = category[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # -2 inherits the global setting and -1 is unlimited; neither is a limit of
    # the category's own.
    if value in (USE_GLOBAL, UNLIMITED):
        return False
    # The shared range rule, not a third hand-written sign test (lintarr#33).
    # `>= 0` alone read `inf` as this category's own releasing limit, which
    # suppressed the SEEDING FAIL on a stack where nothing releases anything —
    # a false PASS, measured, with both globals off. `nan` landed on the right
    # answer here by accident before and does so by rule now.
    return _can_be_reached(value)


def _category_sets_its_own_limit(category: Any) -> bool | None:
    """True when this one category releases its torrents' slots by itself."""
    if not isinstance(category, dict):
        return None
    return _any_of(
        tuple(_own_share_limit(category, k) for k in ("ratio_limit", "seeding_time_limit"))
    )


def _no_category_sets_its_own_limit(qbt: QbtInstance) -> bool | None:
    """True when no category overrides the global share limits.

    Measured on 5.2.3: each category carries ``ratio_limit`` and
    ``seeding_time_limit`` where ``-2`` means inherit the global setting, ``-1``
    means unlimited, and ``>= 0`` is the category's own limit. A category with
    its own limit releases its torrents' slots even when the global limits are
    off, so it breaks the wedge for anything filed under it. A client that does
    not report those keys at all cannot express a per-category limit, so none
    of its categories override — see ``_own_share_limit``.

    Three-valued across categories, in that order: one category proving an
    override settles the premise as False however unreadable its neighbours
    are, and only then does an unreadable category make the answer unknown.
    An unreadable one is never silently skipped as "no override" — a category
    we could not parse is not evidence that it inherits.
    """
    if not is_known(qbt.categories):
        return None
    categories = qbt.categories.value
    # A null category map is a client with no categories, so none override.
    if categories is None:
        return True
    # An empty *list* is not an empty map: a shape this wrong means the read
    # did not give us categories at all, whether or not it happens to be empty.
    if not isinstance(categories, dict):
        return None
    overridden = _any_of(tuple(_category_sets_its_own_limit(c) for c in categories.values()))
    return None if overridden is None else not overridden


#: Which of this invariant's two conflicts a finding came from. They are
#: structurally different failures with different remedies, so anything that
#: explains a finding downstream — the CLI's "Therefore" line above all — must
#: key on this and not on the invariant id alone.
STARVATION = "no-slot-for-a-first-download"
SEEDING = "seeders-absorb-every-slot"

#: Three more routes to "seeders absorb every slot", split out from ``SEEDING``
#: rather than folded into it because each has a different remedy and ``SEEDING``
#: cannot state any of them. ``SEEDING`` says "both global share limits are
#: off"; these are the cases where one or both are ON and the indexer has put
#: them out of reach, so pointing an operator at a global setting is pointing
#: them at a setting that will not run. lintarr#28.
OVERRIDE_BOTH = "indexer-overrides-both-share-limits"
OVERRIDE_RATIO = "indexer-overrides-the-ratio-limit"
OVERRIDE_SEED_TIME = "indexer-overrides-the-seed-time-limit"

#: The release *action*, as opposed to the release *limits* above. Reaching a
#: limit only frees a slot if the configured action stops or removes the
#: torrent; `EnableSuperSeeding` (and `Default`, which matches no branch at
#: all) leave it running. That makes every limit on the stack decorative, so
#: this route names no limit premise — it is the one case where a *better*
#: configured stack wedges. lintarr#32.
ACTION = "share-limit-action-frees-no-slot"

#: Every conflict this invariant can report, in no particular order. Published
#: so the CLI's explanation table can be checked against it instead of against
#: a second hand-written list — a conflict added without a "Therefore" line
#: prints a bare FAIL, and a hand-copied set cannot see that happen.
CONFLICTS: tuple[str, ...] = (
    STARVATION,
    SEEDING,
    OVERRIDE_BOTH,
    OVERRIDE_RATIO,
    OVERRIDE_SEED_TIME,
    ACTION,
)

#: The conflicts that rest on an indexer's seed criteria, and so the ones whose
#: findings may need ``_note_unreadable_seed_criteria``'s sentence. ``STARVATION``
#: is deliberately absent: it never reads a seed criterion, and its "Therefore"
#: line ends "Share limits are not involved".
SEED_CRITERIA_CONFLICTS: frozenset[str] = frozenset(
    {SEEDING, OVERRIDE_BOTH, OVERRIDE_RATIO, OVERRIDE_SEED_TIME}
)


def _starvation_conflict(qbt: QbtInstance, queueing: Premise) -> Finding:
    """A client that cannot start even a first download, seeders or not."""
    return conflict_if(
        INVARIANT_ID,
        f"qbittorrent[{qbt.name}]",
        queueing,
        premise("qbt.no_slot_for_a_first_download", _no_slot_for_a_first_download(qbt)),
        conflict=STARVATION,
    )


def _slot_premises(qbt: QbtInstance, queueing: Premise) -> tuple[Premise, ...]:
    """What every route past starvation needs before share limits matter at all.

    The same physical claim each time: the queue is managed,
    ``max_active_torrents`` can run out, and slow torrents are not exempt from
    it. What the routes disagree about is only whether anything ever releases a
    slot.

    Deliberately *excludes* every release-gate premise, including the inactive
    one. ``ACTION`` composes on this tuple and must not inherit
    ``qbt.no_global_inactive_seed_time``: when the share-limit action frees no
    slot, the inactive arm firing releases nothing either, so requiring the gate
    to be off would make ``ACTION`` miss the stacks where it is on. The four
    seeder-absorption routes take ``_deferring_slot_premises`` instead.
    """
    return (
        queueing,
        premise("qbt.max_active_torrents_binds", _max_active_torrents_binds(qbt)),
        premise("qbt.slow_exempt_off", _not(qbt.dont_count_slow_torrents)),
    )


def _deferring_slot_premises(qbt: QbtInstance, queueing: Premise) -> tuple[Premise, ...]:
    """``_slot_premises`` plus the one release gate no indexer can override.

    ``qbt.no_global_inactive_seed_time`` is shared by all four seeder-absorption
    routes rather than living in any one of them, because
    ``effectiveInactiveSeedingTimeLimit()`` redirects ``-2`` to
    ``globalMaxInactiveSeedingMinutes()`` and Sonarr never sets a per-torrent
    inactive limit — so that arm is always deferring to the global no matter
    which *other* axis the indexer put out of reach. A route that omitted it
    would FAIL a stack the global drains (lintarr#32).

    Reachability is why it belongs with the slot premises and not beside
    ``qbt.no_global_ratio``: ``processTorrentShareLimits`` is an ``else if``
    chain whose arms each test ``(limit >= 0) && (count >= limit)``, so the
    third arm is reachable with the other two globals *on* and not yet reached.

    **Two things the global flag does not settle, both of which can still wedge
    a stack this premise reports as drainable:**

    - A **category** may set its own ``inactive_seeding_time_limit``, which
      ``categoryInactiveSeedingTimeLimit()`` honours ahead of the global. This
      file does not read it — ``_category_sets_its_own_limit`` iterates
      ``ratio_limit`` and ``seeding_time_limit`` only — so a category
      overriding the gate to never-release is invisible here. The data is
      already in ``qbt.categories``; closing it is the remaining slice of
      lintarr#32.
    - The gate releases a torrent that has been **inactive** for the limit
      (``timeSinceActivity()``), so actively-uploading seeders never reach it.
      homelab#393 sat at 0 kB/s, so the recorded incident is on the side this
      premise gets right, and lintarr reads no torrent list so it could not do
      better without a new collection surface.

    Both are the missing-a-FAIL direction, which is the error this project
    prefers to a FAIL on a stack that recovers (see
    ``_override_ratio_conflict``). What the flag *does* settle exactly is
    whether the global gate is armed: ``appcontroller.cpp`` derives
    ``max_inactive_seeding_time_enabled`` as
    ``globalMaxInactiveSeedingMinutes() >= 0``, which is the arm's own firing
    condition, so the paired minutes value changes *when* a torrent is released
    and never *whether* one can be.
    """
    return _slot_premises(qbt, queueing) + (
        premise("qbt.no_global_inactive_seed_time", _not_inactive_gate(qbt)),
    )


def _seeding_conflict(
    qbt: QbtInstance, arrs: tuple[ArrInstance, ...], queueing: Premise
) -> Finding:
    """homelab#393: seeders absorb every slot and nothing ever releases one.

    The *deferring* route, and the flagship. Every criterion hands its limit
    back to the global/category chain — by being absent, null, unreadable or
    ``-2`` — and that chain releases nothing either. The three
    ``OVERRIDE_``… conflicts below are the cases this one cannot express,
    because a criterion that overrides its limit makes these global premises
    statements about a setting that never runs.
    """
    premises = _deferring_slot_premises(qbt, queueing) + (
        premise("qbt.no_global_ratio", _no_global_ratio(qbt)),
        premise("qbt.no_global_seed_time", _not(qbt.max_seeding_time_enabled)),
        premise("qbt.no_category_limits", _no_category_sets_its_own_limit(qbt)),
        premise("arr.indexer_without_seed_criteria", _indexer_without_seed_criteria(arrs)),
    )
    return conflict_if(INVARIANT_ID, f"qbittorrent[{qbt.name}]", *premises, conflict=SEEDING)


def _override_both_conflict(
    qbt: QbtInstance, arrs: tuple[ArrInstance, ...], queueing: Premise
) -> Finding:
    """Both limits overridden to unreachable, so no global setting is consulted.

    The only route here that names no share-limit premise at all. For the two
    limits this file models that is the finding rather than an omission: the
    torrent carries its own unreachable limit on both axes, the ``-2`` redirect
    never fires, and listing ``qbt.no_global_ratio`` would be a false premise on
    a stack whose global ratio limit is on.

    It *was* the route with the widest false-FAIL exposure to the third release
    gate, because it names no share-limit premise and so fired whatever the
    globals said. lintarr#32 closed that: ``_slot_premises`` now carries
    ``qbt.no_global_inactive_seed_time``, so this route — like the other three —
    reaches its FAIL only on a stack where that gate is off too. The share-limit
    premises it still declines to name are the two an indexer *can* override;
    the inactive gate is the one it cannot, which is why that premise is shared
    rather than per-route.
    """
    premises = _deferring_slot_premises(qbt, queueing) + (
        premise(
            "arr.indexer_overrides_both_share_limits",
            _any_torrent_indexer(arrs, _overrides_both_share_limits),
        ),
    )
    return conflict_if(INVARIANT_ID, f"qbittorrent[{qbt.name}]", *premises, conflict=OVERRIDE_BOTH)


def _override_ratio_conflict(
    qbt: QbtInstance, arrs: tuple[ArrInstance, ...], queueing: Premise
) -> Finding:
    """The ratio is overridden; the seeding-time limit is the only route left, and it is off.

    Deliberately asks for ``qbt.no_global_seed_time`` and **not**
    ``qbt.no_global_ratio``. The global ratio limit is irrelevant here by
    measurement, not by taste: the torrent's own ``ratioLimit`` is not ``-2``,
    so ``effectiveRatioLimit()`` never calls ``categoryRatioLimit()`` and
    ``globalMaxRatio()`` is unreachable.

    ``qbt.no_category_limits`` is the existing combined premise, which also
    reads each category's ``ratio_limit``. That is stricter than this route
    needs — a category with a ratio limit but no seeding-time limit suppresses
    this FAIL — so the conflict can miss, and can never fire on a stack that
    recovers. Of the two directions to be imprecise in, that is the one this
    project chooses; splitting the premise per criterion is the remaining slice.
    """
    premises = _deferring_slot_premises(qbt, queueing) + (
        premise("qbt.no_global_seed_time", _not(qbt.max_seeding_time_enabled)),
        premise("qbt.no_category_limits", _no_category_sets_its_own_limit(qbt)),
        premise(
            "arr.indexer_overrides_the_ratio_limit",
            _any_torrent_indexer(arrs, _overrides_the_ratio_limit),
        ),
    )
    return conflict_if(INVARIANT_ID, f"qbittorrent[{qbt.name}]", *premises, conflict=OVERRIDE_RATIO)


def _override_seed_time_conflict(
    qbt: QbtInstance, arrs: tuple[ArrInstance, ...], queueing: Premise
) -> Finding:
    """The mirror: seeding time overridden, so the global ratio limit is all there is."""
    premises = _deferring_slot_premises(qbt, queueing) + (
        premise("qbt.no_global_ratio", _no_global_ratio(qbt)),
        premise("qbt.no_category_limits", _no_category_sets_its_own_limit(qbt)),
        premise(
            "arr.indexer_overrides_the_seed_time_limit",
            _any_torrent_indexer(arrs, _overrides_the_seed_time_limit),
        ),
    )
    return conflict_if(
        INVARIANT_ID, f"qbittorrent[{qbt.name}]", *premises, conflict=OVERRIDE_SEED_TIME
    )


def _action_conflict(qbt: QbtInstance, queueing: Premise) -> Finding:
    """Every limit on the stack is decorative, because reaching one frees nothing.

    The inverse of the four routes above, and the one route here that can FAIL a
    stack whose share limits are all correctly configured. Those four say *no
    limit will ever be reached*; this one says a limit **is** reached and the
    torrent keeps its slot anyway — ``processTorrentShareLimits`` sets super
    seeding, or matches no action branch at all, and returns.

    Names no limit premise deliberately, and that is the finding rather than an
    omission: ``effectiveShareLimitAction()`` is consulted inside ``if
    (reached)``, *after* whichever arm fired, so the action defeats all three
    arms at once. Listing ``qbt.no_global_ratio`` would be a false premise on a
    stack whose global ratio limit is on and reached — which is exactly this
    route's case.

    It takes no ``arrs`` and reads no seed criteria for the same reason, which is
    why ``ACTION`` is absent from ``SEED_CRITERIA_CONFLICTS``: an indexer's goals
    cannot rescue a stack where reaching a goal is what fails.

    Ordered **last** in ``check``, so no stack that already FAILed changes which
    conflict it reports; the only new verdicts are on stacks every other route
    left at PASS.
    """
    return conflict_if(
        INVARIANT_ID,
        f"qbittorrent[{qbt.name}]",
        *_slot_premises(qbt, queueing),
        premise("qbt.share_limit_action_frees_no_slot", _action_frees_no_slot(qbt)),
        conflict=ACTION,
    )


def _note_arrs_that_reported_no_indexers(
    finding: Finding, arrs: tuple[ArrInstance, ...]
) -> Finding:
    """Name any arr that answered with an empty indexer list.

    A bare PASS reads as "we examined this stack's indexers and none of them
    can wedge the queue". An arr that reported no indexers at all clears the
    seeding conflict without a single indexer having been examined — a
    legitimate read, but an operator who expected indexers there has a
    collection problem the verdict alone will never show them.
    """
    empty = tuple(f"{arr.kind}[{arr.name}]" for arr in arrs if not arr.indexers)
    if not empty:
        return finding
    return replace(
        finding,
        detail=f"{', '.join(empty)} answered with no indexers, so nothing there "
        "could leave a torrent seeding",
    )


def _fed_the_premise_an_unreadable_criterion(indexer: IndexerFacts) -> bool:
    """True when this indexer's unreadable criterion is what the premise read.

    Two filters beyond "a criterion is not a number", because each of them was a
    misleading sentence on a real verdict before it existed:

    - ``_lacks_seed_criteria`` — an indexer whose *other* criterion is a real
      goal never reached the premise, so a junk ``seedRatio`` sitting beside a
      ``seedTime`` of 2880 is not something any verdict rests on.
    - ``_is_a_torrent_source(...) is not False`` — a usenet indexer, or one with
      every toggle off, is excluded from the predicate by design. ``None`` stays
      in deliberately: an indexer nobody could classify is exactly the one that
      forces the SKIP this note has to explain.
    """
    if not any(_is_an_unusable_seed_criterion(fact) for fact in _seed_criteria(indexer)):
        return False
    return _lacks_seed_criteria(indexer) and _is_a_torrent_source(indexer) is not False


def _indexers_with_unreadable_seed_criteria(arrs: tuple[ArrInstance, ...]) -> tuple[str, ...]:
    """Name every indexer whose unreadable criterion fed the seeding premise."""
    return tuple(
        f"{arr.kind}[{arr.name}]/{indexer.name}"
        for arr in arrs
        for indexer in arr.indexers
        if _fed_the_premise_an_unreadable_criterion(indexer)
    )


def _note_unreadable_seed_criteria(finding: Finding, arrs: tuple[ArrInstance, ...]) -> Finding:
    """Say which indexer's seed criterion could not be read, if any could not.

    Arming the check is only half of the fix. "This indexer sets no seed goal"
    is a true sentence about an absent value and a false one about an unreadable
    one, and an operator who can see a ratio in Sonarr's UI would read the
    finding as lintarr being wrong and go re-set a goal that is already set. The
    premise is a bool and cannot carry the difference, so the detail does.

    Appended rather than assigned: a SKIP arrives already explaining which
    inputs it could not read, and that sentence is about the verdict while this
    one is about a value the verdict did not use.

    Only on the seeding finding, and that guard is load-bearing. The starvation
    conflict does not consult a seed criterion at all, and its "Therefore" line
    ends "Share limits are not involved, so turning them on will not help" — a
    sentence about seed criteria printed directly above that is the exact
    coupling defect ``Finding.conflict`` and the ``_THEREFORE`` keying in
    ``cli.py`` were written to prevent. A PASS reaches an operator through
    ``_worst_of``'s starvation fall-through, so this excludes the clean runs too.
    """
    if finding.conflict not in SEED_CRITERIA_CONFLICTS:
        return finding
    unreadable = _indexers_with_unreadable_seed_criteria(arrs)
    if not unreadable:
        return finding
    note = (
        f"{', '.join(unreadable)} reported a seed criterion that is not a number, "
        "so no goal could be read from it"
    )
    return replace(finding, detail=f"{finding.detail}; {note}" if finding.detail else note)


def _worst_of(findings: tuple[Finding, ...], arrs: tuple[ArrInstance, ...]) -> Finding:
    """Whichever conflict decides the invariant, annotated.

    Outcome-major: the worst outcome any route reached wins, and among routes
    that reached it, the earliest in *findings* wins. So the caller's order is
    the tie-break and it is load-bearing twice over.

    Starvation leads because a client that cannot start a first download is the
    more fundamental fact and the more actionable one — turning the share
    limits back on would not help it.

    The deferring seeding route comes before the three override routes, which
    makes this change a no-op for every verdict that already existed: a stack
    ``SEEDING`` already FAILs on keeps reporting ``SEEDING``, and the only new
    verdicts are on stacks it left at PASS.

    Two tempting stronger claims are **false**, and a code review measured both.
    Recorded because each reads as *the* reason for the ordering and neither is:

    - *"Any stack the override routes FAIL on is one ``SEEDING`` leaves at
      PASS."* No. The #393 preferences plus one indexer at ``seedRatio=-1``
      FAIL ``SEEDING`` **and** ``OVERRIDE_RATIO`` at once. Nothing currently
      failing changes only because ``SEEDING`` is earlier in the tuple — the
      tie-break is load-bearing, not incidental. It also costs a half-wrong
      remedy on that overlap, which is pre-existing and its own ticket.
    - *"Whenever an override route SKIPs, ``SEEDING`` has SKIPped too."* No.
      Two indexers are enough: a classifiable one with both criteria absent
      settles ``arr.indexer_without_seed_criteria`` to True, so with a global
      ratio limit on ``SEEDING`` PASSes, while an unclassifiable second indexer
      at ``seedRatio=seedTime=-1`` leaves ``OVERRIDE_BOTH`` unknown. The
      surfaced SKIP then carries ``arr.indexer_overrides_both_share_limits``
      alone.

    ``run.py::_resolve_missing_arrs`` survives that second case for a different
    reason than the label: it also requires ``facts.arrs`` to be empty, and with
    no arrs *every* route's existential returns ``None``, so ``SEEDING`` always
    SKIPs and — being first — is always the one that surfaces. The relabel is
    fenced by the no-arrs precondition, not by this ordering. Reordering these
    routes still breaks it, because the first SKIP would then carry an override
    label; ``tests/test_run.py`` holds that line.

    When nothing fires the result is a PASS, which is the one outcome that
    needs to say whether there was anything to examine — hence the
    ``_note_arrs_that_reported_no_indexers`` call on that branch alone. It is
    applied to the first finding because every route reports the same instance
    and a PASS carries no premises to choose between.
    """
    for outcome in (Outcome.FAIL, Outcome.SKIP):
        for finding in findings:
            if finding.outcome is outcome:
                return finding
    return _note_arrs_that_reported_no_indexers(findings[0], arrs)


def check(qbt: QbtInstance, arrs: tuple[ArrInstance, ...]) -> Finding:
    """FAIL when this configuration can reach a state with no startable download.

    The routes are combined as a three-valued disjunction. All of them are
    always evaluated: none is a precondition of another, and stopping at the
    first non-PASS would let an unreadable preference in one hide a wedge
    another had already proved.

    Four seeder-absorption routes rather than one, because "nothing ever
    releases a seeder" is not one claim. A criterion either defers its limit to
    the global/category chain or overrides it, the two cannot be rescued by the
    same setting, and a torrent is released as soon as *either* of its limits
    is reached — so the cases cross-product. See ``SEEDING`` and the
    ``OVERRIDE_``… conflicts for which is which, and lintarr#28 for the
    three shapes the single conjunction left at PASS.
    """
    queueing = premise("qbt.queueing_enabled", qbt.queueing_enabled)
    routes = (
        _starvation_conflict(qbt, queueing),
        _seeding_conflict(qbt, arrs, queueing),
        _override_both_conflict(qbt, arrs, queueing),
        _override_ratio_conflict(qbt, arrs, queueing),
        _override_seed_time_conflict(qbt, arrs, queueing),
        _action_conflict(qbt, queueing),
    )
    return _note_unreadable_seed_criteria(_worst_of(routes, arrs), arrs)
