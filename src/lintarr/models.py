"""Normalised snapshot types.

Multi-instance from day one: separate 4K and anime arr instances, and multiple
download clients per arr, are the common case, and retrofitting multiplicity
after invariants exist is expensive.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lintarr.facts import Fact

if TYPE_CHECKING:
    from lintarr.collect.http import ErrorKind


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
    max_inactive_seeding_time_enabled: Fact[bool]
    categories: Fact[dict[str, Any]]
    save_path: Fact[str]
    auto_tmm_enabled: Fact[bool]
    use_category_paths_in_manual_mode: Fact[bool]


@dataclass(frozen=True, slots=True)
class ClientFacts:
    """Client-neutral facts every backend reports.

    A neutral field is added in the same change as the first shared check that
    reads it, and populated by every registered backend then.

    ``save_paths`` maps each category to the absolute directory, in the
    client's own namespace, where a new download in that category is saved.
    The key ``""`` is the uncategorised location.
    """

    save_paths: Fact[Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class ClientInstance:
    """One collected download client, whatever its kind.

    ``specific`` is the backend's own facts object — a ``QbtInstance`` for
    qBittorrent — and only that backend's checks read it.
    """

    name: str
    kind: str
    version: str
    common: ClientFacts
    specific: object


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
class RootFolder:
    path: str
    #: ``None`` when the arr did not say; counts as accessible downstream.
    accessible: bool | None


@dataclass(frozen=True, slots=True)
class RemotePathMapping:
    host: str
    remote_path: str
    local_path: str


@dataclass(frozen=True, slots=True)
class ArrDownloadClient:
    name: str
    implementation: str
    enabled: bool
    host: str | None
    #: ``tvCategory`` or ``movieCategory``, whichever the arr carries. ``""``
    #: when absent or empty: both mean "the client's default save path".
    category: str


@dataclass(frozen=True, slots=True)
class ArrInstance:
    name: str
    kind: str
    version: str
    indexers: tuple[IndexerFacts, ...]
    copy_using_hardlinks: Fact[bool]
    root_folders: Fact[tuple[RootFolder, ...]]
    mounts: Fact[tuple[str, ...]]
    remote_path_mappings: Fact[tuple[RemotePathMapping, ...]]
    download_clients: Fact[tuple[ArrDownloadClient, ...]]


@dataclass(frozen=True, slots=True)
class ErrorRow:
    """One service that could not be read, and why.

    This was a bare ``(label, kind)`` tuple until #18. The kind is what every
    other layer matches on and the only half that reached the operator; the
    detail is where the collect layer says which path failed, which httpx
    exception was raised, or — on qBittorrent's 403 — that an IP ban may be
    active and how to clear it. Forty words of that were being written for a
    traceback nobody sees.

    ``ErrorKind`` is imported under ``TYPE_CHECKING`` only. There is no import
    cycle to dodge — ``collect.http`` imports nothing from this package — but
    this module is the inner layer and a runtime edge from it out to an HTTP
    adapter is a direction of dependency worth not creating for an annotation.
    An earlier draft of this docstring claimed a cycle; there isn't one.

    The repo declares no type checker, so the narrow annotation is documentation
    today rather than a gate. ``tests/test_error_detail_route.py`` asserts
    membership against the alias at runtime instead, which is also what catches
    prose being concatenated into the kind.
    """

    label: str
    kind: "ErrorKind"
    detail: str


@dataclass(frozen=True, slots=True)
class StackFacts:
    clients: tuple[ClientInstance, ...]
    arrs: tuple[ArrInstance, ...]
    errors: tuple[ErrorRow, ...] = ()
