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
question, but a conjunction cannot express "or", so (1) is decided first.
"""

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


def _not(fact: Fact[bool]) -> bool | None:
    """Negate a boolean fact, preserving unknown-ness."""
    if not is_known(fact):
        return None
    return not bool(fact.value)


def _as_limit(fact: Fact[int]) -> int | None:
    """The integer a limit was read as, or ``None`` if it was not read as one.

    A limit that came back as a string, a null or a bool is a fact we do not
    have. Coercing it would invent a number the client never reported.
    """
    if not is_known(fact) or isinstance(fact.value, bool) or not isinstance(fact.value, int):
        return None
    return fact.value


def _no_slot_for_a_first_download(qbt: QbtInstance) -> bool | None:
    """True when an *idle* client still cannot start anything.

    With zero torrents running, a limit blocks the first start when it binds and
    is at or below zero. ``max_active_uploads`` is not consulted: it gates
    seeding slots, and a seeder that cannot get one does not hold a download
    back.
    """
    total = _as_limit(qbt.max_active_torrents)
    downloads = _as_limit(qbt.max_active_downloads)
    if total is None or downloads is None:
        return None
    return (_binds(total) and total <= 0) or (_binds(downloads) and downloads <= 0)


def _a_limit_binds(qbt: QbtInstance) -> bool | None:
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


def _is_a_torrent_source(indexer: IndexerFacts) -> bool | None:
    """True when this indexer can put seeding torrents in the queue.

    ``None`` when that could not be decided. Guessing "not a torrent" for an
    indexer whose protocol did not parse would drop it from the predicate and
    report PASS on the very failure this check exists to find.
    """
    if not is_known(indexer.protocol):
        return None
    if indexer.protocol.value != "torrent":
        return False
    toggles = (indexer.enable_rss, indexer.enable_automatic_search)
    if any(is_known(t) and bool(t.value) for t in toggles):
        return True
    return False if all(is_known(t) for t in toggles) else None


def _lacks_seed_criteria(indexer: IndexerFacts) -> bool:
    """No usable seed goal — either unreadable, or read and unset."""
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
    """
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


def _no_category_sets_its_own_limit(qbt: QbtInstance) -> bool | None:
    """True when no category overrides the global share limits.

    Measured on 5.2.3: each category carries ``ratio_limit`` and
    ``seeding_time_limit`` where ``-2`` means inherit the global setting, ``-1``
    means unlimited, and ``>= 0`` is the category's own limit. A category with
    its own limit releases its torrents' slots even when the global limits are
    off, so it breaks the wedge for anything filed under it.
    """
    if not is_known(qbt.categories):
        return None
    # A null category map is a client with no categories, so none override.
    categories = qbt.categories.value or {}
    if not isinstance(categories, dict):
        return None
    for category in categories.values():
        if not isinstance(category, dict):
            continue
        for key in ("ratio_limit", "seeding_time_limit"):
            value = category.get(key, USE_GLOBAL)
            if isinstance(value, (int, float)) and value >= 0:
                return False
    return True


def check(qbt: QbtInstance, arrs: tuple[ArrInstance, ...]) -> Finding:
    """FAIL when this configuration can reach a state with no startable download."""
    instance = f"qbittorrent[{qbt.name}]"
    queueing = premise("qbt.queueing_enabled", qbt.queueing_enabled)

    starved = conflict_if(
        INVARIANT_ID,
        instance,
        queueing,
        premise("qbt.no_slot_for_a_first_download", _no_slot_for_a_first_download(qbt)),
    )
    if starved.outcome is not Outcome.PASS:
        # FAIL: wedged with no seeder involved. SKIP: a limit we could not read
        # may already be zero, and "unlimited elsewhere" is not evidence.
        return starved

    seeding: tuple[Premise, ...] = (
        queueing,
        premise("qbt.a_limit_binds", _a_limit_binds(qbt)),
        premise("qbt.slow_exempt_off", _not(qbt.dont_count_slow_torrents)),
        premise("qbt.no_global_ratio", _not(qbt.max_ratio_enabled)),
        premise("qbt.no_global_seed_time", _not(qbt.max_seeding_time_enabled)),
        premise("qbt.no_category_limits", _no_category_sets_its_own_limit(qbt)),
        premise("arr.indexer_without_seed_criteria", _indexer_without_seed_criteria(arrs)),
    )
    return conflict_if(INVARIANT_ID, instance, *seeding)
