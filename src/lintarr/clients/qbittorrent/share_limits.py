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
sonarr season pack ``seasonPackSeedTime`` is the seeding-time limit. So an arm
reached through an indexer goal or an inherited global is recognised, and an
arm reached through a category's own limit is not.

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
from enum import Enum, auto
from typing import Any

from lintarr.clients.qbittorrent.queue_liveness import (
    USE_GLOBAL,
    _all_of,
    _any_of,
    _as_limit,
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
    #: The category's own limit is reachable. The arr does not recognise it.
    OWN = auto()
    #: The inherited global limit is reachable. The arr recognises it.
    GLOBAL = auto()
    #: Nothing on this arm can ever be reached.
    BLOCKED = auto()
    #: This level hands the question to the next one (``-2``, absent or null).
    DEFER = auto()


_FIRING = frozenset({Reach.GOAL, Reach.OWN, Reach.GLOBAL})


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


def _seed_time_criterion_reach(arr: ArrInstance, indexer: IndexerFacts) -> Reach | None:
    """The seed-time criterion, where a sonarr season-pack goal is also a goal."""
    if arr.kind == "sonarr" and _is_a_seed_goal(indexer.season_pack_seed_time):
        return Reach.GOAL
    return _criterion_reach(indexer.seed_time)


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


def _through_category(
    qbt: QbtInstance,
    category: str,
    key: str,
    global_reachable: Callable[[QbtInstance], bool | None],
) -> Reach | None:
    """The category's own limit, else (only when it inherits) the global."""
    body = _category_body(qbt, category)
    if body is None:
        return None
    own = _category_limit_reach(body, key)
    if own is not Reach.DEFER:
        return own
    return _global_reach(global_reachable(qbt))


def _ratio_arm(qbt: QbtInstance, indexer: IndexerFacts, category: str) -> Reach | None:
    criterion = _criterion_reach(indexer.seed_ratio)
    if criterion is not Reach.DEFER:
        return criterion
    return _through_category(qbt, category, "ratio_limit", _global_ratio_reachable)


def _seed_time_arm(
    qbt: QbtInstance, arr: ArrInstance, indexer: IndexerFacts, category: str
) -> Reach | None:
    criterion = _seed_time_criterion_reach(arr, indexer)
    if criterion is not Reach.DEFER:
        return criterion
    return _through_category(qbt, category, "seeding_time_limit", _global_seed_time_reachable)


def _inactive_arm(qbt: QbtInstance, category: str) -> Reach | None:
    """No indexer criterion: the arr sets no per-torrent inactive limit."""
    return _through_category(
        qbt, category, "inactive_seeding_time_limit", _global_inactive_reachable
    )


def _arms(
    qbt: QbtInstance, arr: ArrInstance, indexer: IndexerFacts, category: str
) -> tuple[Reach | None, ...]:
    return (
        _ratio_arm(qbt, indexer, category),
        _seed_time_arm(qbt, arr, indexer, category),
        _inactive_arm(qbt, category),
    )


def _fires(reach: Reach | None) -> bool | None:
    return None if reach is None else reach in _FIRING


def _fires_unrecognised(reach: Reach | None) -> bool | None:
    return None if reach is None else reach is Reach.OWN


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
    """Some limit *arr* does not recognise — a category's own — can be reached."""
    return _some_torrent_indexer(
        arr, lambda i: _any_of(_fires_unrecognised(r) for r in _arms(qbt, arr, i, category))
    )
