"""Per category: what reaching a share limit does, and whether one can be reached.

Three questions ``orphan-inevitability`` asks of a qBittorrent category ``k``
on behalf of one arr:

- ``effective_action``: the share-limit action qBittorrent applies to a
  torrent filed under ``k``;
- ``can_fire``: whether *some* seed limit can be reached for a torrent that
  arr grabbed into ``k``;
- ``can_fire_unrecognised``: whether some limit the arr does **not** recognise
  can be reached.

**What the arr recognises**, read from Sonarr ``v5-develop`` (Radarr shares
the shape): ``HasReachedSeedLimit`` and its siblings read the torrent's own
limit when it is ``>= 0``, and only when the torrent's limit is ``-2`` do they
read the **global** preferences. They never read a category's limits. Indexer
seed criteria become the torrent's own limits when the arr adds it, and for a
sonarr season pack ``seasonPackSeedTime`` alone is the seeding-time limit
(never ``seedTime``; see ``_season_pack_arm``), so season packs are an arm of
their own. So an arm reached through an indexer goal or an inherited global is
recognised.

An arm reached through a category's own limit is recognised only when the
**same-arm global covers it**. The torrent's own limit is still ``-2``, so the
arr checks the global for that arm; a torrent qBittorrent stops at the
category's value has that ratio or seeding time, and a stopped torrent's
counters stop accruing. So the arr removes it exactly when the same-arm global
is reachable and at or below the category's value (``_global_covers``). An
unreadable global flag or value leaves that arm's recognition unknown
(``Reach.OWN_UNSURE``), which only ``can_fire_unrecognised`` reads.

**All three arms read that one rule, the inactive one included** (lintarr#59).
``HasReachedInactiveSeedingTimeLimit`` has the same shape as its siblings —
the torrent's own limit when it is ``>= 0``, else ``config.MaxInactiveSeedingTime``
when the torrent's limit is ``-2`` — and qBittorrent publishes
``max_inactive_seeding_time`` beside its flag, deriving the flag as
``(shareLimits.inactiveSeedingTimeLimit >= 0)`` and clamping the value with
``std::max(..., NO_SEEDING_TIME_LIMIT)``, exactly as for the seeding-time pair
(``appcontroller.cpp``, ``sessionimpl.cpp`` and ``sharelimits.h`` on
qBittorrent ``master``, read 2026-11-21). So the flag alone still answers
reachability, and the value answers coverage.

Known limit: a global inactive limit armed *above* the category's value reads
as unrecognised. That is the conservative half of a question this project has
not measured — the arr compares ``now - last_activity``, which unlike a ratio
or an accumulated seeding time keeps growing after qBittorrent stops the
torrent, so the global may well be reached later and the arr remove it then.
Whether a stopped torrent keeps reporting a growing ``last_activity`` gap is
unmeasured here, so that case stays a possible false FAIL rather than becoming
a guessed PASS.

**qBittorrent's side** is ``queue_liveness``'s measured semantics, imported
rather than re-derived: ``-2`` alone redirects (torrent → category → global),
every other value is returned verbatim into a ``(limit >= 0) && (count >=
limit)`` gate, ``_can_be_reached`` is the one range rule, an absent category
key is information (inherit), and an absent inactive preference is a client
with no third arm.

Each arm resolves to a ``Reach`` — which route reaches it, or that none does —
so the two reachability questions are one walk with two readings rather than
two copies of the decision tree. ``None`` is unknown throughout, and every
combination is ``_any_of``/``_all_of`` so a settled answer is never lost to an
input it did not need.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from lintarr.clients.qbittorrent.queue_liveness import (
    USE_GLOBAL,
    _all_of,
    _any_of,
    _as_limit,
    _as_number,
    _can_be_reached,
    _is_a_readable_seed_criterion,
    _is_a_seed_goal,
    _is_a_torrent_source,
    _is_an_unusable_seed_criterion,
    _no_global_ratio,
    _not_inactive_gate,
    _truth,
)
from lintarr.facts import Fact, is_known
from lintarr.models import ArrInstance, IndexerFacts, QbtInstance

#: ``share_limit_action`` strings as qBittorrent's categories endpoint reports
#: them, mapped to the ``ShareLimitAction`` values ``max_ratio_act`` uses.
ACTION_BY_NAME = {"Stop": 0, "Remove": 1, "EnableSuperSeeding": 2, "RemoveWithContent": 3}

#: The category action that hands the decision to the global ``max_ratio_act``.
DEFAULT_ACTION = "Default"


class Reach(Enum):
    """How one share-limit arm resolves for a torrent."""

    #: The indexer's own criterion is reachable. The arr recognises it.
    GOAL = auto()
    #: The category's own limit is reachable and the same-arm global does not
    #: cover it. The arr does not recognise it.
    OWN = auto()
    #: The category's own limit is reachable and the same-arm global, reachable
    #: at or below it, is what the arr checks. The arr recognises it.
    COVERED = auto()
    #: The category's own limit is reachable; whether the global covers it is unknown.
    OWN_UNSURE = auto()
    #: The inherited global limit is reachable. The arr recognises it.
    GLOBAL = auto()
    #: Nothing on this arm can ever be reached.
    BLOCKED = auto()
    #: This level hands the question to the next one (``-2``, absent or null).
    DEFER = auto()


_FIRING = frozenset({Reach.GOAL, Reach.OWN, Reach.COVERED, Reach.OWN_UNSURE, Reach.GLOBAL})


def _category_body(qbt: QbtInstance, category: str) -> dict[str, Any] | None:
    """Category *category*'s body; ``{}`` when there is none, ``None`` if unreadable.

    ``{}`` makes every key absent, which is exactly how "no such category"
    behaves: inherit the global. ``""`` never needs the map at all.
    """
    if category == "":
        return {}
    if not is_known(qbt.categories):
        return None
    categories = qbt.categories.value
    if categories is None:
        return {}
    if not isinstance(categories, dict):
        return None
    body = categories.get(category, {})
    return body if isinstance(body, dict) else None


def _category_action(body: dict[str, Any]) -> int | str | None:
    """The category's own action, ``DEFAULT_ACTION`` to defer, ``None`` if unreadable."""
    name = body.get("share_limit_action", DEFAULT_ACTION)
    if name == DEFAULT_ACTION:
        return DEFAULT_ACTION
    # A non-string (null or a number) is deliberately unknown, not a value to map.
    return ACTION_BY_NAME.get(name) if isinstance(name, str) else None


def effective_action(qbt: QbtInstance, category: str) -> int | None:
    """The share-limit action for a torrent in *category*, or ``None`` if unknown.

    A category's named action overrides; ``"Default"``, a missing key, ``""``
    and a category not in the map all use the global ``max_ratio_act``.
    """
    body = _category_body(qbt, category)
    if body is None:
        return None
    action = _category_action(body)
    if action == DEFAULT_ACTION:
        return _as_limit(qbt.max_ratio_act)
    return action if isinstance(action, int) else None


def _criterion_reach(fact: Fact[Any]) -> Reach | None:
    """An indexer criterion: a goal, an override that blocks, a deferral, or unusable."""
    if _is_a_seed_goal(fact):
        return Reach.GOAL
    if _is_an_unusable_seed_criterion(fact):
        return None
    if _is_a_readable_seed_criterion(fact) and fact.value != USE_GLOBAL:
        return Reach.BLOCKED
    return Reach.DEFER


def _category_limit_reach(body: dict[str, Any], key: str) -> Reach | None:
    """The category's own *key*: reachable, blocking, inheriting, or unreadable."""
    if key not in body:
        return Reach.DEFER
    value = body[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value == USE_GLOBAL:
        return Reach.DEFER
    return Reach.OWN if _can_be_reached(value) else Reach.BLOCKED


def _global_reach(reachable: bool | None) -> Reach | None:
    if reachable is None:
        return None
    return Reach.GLOBAL if reachable else Reach.BLOCKED


def _global_ratio_reachable(qbt: QbtInstance) -> bool | None:
    no_ratio = _no_global_ratio(qbt)
    return None if no_ratio is None else not no_ratio


def _global_seed_time_reachable(qbt: QbtInstance) -> bool | None:
    return _truth(qbt.max_seeding_time_enabled)


def _global_inactive_reachable(qbt: QbtInstance) -> bool | None:
    no_gate = _not_inactive_gate(qbt)
    return None if no_gate is None else not no_gate


def _global_covers(reachable: bool | None, value: float | None, own: float) -> bool | None:
    """A reachable global at or below the category's *own* value; ``None`` if unread.

    The value is read only when the global is reachable, so a global that is
    off settles the answer without it.
    """
    if reachable is not True:
        return reachable
    if value is None:
        return None
    return _can_be_reached(value) and value <= own


def _global_ratio_covers(qbt: QbtInstance, own: float) -> bool | None:
    return _global_covers(_global_ratio_reachable(qbt), _as_number(qbt.max_ratio), own)


def _global_seed_time_covers(qbt: QbtInstance, own: float) -> bool | None:
    return _global_covers(_global_seed_time_reachable(qbt), _as_number(qbt.max_seeding_time), own)


def _global_inactive_covers(qbt: QbtInstance, own: float) -> bool | None:
    return _global_covers(
        _global_inactive_reachable(qbt), _as_number(qbt.max_inactive_seeding_time), own
    )


def _own_reach(covered: bool | None) -> Reach:
    if covered is None:
        return Reach.OWN_UNSURE
    return Reach.COVERED if covered else Reach.OWN


@dataclass(frozen=True, slots=True)
class _GlobalArm:
    """How the global preference for one arm is read."""

    reachable: Callable[[QbtInstance], bool | None]
    covers: Callable[[QbtInstance, float], bool | None]


_RATIO = _GlobalArm(_global_ratio_reachable, _global_ratio_covers)
_SEED_TIME = _GlobalArm(_global_seed_time_reachable, _global_seed_time_covers)
_INACTIVE = _GlobalArm(_global_inactive_reachable, _global_inactive_covers)


def _through_category(qbt: QbtInstance, category: str, key: str, arm: _GlobalArm) -> Reach | None:
    """The category's own limit (covered or not), else (only when it inherits) the global."""
    body = _category_body(qbt, category)
    if body is None:
        return None
    own = _category_limit_reach(body, key)
    if own is Reach.OWN:
        return _own_reach(arm.covers(qbt, body[key]))
    if own is not Reach.DEFER:
        return own
    return _global_reach(arm.reachable(qbt))


def _ratio_arm(qbt: QbtInstance, indexer: IndexerFacts, category: str) -> Reach | None:
    criterion = _criterion_reach(indexer.seed_ratio)
    if criterion is not Reach.DEFER:
        return criterion
    return _through_category(qbt, category, "ratio_limit", _RATIO)


def _seed_time_through(qbt: QbtInstance, criterion: Fact[Any], category: str) -> Reach | None:
    """A seed-time criterion, deferring to the category's seeding time then the global."""
    reach = _criterion_reach(criterion)
    if reach is not Reach.DEFER:
        return reach
    return _through_category(qbt, category, "seeding_time_limit", _SEED_TIME)


def _seed_time_arm(qbt: QbtInstance, indexer: IndexerFacts, category: str) -> Reach | None:
    return _seed_time_through(qbt, indexer.seed_time, category)


def _season_pack_arm(qbt: QbtInstance, indexer: IndexerFacts, category: str) -> Reach | None:
    """A sonarr season pack's seed time, which is ``seasonPackSeedTime`` alone.

    Verified in Sonarr ``main`` (v4) ``src/NzbDrone.Core/Indexers/SeedConfigProvider.cs``,
    read 2026-11-19: ``var seedTime = fullSeason ? seedCriteria.SeasonPackSeedTime
    : seedCriteria.SeedTime`` and the limit is set only ``if (seedTime.HasValue)``.
    So a season pack never uses ``seedTime``: unset, the torrent keeps ``-2`` and
    defers to the category and then the global, like any other criterion. Its
    ratio is the ordinary ``seedRatio``, which ``_ratio_arm`` already covers.

    Known limit: Sonarr ``v5-develop`` adds a ``SeasonPackSeedGoal`` toggle and a
    ``SeasonPackSeedRatio``. Neither is modelled.
    """
    return _seed_time_through(qbt, indexer.season_pack_seed_time, category)


def _inactive_arm(qbt: QbtInstance, category: str) -> Reach | None:
    """No indexer criterion: the arr sets no per-torrent inactive limit."""
    return _through_category(qbt, category, "inactive_seeding_time_limit", _INACTIVE)


def _arms(
    qbt: QbtInstance, arr: ArrInstance, indexer: IndexerFacts, category: str
) -> tuple[Reach | None, ...]:
    """Every arm for this indexer's torrents; season packs add one for sonarr."""
    arms = (
        _ratio_arm(qbt, indexer, category),
        _seed_time_arm(qbt, indexer, category),
        _inactive_arm(qbt, category),
    )
    if arr.kind != "sonarr":
        return arms
    return (*arms, _season_pack_arm(qbt, indexer, category))


def _fires(reach: Reach | None) -> bool | None:
    return None if reach is None else reach in _FIRING


def _fires_unrecognised(reach: Reach | None) -> bool | None:
    if reach is None or reach is Reach.OWN_UNSURE:
        return None
    return reach is Reach.OWN


def _some_torrent_indexer(
    arr: ArrInstance, holds: Callable[[IndexerFacts], bool | None]
) -> bool | None:
    """Some indexer that grabs torrents satisfies *holds*; no indexers is ``False``."""
    return _any_of(_all_of((_is_a_torrent_source(i), holds(i))) for i in arr.indexers)


def can_fire(qbt: QbtInstance, arr: ArrInstance, category: str) -> bool | None:
    """Some seed limit can be reached for a torrent *arr* grabs into *category*."""
    return _some_torrent_indexer(
        arr, lambda i: _any_of(_fires(r) for r in _arms(qbt, arr, i, category))
    )


def can_fire_unrecognised(qbt: QbtInstance, arr: ArrInstance, category: str) -> bool | None:
    """Some limit *arr* does not recognise can be reached.

    That is a category's own limit the same-arm global does not cover.
    """
    return _some_torrent_indexer(
        arr, lambda i: _any_of(_fires_unrecognised(r) for r in _arms(qbt, arr, i, category))
    )
