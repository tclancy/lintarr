"""Sonarr/Radarr adapter (v3 API — identical shape for both).

Seed criteria live on the *indexer*, not the download client. Verified against
a live Sonarr: /api/v3/downloadclient carries no seed fields, while each
/api/v3/indexer entry has a ``fields`` list containing
``seedCriteria.seedRatio``, ``seedCriteria.seedTime`` and
``seedCriteria.seasonPackSeedTime``.

There is also no plain ``enable`` key on an indexer. Verified against a live
Sonarr/Radarr: an indexer's top-level keys include ``enableRss``,
``enableAutomaticSearch`` and ``enableInteractiveSearch`` instead — three
independent toggles, not one. Defaulting an absent key to ``False`` would be
this project's cardinal sin (a defaulted fact masquerading as a real one), so
each is read as its own ``Fact`` rather than collapsed into a bare bool.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.config import ArrConfig
from lintarr.facts import Fact, Known, Unknown, read
from lintarr.models import (
    ArrDownloadClient,
    ArrInstance,
    IndexerFacts,
    RemotePathMapping,
    RootFolder,
)

_STATUS = "/api/v3/system/status"
_INDEXER = "/api/v3/indexer"
_MEDIA_MANAGEMENT = "/api/v3/config/mediamanagement"
_ROOT_FOLDER = "/api/v3/rootfolder"
_DISK_SPACE = "/api/v3/diskspace"
_REMOTE_PATH_MAPPING = "/api/v3/remotepathmapping"
_DOWNLOAD_CLIENT = "/api/v3/downloadclient"

_SEED_FIELDS = {
    "seed_ratio": "seedCriteria.seedRatio",
    "seed_time": "seedCriteria.seedTime",
    "season_pack_seed_time": "seedCriteria.seasonPackSeedTime",
}

# Derived, not restated: the duplicate-conflict guard in ``_single_value`` is
# scoped to the names ``_indexer_facts`` actually reads out of the mapping, so
# adding a seed criterion above extends the guard with no second edit here.
_READ_FIELD_NAMES = frozenset(_SEED_FIELDS.values())

# These live at the indexer's top level, unlike the seed criteria above which
# are nested inside its ``fields`` list.
_ENABLE_FIELDS = {
    "enable_rss": "enableRss",
    "enable_automatic_search": "enableAutomaticSearch",
    "enable_interactive_search": "enableInteractiveSearch",
}


def _field_entries(indexer: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch one indexer's ``fields`` list, rejecting any shape that is not a list of objects.

    The silent shapes are the reason this is a hard error rather than a skip.
    Iterating a string, an object, or a list of strings yields *strings*, and
    ``"value" in "seedCriteria.seedRatio"`` is a substring test rather than a
    key test. It answers ``False``, so every entry is filtered out, the
    mapping comes back empty and the seed criteria become
    ``Unknown("field-absent")`` — a malformed payload reported as "this arr
    version does not expose seed criteria". That is a defaulted fact
    masquerading as a real one, which is the thing this project exists to
    refuse.

    Whether a non-dict entry is silent or loud turns on the *type*, not on
    being a non-dict: ``"value" in x`` is a membership test for anything
    iterable, so ``[["x"]]`` and ``[()]`` were silent too, and only an entry
    that is not iterable at all (``[7]``, ``[None]``) raised ``TypeError``.
    Both halves are rejected here, so the distinction only matters for
    reading the measurements this guard was built from.

    An explicit ``"fields": null`` is a bad response, while a *missing*
    ``fields`` key is not: absence matches how the three top-level ``enable*``
    keys behave on older arr versions, and falls through to
    ``Unknown("field-absent")`` for every field. Present-but-null is a shape
    no arr emits, so it is a malformed payload rather than an old one.
    """
    fields = indexer.get("fields", [])
    if not isinstance(fields, list) or not all(isinstance(f, dict) for f in fields):
        raise ServiceError("bad-response", f"{_INDEXER}: 'fields' is not an array of objects")
    return fields


def _field_name(entry: dict[str, Any]) -> str:
    """A ``fields`` entry with no usable name is a malformed payload, not an unnamed field.

    Returns the *stripped* name, as ``_read_version`` does. This name is a
    lookup key, not a label: validating with ``.strip()`` and then returning
    the padded original would key the mapping on ``"  seedCriteria.seedRatio  "``,
    so a ratio the operator really had configured would come back
    ``Unknown("field-absent")`` — the same "this version does not expose it"
    lie the guard exists to refuse, re-entered through the guard itself.
    """
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ServiceError("bad-response", f"{_INDEXER}: a 'fields' entry has no 'name' string")
    return name.strip()


def _fields_as_mapping(indexer: dict[str, Any]) -> dict[str, Any]:
    """Flatten the arr ``fields`` list into ``{name: value}``.

    A name absent here means the running version does not expose it; a name
    present with ``None`` means configured-but-unset. Those are different
    facts, so an entry carrying no ``value`` key at all must not be admitted
    to the mapping — doing so would turn "never read" into ``Known(None)``,
    which is exactly the defaulting this project exists to refuse. Entries
    without ``value`` fall through to ``read()``'s absent branch and become
    ``Unknown("field-absent")``.

    Names are resolved for *every* entry before the ``value`` filter runs, not
    inside the comprehension. ``value`` is legitimately absent on any
    never-configured setting — the commonest live shape — so a name check
    evaluated only for entries that carry one would leave most of a real
    payload unvalidated.

    A repeated name used to resolve last-win, silently (#6): two entries both
    claiming ``seedCriteria.seedRatio`` returned the second one's value as
    ``Known``, which reads as *more* trustworthy than ``Unknown`` while being a
    coin toss on list order. Grouping instead hands the decision to
    ``_single_value`` — but only for the names this collector reads. A name
    outside ``_READ_FIELD_NAMES`` keeps resolving last-win, because erroring the
    whole instance over ambiguity in data nobody consumes would discard every
    fact we *can* read to protect against a misreading that cannot happen.
    """
    grouped: dict[str, list[Any]] = {}
    for name, entry in ((_field_name(f), f) for f in _field_entries(indexer)):
        if "value" in entry:
            grouped.setdefault(name, []).append(entry["value"])
    return {
        name: _single_value(name, values) if name in _READ_FIELD_NAMES else values[-1]
        for name, values in grouped.items()
    }


def _interchangeable(value: Any, other: Any) -> bool:
    """Whether two duplicate values are the same *fact*, not merely ``==``.

    ``False == 0`` and ``True == 1`` in Python, and the invariants layer draws
    its line in exactly that gap: ``_is_a_seed_goal`` excludes ``bool``
    explicitly — because ``isinstance(True, int)`` — while ``0`` reads as a goal
    of zero. So ``[0, False]`` and ``[False, 0]`` would give one indexer
    opposite verdicts on the flagship premise depending on list order, which is
    the position-decided fact this guard exists to refuse, re-entered through
    the guard itself.

    Bool-ness rather than ``type()``: ``2`` and ``2.0`` *are* one fact here,
    since ``_is_a_seed_goal`` admits ``int`` and ``float`` alike, and splitting
    them would raise a conflict over a payload nobody could misread.

    Two bare ``NaN`` entries — which ``json.loads`` does accept — report as a
    conflict, because ``nan != nan``. Left alone: a ``NaN`` seed goal is already
    unusable downstream, so the only cost is a slightly wrong error message on a
    payload that errors either way.
    """
    return isinstance(value, bool) == isinstance(other, bool) and value == other


def _single_value(name: str, values: list[Any]) -> Any:
    """The one value *name* carries, or a bad response if its duplicates disagree.

    Conflicting duplicates only. An agreeing repeat is readable — first-win and
    last-win give the same answer, so there is nothing to be wrong about — and
    rejecting it would turn a payload that costs nothing to read into an ERROR.
    A *disagreeing* repeat is a payload this collector genuinely cannot read:
    ``Known(1.0)`` and ``Known(9.0)`` are different operator intents, and
    picking one by position is the fabrication ``_field_name`` and
    ``_field_entries`` already refuse one line above.

    ``bad-response`` rather than ``Unknown``, which looks like the humbler
    answer and is not: ``_lacks_seed_criteria`` is deliberately total and reads
    ``Unknown`` as *no seed goal*, so an unreadable duplicate would arrive at
    the flagship check as a confident **FAIL**. That is last-win's fabrication
    pointed the other way. Raising is the only reading that admits ignorance.

    Compared value-by-value rather than through a set, because a ``value`` is
    whatever JSON put there and lists and dicts are unhashable. Entries with no
    ``value`` key never reach here, so "absent" is not one of the readings in
    play: a bare duplicate beside a valued one is not a conflict, since only one
    of them says anything about what the operator set.
    """
    first, *rest = values
    if any(not _interchangeable(value, first) for value in rest):
        raise ServiceError(
            "bad-response",
            f"{_INDEXER}: 'fields' gives {name!r} two different values",
        )
    return first


#: The arrs this adapter reads, by the kind their ``appName`` normalises to.
ARR_KINDS = frozenset({"sonarr", "radarr"})

#: The kind half of every arr error label, ``arr[<name>]``. A failed arr may
#: never have said which arr it is, so its label names the instance instead.
ARR_LABEL_KIND = "arr"


def _status(client: ReadOnlyClient) -> dict[str, Any]:
    payload = client.get_json(_STATUS)
    if not isinstance(payload, dict):
        raise ServiceError("bad-response", f"{_STATUS}: expected a JSON object")
    return payload


def _read_version(status: dict[str, Any]) -> str:
    """Read the instance version, or fail.

    The version is stamped onto every fact this instance produces and gates
    version-ranged axioms downstream, so a missing or null one is ERROR rather
    than a guess: ``str(None)`` would fabricate the literal version ``'None'``.
    """
    version = status.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ServiceError("bad-response", f"{_STATUS}: no usable 'version' string")
    return version.strip()


def _read_kind(status: dict[str, Any]) -> str:
    """Which arr this is, from its own ``appName``, or fail.

    The kind is not configured, so this is the only place it is known. A
    missing or unfamiliar ``appName`` is ERROR rather than a guess: reading a
    Lidarr's indexers as a Sonarr's would be a fact about the wrong service.
    """
    value = status.get("appName")
    kind = value.strip().lower() if isinstance(value, str) else None
    if kind not in ARR_KINDS:
        raise ServiceError(
            "bad-response",
            f"{_STATUS}: appName {value!r} is not a supported arr ({', '.join(sorted(ARR_KINDS))})",
        )
    return kind


def _indexer_payloads(client: ReadOnlyClient) -> list[dict[str, Any]]:
    """Fetch the indexer list, rejecting any shape that is not a list of objects.

    An arr behind a misconfigured reverse proxy can answer 200 with
    ``{"message": "Unauthorized"}``. Indexing into that blindly raises
    ``AttributeError`` out of the adapter and aborts the whole run, which the
    stack layer explicitly promises not to do.
    """
    payload = client.get_json(_INDEXER)
    if not isinstance(payload, list) or not all(isinstance(i, dict) for i in payload):
        raise ServiceError("bad-response", f"{_INDEXER}: expected a JSON array of objects")
    return payload


def _indexer_name(raw: dict[str, Any]) -> str:
    """An indexer with no usable name is a malformed payload, not an unnamed indexer."""
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ServiceError("bad-response", f"{_INDEXER}: an entry has no 'name' string")
    return name


def _indexer_facts(raw: dict[str, Any], *, version: str) -> IndexerFacts:
    name = _indexer_name(raw)
    mapping = _fields_as_mapping(raw)
    source = f"GET {_INDEXER}[{name}]"
    return IndexerFacts(
        name=name,
        # protocol decides whether the flagship "enabled torrent indexer
        # lacking seed criteria" premise even applies to this indexer, so an
        # unread protocol must not silently classify it as not-a-torrent.
        protocol=read(raw, "protocol", source=source, version=version),
        **{
            attr: read(raw, key, source=source, version=version)
            for attr, key in _ENABLE_FIELDS.items()
        },
        **{
            attr: read(mapping, key, source=source, version=version)
            for attr, key in _SEED_FIELDS.items()
        },
    )


def _optional[T](
    client: ReadOnlyClient,
    path: str,
    reader: Callable[[ReadOnlyClient, str], Fact[T]],
    *,
    version: str,
) -> Fact[T]:
    """Read one endpoint that an older arr may not serve.

    Only a 404 means "this version has no such endpoint". Anything else, a 500
    or a 401 included, is a service we could not read and must ERROR the arr as
    the indexer read does, not be quietly downgraded to an unknown.
    """
    try:
        return reader(client, version)
    except ServiceError as exc:
        if exc.status == 404:
            return Unknown(reason="field-absent", detail=f"{path} answered 404")
        raise


def _known[T](value: T, path: str, version: str) -> Known[T]:
    return Known(
        value=value, source=f"GET {path}", read_at=datetime.now(UTC), service_version=version
    )


def _array_of_objects(client: ReadOnlyClient, path: str) -> list[dict[str, Any]]:
    payload = client.get_json(path)
    if not isinstance(payload, list) or not all(isinstance(i, dict) for i in payload):
        raise ServiceError("bad-response", f"{path}: expected a JSON array of objects")
    return payload


def _required_str(entry: dict[str, Any], key: str, path: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        raise ServiceError("bad-response", f"{path}: an entry has no {key!r} string")
    return value


def _read_copy_using_hardlinks(client: ReadOnlyClient, version: str) -> Fact[bool]:
    """A non-bool is a bad response: a later ``"false"`` would read as truthy."""
    payload = client.get_json(_MEDIA_MANAGEMENT)
    if not isinstance(payload, dict):
        raise ServiceError("bad-response", f"{_MEDIA_MANAGEMENT}: expected a JSON object")
    if "copyUsingHardlinks" not in payload:
        return Unknown(
            reason="field-absent",
            detail=f"'copyUsingHardlinks' absent from GET {_MEDIA_MANAGEMENT}",
        )
    value = payload["copyUsingHardlinks"]
    if not isinstance(value, bool):
        raise ServiceError("bad-response", f"{_MEDIA_MANAGEMENT}: 'copyUsingHardlinks' not a bool")
    return _known(value, _MEDIA_MANAGEMENT, version)


def _root_folder(entry: dict[str, Any]) -> RootFolder:
    accessible = entry.get("accessible")
    if accessible is not None and not isinstance(accessible, bool):
        raise ServiceError("bad-response", f"{_ROOT_FOLDER}: 'accessible' is not a bool")
    return RootFolder(path=_required_str(entry, "path", _ROOT_FOLDER), accessible=accessible)


def _read_root_folders(client: ReadOnlyClient, version: str) -> Fact[tuple[RootFolder, ...]]:
    folders = tuple(_root_folder(e) for e in _array_of_objects(client, _ROOT_FOLDER))
    return _known(folders, _ROOT_FOLDER, version)


def _read_mounts(client: ReadOnlyClient, version: str) -> Fact[tuple[str, ...]]:
    paths = tuple(
        _required_str(e, "path", _DISK_SPACE) for e in _array_of_objects(client, _DISK_SPACE)
    )
    return _known(paths, _DISK_SPACE, version)


def _mapping(entry: dict[str, Any]) -> RemotePathMapping:
    return RemotePathMapping(
        host=_required_str(entry, "host", _REMOTE_PATH_MAPPING),
        remote_path=_required_str(entry, "remotePath", _REMOTE_PATH_MAPPING),
        local_path=_required_str(entry, "localPath", _REMOTE_PATH_MAPPING),
    )


def _read_remote_path_mappings(
    client: ReadOnlyClient, version: str
) -> Fact[tuple[RemotePathMapping, ...]]:
    mappings = tuple(_mapping(e) for e in _array_of_objects(client, _REMOTE_PATH_MAPPING))
    return _known(mappings, _REMOTE_PATH_MAPPING, version)


_CATEGORY_FIELDS = ("tvCategory", "movieCategory")


def _named_client_field(entry: dict[str, Any], names: tuple[str, ...]) -> tuple[str, Any] | None:
    """The name and value of the first of *names* this client's ``fields`` list carries.

    The name comes back with the value so a complaint about the value can say
    which field it read: a Sonarr carries ``tvCategory`` and a Radarr
    ``movieCategory``, and naming the wrong one sends the operator looking for
    a field their app does not have.
    """
    fields = entry.get("fields", [])
    if not isinstance(fields, list) or not all(isinstance(f, dict) for f in fields):
        raise ServiceError(
            "bad-response", f"{_DOWNLOAD_CLIENT}: 'fields' is not an array of objects"
        )
    for name in names:
        for f in fields:
            if f.get("name") == name:
                return name, f.get("value")
    return None


def _client_field(entry: dict[str, Any], names: tuple[str, ...]) -> Any:
    """The value of the first of *names* this client's ``fields`` list carries."""
    found = _named_client_field(entry, names)
    return found[1] if found is not None else None


def _optional_str_field(entry: dict[str, Any], names: tuple[str, ...], absent: str | None):
    """A client field that is a string, or *absent* when missing or null; junk is bad-response."""
    found = _named_client_field(entry, names)
    if found is None:
        return absent
    name, value = found
    if value is None:
        return absent
    if not isinstance(value, str):
        raise ServiceError("bad-response", f"{_DOWNLOAD_CLIENT}: {name!r} is not a string")
    return value


_IMPORTED_CATEGORY_FIELDS = ("tvImportedCategory", "movieImportedCategory")


def _remove_completed(entry: dict[str, Any]) -> bool | None:
    value = entry.get("removeCompletedDownloads")
    return value if isinstance(value, bool) else None


def _imported_category(entry: dict[str, Any]) -> str:
    value = _client_field(entry, _IMPORTED_CATEGORY_FIELDS)
    return value if isinstance(value, str) and value else ""


def _download_client(entry: dict[str, Any]) -> ArrDownloadClient:
    enabled = entry.get("enable")
    if not isinstance(enabled, bool):
        raise ServiceError("bad-response", f"{_DOWNLOAD_CLIENT}: 'enable' is not a bool")
    host = _optional_str_field(entry, ("host",), None)
    category = _optional_str_field(entry, _CATEGORY_FIELDS, "")
    return ArrDownloadClient(
        name=_required_str(entry, "name", _DOWNLOAD_CLIENT),
        implementation=_required_str(entry, "implementation", _DOWNLOAD_CLIENT),
        enabled=enabled,
        host=host,
        category=category,
        remove_completed=_remove_completed(entry),
        imported_category=_imported_category(entry),
    )


def _read_download_clients(
    client: ReadOnlyClient, version: str
) -> Fact[tuple[ArrDownloadClient, ...]]:
    clients = tuple(_download_client(e) for e in _array_of_objects(client, _DOWNLOAD_CLIENT))
    return _known(clients, _DOWNLOAD_CLIENT, version)


def collect_arr(client: ReadOnlyClient, cfg: ArrConfig) -> ArrInstance:
    status = _status(client)
    version, kind = _read_version(status), _read_kind(status)
    indexers = tuple(_indexer_facts(raw, version=version) for raw in _indexer_payloads(client))

    def optional(path: str, reader):
        return _optional(client, path, reader, version=version)

    return ArrInstance(
        name=cfg.name,
        kind=kind,
        version=version,
        indexers=indexers,
        copy_using_hardlinks=optional(_MEDIA_MANAGEMENT, _read_copy_using_hardlinks),
        root_folders=optional(_ROOT_FOLDER, _read_root_folders),
        mounts=optional(_DISK_SPACE, _read_mounts),
        remote_path_mappings=optional(_REMOTE_PATH_MAPPING, _read_remote_path_mappings),
        download_clients=optional(_DOWNLOAD_CLIENT, _read_download_clients),
    )
