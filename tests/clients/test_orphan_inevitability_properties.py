"""Properties of ``orphan-inevitability`` over generated clients and arrs.

The oracle for "never PASS on an unknown action" re-derives the settled
category (spec step 0) by hand and asks ``share_limits.effective_action``,
which has its own example tests, for the action. Each property quantifies over
one outcome, so a hand-built control below proves that outcome is reachable,
and one proves the strategy reaches the category actions the check reads.
"""

from collections import Counter
from dataclasses import replace

from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from lintarr.checks.arr_clients import is_well_formed
from lintarr.clients.qbittorrent.orphan_inevitability import (
    INVARIANT_ID,
    NOTHING_RECLAIMS,
    PREMISE_FILES_STAY,
    PREMISE_NOTHING_RECLAIMS,
    REMOVE_KEEPS_FILES,
    check,
)
from lintarr.clients.qbittorrent.share_limits import ACTION_BY_NAME, effective_action
from lintarr.facts import is_known
from lintarr.models import ArrDownloadClient, ArrInstance, ClientInstance
from lintarr.outcomes import Outcome, Premise
from tests.fixtures.clients import as_client
from tests.fixtures.homelab import qbt_with
from tests.fixtures.homelab_paths import QBT_CATEGORIES, arr_with, homelab_arr
from tests.strategies import (
    ARR_DOWNLOAD_CLIENTS,
    ARR_INSTANCES,
    CATEGORY,
    INDEXERS,
    MATCHABLE_CATEGORY_NAMES,
    known,
    qbt_instances,
)

#: The shared strategies alone almost never decide a pair: a review sampled
#: 400 draws and got 218 N/A, 133 SKIP, 48 PASS, 1 nothing-reclaims FAIL, no
#: remove-keeps-files FAIL and one matched category with a named action,
#: because a decided pair needs a readable download-client list, a category
#: map holding that client's category, a readable action and a torrent
#: indexer, all at once. ``_LINED_UP`` builds exactly that, with every other
#: field still drawn from the shared strategies, and ``STACKS`` mixes it with
#: the shared draws so every shape they produce is still searched.
_NAMED_ACTIONS = st.sampled_from(sorted(ACTION_BY_NAME))


@st.composite
def _lined_up(draw: st.DrawFn) -> tuple[ClientInstance, tuple[ArrInstance, ...]]:
    category = draw(st.sampled_from(MATCHABLE_CATEGORY_NAMES))
    # A readable ratio limit (inherit, unlimited, or reachable) so the pair is
    # usually decidable; junk still reaches the other limits through CATEGORY.
    body = draw(CATEGORY) | {
        "share_limit_action": draw(_NAMED_ACTIONS),
        "ratio_limit": draw(st.sampled_from([-2, -1, 0, 1.5])),
    }
    others = draw(st.dictionaries(st.text(min_size=1, max_size=4), CATEGORY, max_size=1))
    qbt = replace(draw(qbt_instances()), categories=draw(known(st.just(others | {category: body}))))
    d = replace(
        draw(ARR_DOWNLOAD_CLIENTS),
        name="qBittorrent",
        implementation="QBittorrent",
        enabled=True,
        category=category,
    )
    torrent = replace(
        draw(INDEXERS),
        protocol=draw(known(st.just("torrent"))),
        enable_rss=draw(known(st.just(True))),
    )
    arr = replace(
        draw(ARR_INSTANCES),
        indexers=(torrent, *draw(st.lists(INDEXERS, max_size=1))),
        download_clients=draw(known(st.just((d,)))),
    )
    # A second arr with a readable client list, so it adds a pair rather than
    # (as the shared arrs mostly do) an unreadable list that only SKIPs.
    readable = known(st.lists(ARR_DOWNLOAD_CLIENTS, max_size=2).map(tuple))
    rest = draw(st.lists(st.builds(replace, ARR_INSTANCES, download_clients=readable), max_size=1))
    return as_client(qbt), (arr, *rest)


STACKS = st.one_of(
    st.tuples(
        qbt_instances().map(as_client), st.lists(ARR_INSTANCES, min_size=1, max_size=3).map(tuple)
    ),
    _lined_up(),
    _lined_up(),
)

_PREMISE_OF = {REMOVE_KEEPS_FILES: PREMISE_FILES_STAY, NOTHING_RECLAIMS: PREMISE_NOTHING_RECLAIMS}

_SETTINGS = settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def _matched(client: ClientInstance, arr: ArrInstance) -> list[ArrDownloadClient]:
    """The arr's download clients that could be *client*, when they can be told at all."""
    clients = arr.download_clients
    if not is_known(clients) or not isinstance(clients.value, tuple):
        return []
    save_paths = client.common.save_paths
    paths = save_paths.value if is_known(save_paths) else None
    return [
        d
        for d in clients.value
        if isinstance(d, ArrDownloadClient)
        and is_well_formed(d)
        and d.enabled
        and d.implementation.lower() == client.kind
        and (not isinstance(paths, dict) or d.category in paths)
    ]


def _settled_category(d: ArrDownloadClient) -> str:
    imported = d.imported_category
    if isinstance(imported, str) and imported and imported != d.category:
        return imported
    return d.category


def _some_action_is_unknown(client: ClientInstance, arrs: tuple[ArrInstance, ...]) -> bool:
    return any(
        effective_action(client.specific, _settled_category(d)) is None
        for arr in arrs
        for d in _matched(client, arr)
    )


def _some_category_action_is_named(client: ClientInstance, arrs: tuple[ArrInstance, ...]) -> bool:
    categories = client.specific.categories
    if not is_known(categories) or not isinstance(categories.value, dict):
        return False
    return any(
        isinstance(body := categories.value.get(_settled_category(d)), dict)
        and isinstance(action := body.get("share_limit_action"), str)
        and action in ACTION_BY_NAME
        for arr in arrs
        for d in _matched(client, arr)
    )


@_SETTINGS
@given(STACKS)
def test_the_check_never_raises_and_keeps_its_identity(stack):
    client, arrs = stack
    finding = check(client, arrs)
    assert finding.invariant == INVARIANT_ID
    assert finding.conflict in _PREMISE_OF
    assert finding.instance == f"qbittorrent[{client.name}]"


@_SETTINGS
@given(STACKS)
def test_every_fail_carries_exactly_its_one_premise_holding(stack):
    client, arrs = stack
    finding = check(client, arrs)
    if finding.outcome is Outcome.FAIL:
        assert finding.premises == (Premise(_PREMISE_OF[finding.conflict], True),)
        assert finding.detail


@_SETTINGS
@given(STACKS)
def test_never_pass_while_a_matched_action_is_unknown(stack):
    client, arrs = stack
    if check(client, arrs).outcome is Outcome.PASS:
        assert not _some_action_is_unknown(client, arrs)


# --- controls -----------------------------------------------------------------


def _draws_under_the_property_settings() -> list[tuple[ClientInstance, tuple[ArrInstance, ...]]]:
    """Exactly the draws a property sees: its strategy, its example count, a fixed seed."""
    seen: list[tuple[ClientInstance, tuple[ArrInstance, ...]]] = []

    @settings(_SETTINGS, derandomize=True, database=None, phases=[Phase.generate])
    @given(STACKS)
    def collect(stack):
        seen.append(stack)

    collect()
    return seen


def test_control_the_properties_reach_fail_of_both_conflicts_and_named_actions():
    """Each property quantifies over one outcome; this proves the outcome is drawn.

    At least 10% of draws FAIL, both conflicts occur, PASS and SKIP occur, and
    a matched category with a named (non-Default) action is common.
    """
    draws = _draws_under_the_property_settings()
    findings = [check(client, arrs) for client, arrs in draws]
    fails = [f for f in findings if f.outcome is Outcome.FAIL]
    assert len(draws) >= 300
    assert len(fails) >= len(draws) // 10, Counter(f.outcome for f in findings)
    assert {f.conflict for f in fails} == set(_PREMISE_OF)
    assert {Outcome.PASS, Outcome.SKIP} <= {f.outcome for f in findings}
    named = sum(_some_category_action_is_named(client, arrs) for client, arrs in draws)
    assert named >= len(draws) // 10


def test_control_oracle_rejects_pass_over_an_unknown_category_action():
    categories = QBT_CATEGORIES | {"tv": QBT_CATEGORIES["tv"] | {"share_limit_action": "Pause"}}
    client = as_client(qbt_with("main", categories=categories))
    arrs = (homelab_arr(),)
    assert _some_action_is_unknown(client, arrs)
    assert check(client, arrs).outcome is Outcome.SKIP


def test_control_oracle_follows_the_imported_category():
    categories = QBT_CATEGORIES | {"tv-done": {"share_limit_action": "Pause"}}
    client = as_client(qbt_with("main", categories=categories))
    base = homelab_arr()
    (d,) = base.download_clients.value
    arr = arr_with(base, download_clients=(replace(d, imported_category="tv-done"),))
    assert _some_action_is_unknown(client, (arr,))
    assert not _some_action_is_unknown(client, (base,))
