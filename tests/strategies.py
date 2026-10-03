"""Hypothesis strategies for the fact and snapshot types.

Kept separate from the property tests so a strategy stays usable from an
example-based test, and so the *shape* of what gets generated is reviewable on
its own. The values deliberately include every shape the adapters have been
observed to emit and a few they have not: a service that answers ``null``, a
limit that arrives as a string, a category map that is a list. Each of those
has at some point reached a predicate that assumed otherwise.
"""

from datetime import UTC, datetime
from typing import Any

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

#: The malformed scalar shapes the strategies must keep producing, named once
#: so a strategy and the test that vouches for it cannot drift apart. ``None``
#: is a real answer (Sonarr clears a seed goal that way); the rest are reads
#: that must not be coerced into a number or a boolean.
#:
#: This tuple exists because the first version of this module did not have it.
#: The control that claimed to guard against "a strategy that silently degraded
#: to generating only well-formed facts" hand-built a single value of its own,
#: so narrowing ``JUNK_VALUES`` to ``st.booleans()`` left all eighteen property
#: tests green — the exact aggregate-over-nothing failure the properties open by
#: naming. ``test_the_strategies_still_generate_every_shape_they_claim`` reads
#: this tuple, so narrowing it now fails there instead of passing quietly.
MALFORMED_SCALARS: tuple[Any, ...] = (None, True, "5", [1], {"a": 1})

#: Values a service can put where a scalar was expected.
JUNK_VALUES = st.one_of(
    st.sampled_from(MALFORMED_SCALARS),
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

#: Category-map shapes that are not a map of categories. Named for the same
#: reason as ``MALFORMED_SCALARS``: narrowing ``CATEGORY_MAPS`` to dicts alone
#: also left every property green.
#:
#: ``None`` is the odd one out and deliberately kept beside the others: a null
#: category map is *information* — a client with no categories, so none of them
#: can carry a share limit of its own — while a list or a string means the read
#: did not give us categories at all. One tuple, two answers, and the control
#: asserts both.
MALFORMED_CATEGORY_MAPS: tuple[Any, ...] = (None, [{"ratio_limit": -2}], "categories")

CATEGORY_MAPS = st.one_of(
    st.dictionaries(st.text(min_size=1, max_size=4), CATEGORY, max_size=3),
    st.sampled_from(MALFORMED_CATEGORY_MAPS),
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
    # Weighted, not uniform. Measured over 3000 draws of an earlier uniform
    # version: ``_is_a_torrent_source`` answered None 88% of the time and True
    # 1%, and an indexer that was both a torrent source and goal-less turned up
    # in 0.67% of draws — so three runs in five generated no wedging indexer at
    # all and the generative search had essentially no power over the flagship
    # predicate. Nothing went falsely green (every property has a hand-built
    # control), but the search was not searching the interesting half.
    protocol=st.one_of(
        known(st.just("torrent")),
        known(st.just("torrent")),
        known(st.just("torrent")),
        facts(st.one_of(st.sampled_from(["usenet", "Torrent"]), JUNK_VALUES)),
    ),
    # Weighted toward "on" for the same reason: an indexer whose every toggle
    # is off or unreadable cannot put a torrent in the queue, so it never
    # reaches the half of the predicate worth searching.
    enable_rss=st.one_of(known(st.just(True)), BOOL_FACTS),
    enable_automatic_search=BOOL_FACTS,
    enable_interactive_search=BOOL_FACTS,
    # Weighted toward "no goal set", which is how Sonarr reports an unset one
    # and is the state the flagship premise is about.
    seed_ratio=st.one_of(
        UNKNOWN_FACTS,
        facts(st.one_of(st.floats(allow_nan=False, width=32), JUNK_VALUES)),
    ),
    seed_time=st.one_of(UNKNOWN_FACTS, LIMIT_FACTS),
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
