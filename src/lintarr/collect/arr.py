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

from lintarr.collect.http import ReadOnlyClient
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
    present with ``None`` means configured-but-unset. Those are different facts.
    """
    return {f["name"]: f.get("value") for f in indexer.get("fields", [])}


def collect_arr(client: ReadOnlyClient, cfg: ArrConfig) -> ArrInstance:
    version = str(client.get_json(_STATUS).get("version", "")).strip()
    indexers = []
    for raw in client.get_json(_INDEXER):
        mapping = _fields_as_mapping(raw)
        source = f"GET {_INDEXER}[{raw.get('name')}]"
        indexers.append(
            IndexerFacts(
                name=raw.get("name", ""),
                protocol=raw.get("protocol", ""),
                **{
                    attr: read(raw, key, source=source, version=version)
                    for attr, key in _ENABLE_FIELDS.items()
                },
                **{
                    attr: read(mapping, key, source=source, version=version)
                    for attr, key in _SEED_FIELDS.items()
                },
            )
        )
    return ArrInstance(name=cfg.name, kind=cfg.kind, version=version, indexers=tuple(indexers))
