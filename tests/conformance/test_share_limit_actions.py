"""Nightly pins for the share-limit actions `orphan-inevitability` reasons from.

The check's whole claim is "when a seed limit fires, does anything remove the
files?", so every action's effect on the torrent *and* on its files is a load-
bearing axiom. Before this file, `Remove` keeping its files and the category
strings were assumed from the qBittorrent source; now they are measured. The
table is `firing.FATES` and the run that produced it is
docs/measurements/2026-11-21-qbt-share-limit-actions.md.

Two clients: the session container for the global `max_ratio_act`, and a
module-scoped one whose categories carry an action before it starts (the
WebAPI cannot set one; see `tests/conformance/categories.py`).
"""

from collections.abc import Iterator

import pytest

from lintarr.clients.qbittorrent.share_limits import ACTION_BY_NAME, DEFAULT_ACTION
from tests.conformance.categories import qbt_with_categories
from tests.conformance.firing import (
    FATE_BY_NAME,
    FATES,
    Fate,
    Observed,
    arm_ratio_zero,
    disarm,
    watch_until_fired,
)
from tests.conformance.qbt import QbtHarness
from tests.conformance.torrents import make_torrent
from tests.conformance.watching import SHARE_LIMIT_TIMEOUT, add_settled

pytestmark = pytest.mark.container

_IDS = [fate.name for fate in FATES]

#: One category per action, plus `Default`, which hands the decision back to the
#: global. The names are the arr-ish names a reader will recognise, not the
#: actions, so a test that asserts on the action cannot be read off the name.
CATEGORIES = {
    "tv": "Stop",
    "movies": "Remove",
    "music": "EnableSuperSeeding",
    "books": "RemoveWithContent",
    "comics": DEFAULT_ACTION,
}
CATEGORY_FOR = {action: name for name, action in CATEGORIES.items()}
DEFAULT_CATEGORY = CATEGORY_FOR[DEFAULT_ACTION]


# --- the global action ---------------------------------------------------------


@pytest.mark.parametrize("fate", FATES, ids=_IDS)
def test_the_global_action_decides_what_a_fired_limit_does(qbt: QbtHarness, fate: Fate) -> None:
    """Each `max_ratio_act` value, against a complete torrent at ratio 0."""
    disarm(qbt, fate.action)
    t = make_torrent(f"share-global-{fate.action}")
    add_settled(qbt, t, complete=True, want="stalledUP")
    arm_ratio_zero(qbt, fate.action)

    observed = watch_until_fired(qbt, {"t": t}, SHARE_LIMIT_TIMEOUT)

    assert observed["t"] == fate.observed


def test_the_global_action_never_reports_default(qbt: QbtHarness) -> None:
    """`Default` (-1) is not observable in `max_ratio_act`: qBittorrent coerces the
    global to Stop as it stores it (`sessionimpl.cpp:554-555`, release 5.2.3).
    Set deliberately, not verified through `set_share_limit_prefs`, because the
    read-back is the measurement."""
    disarm(qbt, 2)
    assert qbt.preferences()["max_ratio_act"] == 2  # the write takes, so -1 below is not a no-op

    qbt.set_prefs({"max_ratio_act": -1})

    assert qbt.preferences()["max_ratio_act"] == FATE_BY_NAME["Stop"].action


# --- the category action -------------------------------------------------------


@pytest.fixture(scope="module")
def categorised(tmp_path_factory: pytest.TempPathFactory) -> Iterator[QbtHarness]:
    """A client whose categories already carry their share-limit actions."""
    with qbt_with_categories(tmp_path_factory.mktemp("categorised"), CATEGORIES) as harness:
        yield harness


def test_each_action_string_reads_back_from_the_categories_endpoint(
    categorised: QbtHarness,
) -> None:
    """The strings `share_limits.ACTION_BY_NAME` maps are the strings the endpoint
    reports, and `Default` is one of them: that is why an unmapped `Default`
    means "ask the global" rather than "unknown action"."""
    reported = {name: body["share_limit_action"] for name, body in categorised.categories().items()}

    assert reported == CATEGORIES
    assert set(CATEGORIES.values()) == set(ACTION_BY_NAME) | {DEFAULT_ACTION}


def _fates_under_global(categorised: QbtHarness, action: int) -> dict[str, Observed]:
    """Seed one torrent per category, then fire one global limit over the lot."""
    disarm(categorised, action)
    torrents = {name: make_torrent(f"share-cat-{name}-{action}") for name in CATEGORIES}
    for name, t in torrents.items():
        add_settled(categorised, t, complete=True, want="stalledUP", category=name)
    arm_ratio_zero(categorised, action)
    return watch_until_fired(categorised, torrents, SHARE_LIMIT_TIMEOUT)


@pytest.fixture(scope="module")
def under_remove_with_content(categorised: QbtHarness) -> dict[str, Observed]:
    """Every category's fate with the global action set to RemoveWithContent (3)."""
    return _fates_under_global(categorised, FATE_BY_NAME["RemoveWithContent"].action)


@pytest.fixture(scope="module")
def under_stop(
    under_remove_with_content: dict[str, Observed], categorised: QbtHarness
) -> dict[str, Observed]:
    """The same, with the global action set to Stop (0). Ordered after the first
    batch by depending on it, so the two never share a client."""
    return _fates_under_global(categorised, FATE_BY_NAME["Stop"].action)


@pytest.mark.parametrize("fate", FATES, ids=_IDS)
def test_a_categorys_action_overrides_the_global(
    under_remove_with_content: dict[str, Observed], fate: Fate
) -> None:
    """With the global at RemoveWithContent, every named category still gets its
    own fate; only `RemoveWithContent`'s own category agrees with the global."""
    assert under_remove_with_content[CATEGORY_FOR[fate.name]] == fate.observed


@pytest.mark.parametrize("fate", FATES, ids=_IDS)
def test_a_categorys_action_overrides_a_stop_global(
    under_stop: dict[str, Observed], fate: Fate
) -> None:
    """The same table under the opposite global, so a category's fate cannot be
    the global's fate in disguise."""
    assert under_stop[CATEGORY_FOR[fate.name]] == fate.observed


@pytest.mark.parametrize("global_action", ["RemoveWithContent", "Stop"])
def test_the_default_category_action_follows_the_global(
    under_remove_with_content: dict[str, Observed],
    under_stop: dict[str, Observed],
    global_action: str,
) -> None:
    """`Default` is not an action: the torrent gets whichever fate the global
    names, which is why `effective_action` reads `max_ratio_act` for it."""
    batch = {"RemoveWithContent": under_remove_with_content, "Stop": under_stop}[global_action]

    assert batch[DEFAULT_CATEGORY] == FATE_BY_NAME[global_action].observed
