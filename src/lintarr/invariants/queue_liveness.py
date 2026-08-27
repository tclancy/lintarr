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
   #393, and it is a conjunction.

They are reported as one invariant because they answer the same operator
question. A conjunction cannot express "or", so each is decided separately and
the two are combined as a three-valued disjunction: FAIL if either proves the
wedge, SKIP only if neither proves it and something could not be read, PASS
otherwise. Anything cruder loses verdicts it had already proved — a single
unreadable preference must not silence a wedge established without it.

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

from collections.abc import Iterable
from typing import Any

from lintarr.facts import Fact, is_known
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
    "qbt.max_seeding_time_enabled",
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


def _lacks_seed_criteria(indexer: IndexerFacts) -> bool:
    """No usable seed goal — either unreadable, or read and unset.

    Deliberately total rather than three-valued: Sonarr reports "unset" by
    omitting the value, so an Unknown here is the ordinary case, not a gap. See
    the module docstring for what that costs and why the alternative costs more.

    A criterion read back as ``null`` is not a goal either. Sonarr reports a
    cleared criterion that way, and reading "present but null" as a goal would
    clear the indexer whose goals an operator had explicitly removed.

    ``season_pack_seed_time`` is collected but deliberately not consulted here.
    It bounds season-pack grabs only, so an indexer that sets it and nothing
    else still seeds every single-episode torrent forever — counting it as a
    goal would excuse exactly the indexer that can still wedge the queue.
    """
    for fact in (indexer.seed_ratio, indexer.seed_time):
        if is_known(fact) and fact.value is not None:
            return False
    return True


def _indexer_without_seed_criteria(arrs: tuple[ArrInstance, ...]) -> bool | None:
    """Any ONE enabled torrent indexer lacking goals is enough to wedge.

    Torrents grabbed from it seed forever and accumulate in the slots.
    Requiring every indexer to lack them would miss the mixed case.

    An indexer that could not be classified only makes the answer unknown when
    it also lacks goals — with goals set it could not have contributed either
    way, so it is not allowed to force a SKIP.

    No arr instances at all is unknown, never False. "We looked at every arr
    and found no goal-less torrent indexer" and "there was no arr to look at"
    are different claims, and only the first of them can support a PASS. An arr
    that answered with an empty indexer list *is* the first claim: that read
    happened and it grabs nothing, so it stays False. Which of the ways there
    can be no arr this is — none configured, one declared but never collected —
    is decided in ``run.py``, which is the layer that knows what was declared.
    """
    if not arrs:
        return None
    undecidable = False
    for arr in arrs:
        for indexer in arr.indexers:
            if not _lacks_seed_criteria(indexer):
                continue
            match _is_a_torrent_source(indexer):
                case True:
                    return True
                case None:
                    undecidable = True
    return None if undecidable else False


def _own_share_limit(category: dict[str, Any], key: str) -> bool | None:
    """True when *key* is this category's own limit rather than an inherited one.

    An absent key is unknown, not an inherited limit. Measured on 5.2.3 both
    keys are always present, so a category missing one is a shape we have never
    seen and cannot reason about; defaulting it to ``USE_GLOBAL`` would decide
    the premise from a value the client never sent. A value that is not a
    number — a string, a null, a bool — is unknown for the same reason.
    """
    if key not in category:
        return None
    value = category[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # -2 inherits the global setting and -1 is unlimited; neither is a limit of
    # the category's own, and anything from 0 up is.
    if value in (USE_GLOBAL, UNLIMITED):
        return False
    return value >= 0


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
    off, so it breaks the wedge for anything filed under it.

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


def _starvation_conflict(qbt: QbtInstance, queueing: Premise) -> Finding:
    """A client that cannot start even a first download, seeders or not."""
    return conflict_if(
        INVARIANT_ID,
        f"qbittorrent[{qbt.name}]",
        queueing,
        premise("qbt.no_slot_for_a_first_download", _no_slot_for_a_first_download(qbt)),
        conflict=STARVATION,
    )


def _seeding_conflict(
    qbt: QbtInstance, arrs: tuple[ArrInstance, ...], queueing: Premise
) -> Finding:
    """homelab#393: seeders absorb every slot and nothing ever releases one."""
    premises: tuple[Premise, ...] = (
        queueing,
        premise("qbt.max_active_torrents_binds", _max_active_torrents_binds(qbt)),
        premise("qbt.slow_exempt_off", _not(qbt.dont_count_slow_torrents)),
        premise("qbt.no_global_ratio", _not(qbt.max_ratio_enabled)),
        premise("qbt.no_global_seed_time", _not(qbt.max_seeding_time_enabled)),
        premise("qbt.no_category_limits", _no_category_sets_its_own_limit(qbt)),
        premise("arr.indexer_without_seed_criteria", _indexer_without_seed_criteria(arrs)),
    )
    return conflict_if(INVARIANT_ID, f"qbittorrent[{qbt.name}]", *premises, conflict=SEEDING)


def check(qbt: QbtInstance, arrs: tuple[ArrInstance, ...]) -> Finding:
    """FAIL when this configuration can reach a state with no startable download.

    The two conflicts are combined as a three-valued disjunction. Both are
    always evaluated: neither is a precondition of the other, and stopping at
    the first non-PASS would let an unreadable preference in one hide a wedge
    the other had already proved.
    """
    queueing = premise("qbt.queueing_enabled", qbt.queueing_enabled)
    starved = _starvation_conflict(qbt, queueing)
    seeding = _seeding_conflict(qbt, arrs, queueing)

    # Starvation is reported ahead of seeding when both fire: a client that
    # cannot start a first download is the more fundamental fact and the more
    # actionable one, since turning the share limits back on would not help it.
    for outcome in (Outcome.FAIL, Outcome.SKIP):
        for finding in (starved, seeding):
            if finding.outcome is outcome:
                return finding
    return starved
