"""Properties of ``orphan-inevitability`` over generated clients and arrs.

The oracle for "never PASS on an unknown action" re-derives the settled
category (spec step 0) by hand and asks ``share_limits.effective_action``,
which has its own example tests, for the action. Each property quantifies over
one outcome, so a hand-built control below proves that outcome is reachable,
and one proves the strategy reaches the category actions the check reads.
"""

from dataclasses import replace

from hypothesis import HealthCheck, find, given, settings
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
    MATCHABLE_CATEGORY_NAMES,
    known,
    qbt_instances,
)

#: The shared strategies, plus a weighted half that reaches the pairs this check
#: decides. Measured: over the shared strategies alone, 5000 draws found no
#: matched category with a named action and no remove-keeps-files FAIL, because
#: a matched pair needs a readable download-client list *and* a readable
#: category map holding that client's category. The weighted halves keep every
#: shape the shared strategies draw and add those that line up.
_MATCHABLE_CATEGORY_MAPS = known(
    st.dictionaries(st.sampled_from(MATCHABLE_CATEGORY_NAMES), CATEGORY, min_size=1, max_size=2)
)
_READABLE_DOWNLOAD_CLIENTS = known(
    st.lists(ARR_DOWNLOAD_CLIENTS, min_size=1, max_size=2).map(tuple)
)

CLIENTS = st.one_of(
    qbt_instances(),
    st.builds(replace, qbt_instances(), categories=_MATCHABLE_CATEGORY_MAPS),
).map(as_client)
ARRS = st.lists(
    st.one_of(
        ARR_INSTANCES,
        st.builds(replace, ARR_INSTANCES, download_clients=_READABLE_DOWNLOAD_CLIENTS),
    ),
    max_size=3,
).map(tuple)

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
@given(CLIENTS, ARRS)
def test_the_check_never_raises_and_keeps_its_identity(client, arrs):
    finding = check(client, arrs)
    assert finding.invariant == INVARIANT_ID
    assert finding.conflict in _PREMISE_OF
    assert finding.instance == f"qbittorrent[{client.name}]"


@_SETTINGS
@given(CLIENTS, ARRS)
def test_every_fail_carries_exactly_its_one_premise_holding(client, arrs):
    finding = check(client, arrs)
    if finding.outcome is Outcome.FAIL:
        assert finding.premises == (Premise(_PREMISE_OF[finding.conflict], True),)
        assert finding.detail


@_SETTINGS
@given(CLIENTS, ARRS)
def test_never_pass_while_a_matched_action_is_unknown(client, arrs):
    if check(client, arrs).outcome is Outcome.PASS:
        assert not _some_action_is_unknown(client, arrs)


# --- controls -----------------------------------------------------------------


def test_control_strategy_reaches_a_matched_category_whose_action_is_not_default():
    client, arrs = find(
        st.tuples(CLIENTS, ARRS),
        lambda pair: _some_category_action_is_named(*pair),
        settings=settings(max_examples=5000, deadline=None),
    )
    assert _some_category_action_is_named(client, arrs)


def test_control_strategy_reaches_a_pass_and_a_fail_of_each_conflict():
    pairs = st.tuples(CLIENTS, ARRS)
    budget = settings(max_examples=5000, deadline=None)
    passing = find(pairs, lambda p: check(*p).outcome is Outcome.PASS, settings=budget)
    assert check(*passing).outcome is Outcome.PASS
    for conflict in _PREMISE_OF:
        failing = find(
            pairs,
            lambda p, c=conflict: (f := check(*p)).outcome is Outcome.FAIL and f.conflict == c,
            settings=budget,
        )
        assert check(*failing).conflict == conflict


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
