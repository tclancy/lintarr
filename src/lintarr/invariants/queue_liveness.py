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

- a **goal** (``>= 0`` and finite) — the slot is released, no wedge;
- **deferring** (``-2``, absent, or null) — ``effectiveRatioLimit()`` redirects
  it to ``categoryRatioLimit(category())``, which is the only route to
  ``globalMaxRatio()``, so the global or category setting decides;
- **overriding** (any other negative, and ``inf``/``nan``) — returned verbatim
  into a ``>= 0`` gate it can never pass. The redirect never happens, so the
  global is never consulted and **cannot** release the slot.

A criterion nobody can read is a **fourth** state, not one of these, and the
distinction matters because the two sides want opposite defaults for it.
``_lacks_seed_criteria`` reads it as "no goal", which *arms* ``SEEDING``;
``_overrides_the_share_limit`` reads it as "not an override", which *disarms*
the three routes below. So an indexer reporting ``-1`` as the string ``"-1"`` —
the shape ``_is_a_readable_seed_criterion`` exists for — is a silent PASS on a
stack a readable ``-1`` FAILs, and no note explains it. Pre-existing rather
than introduced here, measured during #28's review, and its own ticket.

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

import math
from collections.abc import Callable, Iterable
from dataclasses import replace
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
    is ``>= 0`` and it is not a guess: see
    ``docs/measurements/2026-10-05-sonarr-seed-ratio-range.md``, measured from
    Sonarr's and qBittorrent's own source rather than reasoned.

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
    - ``inf`` is **not** a goal, and it is the one case the range test alone
      gets wrong: ``inf >= 0`` is True, while a ratio limit of ``inf`` can
      never be reached by ``realRatio()``. It is ``-1`` wearing a positive
      sign, so it needs ``math.isfinite`` rather than the comparison.
      Reachable rather than theoretical — ``json`` decodes both ``1e400``
      and the non-standard ``Infinity`` token to ``inf``.

    ``nan`` needs nothing added: every comparison against it is False, so it
    already fails the range test and arms the check. Recorded here so that
    stays a known property rather than a lucky one.

    The ``isinstance(..., float)`` is load-bearing and not a tidy-up. Only a
    ``float`` can be non-finite — a Python ``int`` always is — and
    ``math.isfinite`` on a large enough ``int`` raises ``OverflowError``
    instead of answering. ``json`` decodes a 401-digit integer literal to
    exactly such an ``int``, so a bare ``math.isfinite(fact.value)`` here
    turns a junk payload into a crash, which is the one outcome this file
    ranks below a false PASS.

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
    if isinstance(fact.value, float) and not math.isfinite(fact.value):
        return False
    return fact.value >= 0


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


def _overrides_the_share_limit(fact: Fact[Any]) -> bool:
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
    not a tidy-up: ``inf`` is not a goal either (``realRatio()`` never reaches
    it) and ``inf != -2``, so a positive infinity overrides exactly as ``-1``
    does. ``nan`` lands here for the same two reasons. A ``< 0`` spelling would
    read both as deferring and PASS a stack that cannot recover.
    """
    if not _is_a_readable_seed_criterion(fact):
        return False
    if fact.value == USE_GLOBAL:
        return False
    return not _is_a_seed_goal(fact)


def _overrides_both_share_limits(indexer: IndexerFacts) -> bool:
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
    return all(_overrides_the_share_limit(fact) for fact in _seed_criteria(indexer))


def _overrides_the_ratio_limit(indexer: IndexerFacts) -> bool:
    """Ratio overridden, and the seed time cannot release the slot on its own.

    "Sets no goal" is the weaker half on purpose. The seed time may be
    overridden too, or absent, or junk — in every one of those the *indexer*
    has not asked for a release, so whether the slot is ever freed comes down
    to the global seeding-time limit, which is the premise the conflict adds.
    A real ``seedTime`` goal beside an overridden ratio is not a wedge and must
    not match here.
    """
    return _overrides_the_share_limit(indexer.seed_ratio) and not _is_a_seed_goal(indexer.seed_time)


def _overrides_the_seed_time_limit(indexer: IndexerFacts) -> bool:
    """The mirror of ``_overrides_the_ratio_limit``, and not symmetrical by assumption.

    Its own predicate because the two criteria share a loop by convention and
    nothing else in this file trusts that convention — a ``> 0`` rule applied
    to ``seed_time`` alone survived the whole suite once already (#14). The
    redirect is measured on both fields: ``DEFAULT_SEEDING_TIME_LIMIT`` is
    ``-2`` and ``effectiveSeedingTimeLimit()`` treats it exactly as the ratio's.
    """
    return _overrides_the_share_limit(indexer.seed_time) and not _is_a_seed_goal(indexer.seed_ratio)


def _any_torrent_indexer(
    arrs: tuple[ArrInstance, ...], matches: Callable[[IndexerFacts], bool]
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
    """
    if not arrs:
        return None
    undecidable = False
    for arr in arrs:
        for indexer in arr.indexers:
            if not matches(indexer):
                continue
            match _is_a_torrent_source(indexer):
                case True:
                    return True
                case None:
                    undecidable = True
    return None if undecidable else False


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
    """
    if key not in category:
        return False
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
    """What every seeder-absorption route needs before share limits matter at all.

    Shared by all four of them because it is the same physical claim each time:
    the queue is managed, ``max_active_torrents`` can run out, and slow
    torrents are not exempt from it. What the routes disagree about is only
    whether anything ever releases a slot.
    """
    return (
        queueing,
        premise("qbt.max_active_torrents_binds", _max_active_torrents_binds(qbt)),
        premise("qbt.slow_exempt_off", _not(qbt.dont_count_slow_torrents)),
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
    premises = _slot_premises(qbt, queueing) + (
        premise("qbt.no_global_ratio", _not(qbt.max_ratio_enabled)),
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

    **It is therefore the route with the widest false-FAIL exposure to the one
    release gate this file does not model at all.** A code review measured it:
    ``processTorrentShareLimits`` has three arms, and
    ``effectiveInactiveSeedingTimeLimit()`` redirects ``-2`` to
    ``globalMaxInactiveSeedingMinutes()`` exactly as the other two do. Sonarr
    never sets a per-torrent inactive limit, so that arm is *always* deferring
    and a global inactive-seeding-time limit does release these torrents.
    ``max_inactive_seeding_time_enabled`` is not collected, so no route can see
    it — but ``SEEDING`` at least only reaches its FAIL with the other two
    globals off, whereas this one fires whatever they say. Until the preference
    is collected, say so rather than assert the operator has no setting left.
    """
    premises = _slot_premises(qbt, queueing) + (
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
    premises = _slot_premises(qbt, queueing) + (
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
    premises = _slot_premises(qbt, queueing) + (
        premise("qbt.no_global_ratio", _not(qbt.max_ratio_enabled)),
        premise("qbt.no_category_limits", _no_category_sets_its_own_limit(qbt)),
        premise(
            "arr.indexer_overrides_the_seed_time_limit",
            _any_torrent_indexer(arrs, _overrides_the_seed_time_limit),
        ),
    )
    return conflict_if(
        INVARIANT_ID, f"qbittorrent[{qbt.name}]", *premises, conflict=OVERRIDE_SEED_TIME
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
    )
    return _note_unreadable_seed_criteria(_worst_of(routes, arrs), arrs)
