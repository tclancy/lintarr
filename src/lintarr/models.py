"""Normalised snapshot types.

Multi-instance from day one: separate 4K and anime arr instances, and multiple
download clients per arr, are the common case, and retrofitting multiplicity
after invariants exist is expensive.
"""

from dataclasses import dataclass
from typing import Any

from lintarr.facts import Fact


@dataclass(frozen=True, slots=True)
class QbtInstance:
    name: str
    version: str
    queueing_enabled: Fact[bool]
    max_active_downloads: Fact[int]
    max_active_uploads: Fact[int]
    max_active_torrents: Fact[int]
    dont_count_slow_torrents: Fact[bool]
    max_ratio_enabled: Fact[bool]
    max_ratio: Fact[float]
    max_ratio_act: Fact[int]
    max_seeding_time_enabled: Fact[bool]
    max_seeding_time: Fact[int]
    categories: Fact[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class IndexerFacts:
    name: str
    # A Fact, not a bare str: the flagship premise is "any enabled *torrent*
    # indexer lacking seed criteria". An indexer whose protocol did not parse
    # must not be silently classified as not-a-torrent and dropped from the
    # predicate — that reports PASS on the very failure lintarr exists to find.
    protocol: Fact[str]
    enable_rss: Fact[bool]
    enable_automatic_search: Fact[bool]
    enable_interactive_search: Fact[bool]
    seed_ratio: Fact[float]
    seed_time: Fact[int]
    season_pack_seed_time: Fact[int]


@dataclass(frozen=True, slots=True)
class ArrInstance:
    name: str
    kind: str
    version: str
    indexers: tuple[IndexerFacts, ...]


@dataclass(frozen=True, slots=True)
class StackFacts:
    qbits: tuple[QbtInstance, ...]
    arrs: tuple[ArrInstance, ...]
    errors: tuple[tuple[str, str], ...] = ()
