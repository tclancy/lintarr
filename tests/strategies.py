"""Hypothesis strategies for the fact and snapshot types.

Kept separate from the property tests so a strategy stays usable from an
example-based test, and so the *shape* of what gets generated is reviewable on
its own. The values deliberately include every shape the adapters have been
observed to emit and a few they have not: a service that answers ``null``, a
limit that arrives as a string, a category map that is a list. Each of those
has at some point reached a predicate that assumed otherwise.
"""

from datetime import UTC, datetime

from hypothesis import strategies as st

from lintarr.facts import Known, Unknown
from lintarr.models import ArrInstance, IndexerFacts, QbtInstance, StackFacts

#: Fixed so a shrunk counterexample is reproducible; the predicates under test
#: never read it.
READ_AT = datetime(2026, 10, 3, 5, 0, tzinfo=UTC)

#: The boundary values that carry meaning to ``queue_liveness``: -2 inherits a
#: global category limit, -1 is the only value meaning unlimited, 0 wedges
#: outright, and -3 is a negative that is neither. Drawn explicitly because
#: uniform integers reach them too rarely to be relied on.
LIMIT_VALUES = st.one_of(
    st.sampled_from([-3, -2, -1, 0, 1, 3, 5, 10]),
    st.integers(min_value=-50, max_value=50),
)

#: Values a service can put where a scalar was expected. ``None`` is a real
#: answer (Sonarr clears a seed goal that way), the rest are malformed reads
#: that must not be coerced into numbers or booleans.
JUNK_VALUES = st.one_of(
    st.none(),
    st.booleans(),
    st.text(max_size=8),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.lists(st.integers(), max_size=2),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)

UNKNOWN_FACTS = st.builds(
    Unknown,
    reason=st.sampled_from(["service-absent", "field-absent", "insufficient-permission"]),
    detail=st.text(max_size=12),
)


def known(values: st.SearchStrategy) -> st.SearchStrategy:
    """A ``Known`` wrapping *values*."""
    return st.builds(
        Known,
        value=values,
        source=st.just("GET /generated"),
        read_at=st.just(READ_AT),
        service_version=st.sampled_from(["v5.2.4", "4.0.0"]),
    )


def facts(values: st.SearchStrategy) -> st.SearchStrategy:
    """A ``Fact`` that is either ``Known(value)`` or ``Unknown``."""
    return st.one_of(known(values), UNKNOWN_FACTS)


BOOL_FACTS = facts(st.one_of(st.booleans(), JUNK_VALUES))
LIMIT_FACTS = facts(st.one_of(LIMIT_VALUES, JUNK_VALUES))

#: A category entry. ``ratio_limit`` / ``seeding_time_limit`` may be absent
#: (a client too old to expose them), present and numeric, or present and junk
#: — three cases ``_own_share_limit`` answers differently and on purpose.
CATEGORY = st.dictionaries(
    st.sampled_from(["ratio_limit", "seeding_time_limit", "savePath"]),
    st.one_of(LIMIT_VALUES, st.floats(min_value=-3, max_value=5, width=32), JUNK_VALUES),
    max_size=3,
)

CATEGORY_MAPS = st.one_of(
    st.dictionaries(st.text(min_size=1, max_size=4), CATEGORY, max_size=3),
    st.none(),
    st.lists(CATEGORY, max_size=2),
    st.text(max_size=5),
)


def qbt_instances(names: st.SearchStrategy | None = None) -> st.SearchStrategy:
    return st.builds(
        QbtInstance,
        name=names or st.text(min_size=1, max_size=5),
        version=st.sampled_from(["v5.2.4", "5.2.4", ""]),
        queueing_enabled=BOOL_FACTS,
        max_active_downloads=LIMIT_FACTS,
        max_active_uploads=LIMIT_FACTS,
        max_active_torrents=LIMIT_FACTS,
        dont_count_slow_torrents=BOOL_FACTS,
        max_ratio_enabled=BOOL_FACTS,
        max_ratio=facts(st.one_of(st.floats(allow_nan=False, width=32), JUNK_VALUES)),
        max_ratio_act=LIMIT_FACTS,
        max_seeding_time_enabled=BOOL_FACTS,
        max_seeding_time=LIMIT_FACTS,
        categories=facts(CATEGORY_MAPS),
    )


INDEXERS = st.builds(
    IndexerFacts,
    name=st.text(min_size=1, max_size=6),
    protocol=facts(st.one_of(st.sampled_from(["torrent", "usenet", "Torrent"]), JUNK_VALUES)),
    enable_rss=BOOL_FACTS,
    enable_automatic_search=BOOL_FACTS,
    enable_interactive_search=BOOL_FACTS,
    seed_ratio=facts(st.one_of(st.floats(allow_nan=False, width=32), JUNK_VALUES)),
    seed_time=LIMIT_FACTS,
    season_pack_seed_time=LIMIT_FACTS,
)

ARR_INSTANCES = st.builds(
    ArrInstance,
    name=st.text(min_size=1, max_size=5),
    kind=st.sampled_from(["sonarr", "radarr"]),
    version=st.just("4.0.0"),
    indexers=st.lists(INDEXERS, max_size=3).map(tuple),
)

#: Error rows as ``collect_stack`` writes them: ``kind[name]`` and an
#: ``ErrorKind``. ``run.py`` splits the label on ``[`` to recover the kind, so
#: a label without one is included on purpose.
ERRORS = st.tuples(
    st.one_of(
        st.builds(
            "{}[{}]".format,
            st.sampled_from(["sonarr", "radarr", "qbittorrent"]),
            st.text(min_size=1, max_size=4),
        ),
        st.text(min_size=1, max_size=6),
    ),
    st.sampled_from(["unreachable", "unauthorised", "banned", "bad-response"]),
)

DECLARED = st.frozensets(st.sampled_from(["qbittorrent", "sonarr", "radarr"]), max_size=3)


STACK_FACTS = st.builds(
    StackFacts,
    qbits=st.lists(qbt_instances(), max_size=2).map(tuple),
    arrs=st.lists(ARR_INSTANCES, max_size=2).map(tuple),
    errors=st.lists(ERRORS, max_size=2).map(tuple),
)
