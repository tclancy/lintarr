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

from typing import Any

from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.config import ArrConfig
from lintarr.facts import read
from lintarr.models import ArrInstance, IndexerFacts

_STATUS = "/api/v3/system/status"
_INDEXER = "/api/v3/indexer"

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


def _read_version(client: ReadOnlyClient) -> str:
    """Read the instance version, or fail.

    The version is stamped onto every fact this instance produces and gates
    version-ranged axioms downstream, so a missing or null one is ERROR rather
    than a guess: ``str(None)`` would fabricate the literal version ``'None'``.
    """
    payload = client.get_json(_STATUS)
    if not isinstance(payload, dict):
        raise ServiceError("bad-response", f"{_STATUS}: expected a JSON object")
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ServiceError("bad-response", f"{_STATUS}: no usable 'version' string")
    return version.strip()


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


def collect_arr(client: ReadOnlyClient, cfg: ArrConfig) -> ArrInstance:
    version = _read_version(client)
    indexers = tuple(_indexer_facts(raw, version=version) for raw in _indexer_payloads(client))
    return ArrInstance(name=cfg.name, kind=cfg.kind, version=version, indexers=indexers)
