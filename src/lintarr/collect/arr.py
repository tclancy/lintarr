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

# These live at the indexer's top level, unlike the seed criteria above which
# are nested inside its ``fields`` list.
_ENABLE_FIELDS = {
    "enable_rss": "enableRss",
    "enable_automatic_search": "enableAutomaticSearch",
    "enable_interactive_search": "enableInteractiveSearch",
}


def _fields_as_mapping(indexer: dict[str, Any]) -> dict[str, Any]:
    """Flatten the arr ``fields`` list into ``{name: value}``.

    A name absent here means the running version does not expose it; a name
    present with ``None`` means configured-but-unset. Those are different
    facts, so an entry carrying no ``value`` key at all must not be admitted
    to the mapping — doing so would turn "never read" into ``Known(None)``,
    which is exactly the defaulting this project exists to refuse. Entries
    without ``value`` fall through to ``read()``'s absent branch and become
    ``Unknown("field-absent")``.
    """
    return {f["name"]: f["value"] for f in indexer.get("fields", []) if "value" in f}


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
