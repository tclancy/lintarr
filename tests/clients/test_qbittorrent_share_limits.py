"""Per category: the share-limit action, and whether a seed limit can fire.

The arr only ever recognises an indexer goal or an inherited global limit
(Sonarr's ``HasReachedSeedLimit`` reads the torrent's own limit, else the
global prefs when it is ``-2``, never a category's). A limit reached through a
category's own value is the one it does not recognise, unless the same-arm
global is reachable at or below that value.
"""

from datetime import UTC, datetime

import pytest

from lintarr.clients.qbittorrent.share_limits import (
    ACTION_BY_NAME,
    can_fire,
    can_fire_unrecognised,
    effective_action,
)
from lintarr.facts import Known, Unknown
from lintarr.models import IndexerFacts
from tests.fixtures.homelab import qbt_with
from tests.fixtures.homelab_paths import QBT_CATEGORIES, arr_with, homelab_arr, homelab_qbt

GLOBALS_OFF = {
    "max_ratio_enabled": False,
    "max_seeding_time_enabled": False,
    "max_inactive_seeding_time_enabled": False,
}


def _fact(value):
    return Known(value=value, source="GET /x", read_at=datetime.now(UTC), service_version="v1")


def _absent(name):
    return Unknown("field-absent", name)


def _indexer(*, protocol="torrent", seed_ratio=None, seed_time=None, season_pack=None):
    """One enabled indexer; a criterion left as ``None`` is absent, as Sonarr reports unset."""
    return IndexerFacts(
        name="1337x",
        protocol=_fact(protocol),
        enable_rss=_fact(True),
        enable_automatic_search=_fact(True),
        enable_interactive_search=_fact(True),
        seed_ratio=_absent("seed_ratio") if seed_ratio is None else _fact(seed_ratio),
        seed_time=_absent("seed_time") if seed_time is None else _fact(seed_time),
        season_pack_seed_time=(
            _absent("season_pack_seed_time") if season_pack is None else _fact(season_pack)
        ),
    )


def _arr(*indexers, kind="sonarr"):
    return arr_with(homelab_arr(kind=kind), indexers=indexers)


def _qbt(tv=None, **prefs):
    """The homelab qBittorrent with category ``tv`` and preferences overridden."""
    categories = QBT_CATEGORIES | {"tv": QBT_CATEGORIES["tv"] | (tv or {})}
    return qbt_with("main", categories=categories, **prefs)


def _without_key(key):
    return {
        name: {k: v for k, v in body.items() if k != key} for name, body in QBT_CATEGORIES.items()
    }


# --- effective_action -------------------------------------------------------


def test_default_action_is_the_global():
    assert effective_action(homelab_qbt(), "tv") == 3


@pytest.mark.parametrize(("name", "value"), sorted(ACTION_BY_NAME.items()))
def test_a_named_category_action_overrides_the_global(name, value):
    assert effective_action(_qbt(tv={"share_limit_action": name}), "tv") == value


def test_action_names_are_the_measured_enum():
    assert ACTION_BY_NAME == {
        "Stop": 0,
        "Remove": 1,
        "EnableSuperSeeding": 2,
        "RemoveWithContent": 3,
    }


@pytest.mark.parametrize("category", ["", "anime"])
def test_no_category_body_falls_back_to_the_global(category):
    qbt = _qbt(tv={"share_limit_action": "Stop"}, max_ratio_act=1)
    assert effective_action(qbt, category) == 1


def test_a_missing_action_key_falls_back_to_the_global():
    qbt = qbt_with("main", categories=_without_key("share_limit_action"), max_ratio_act=0)
    assert effective_action(qbt, "tv") == 0


@pytest.mark.parametrize("value", ["Pause", 1, None])
def test_an_unknown_or_mistyped_action_is_unknown(value):
    assert effective_action(_qbt(tv={"share_limit_action": value}), "tv") is None


def test_unreadable_categories_make_the_action_unknown():
    qbt = qbt_with("main", categories=Unknown("insufficient-permission", "categories"))
    assert effective_action(qbt, "tv") is None


def test_no_category_never_needs_the_category_map():
    qbt = qbt_with("main", categories=Unknown("insufficient-permission", "categories"))
    assert effective_action(qbt, "") == 3


def test_an_unreadable_global_action_is_unknown_only_when_used():
    qbt = _qbt(max_ratio_act=Unknown("insufficient-permission", "max_ratio_act"))
    assert effective_action(qbt, "tv") is None
    assert effective_action(_qbt(tv={"share_limit_action": "Stop"}, max_ratio_act="x"), "tv") == 0


# --- can_fire ---------------------------------------------------------------


def test_homelab_can_fire_through_the_global():
    assert can_fire(homelab_qbt(), _arr(_indexer()), "tv") is True


def test_unlimited_seeding_cannot_fire():
    assert can_fire(_qbt(**GLOBALS_OFF), _arr(_indexer()), "tv") is False


@pytest.mark.parametrize("criterion", ["seed_ratio", "seed_time"])
def test_an_indexer_goal_fires_with_globals_off(criterion):
    assert can_fire(_qbt(**GLOBALS_OFF), _arr(_indexer(**{criterion: 0})), "tv") is True


def test_an_override_blocks_only_its_own_arm():
    ratio_only = GLOBALS_OFF | {"max_ratio_enabled": True}
    assert can_fire(_qbt(**ratio_only), _arr(_indexer(seed_ratio=-1)), "tv") is False
    assert can_fire(homelab_qbt(), _arr(_indexer(seed_ratio=-1)), "tv") is True


def test_an_infinite_criterion_overrides():
    ratio_only = GLOBALS_OFF | {"max_ratio_enabled": True}
    assert can_fire(_qbt(**ratio_only), _arr(_indexer(seed_ratio=float("inf"))), "tv") is False


def test_a_minus_two_criterion_defers_to_the_global():
    ratio_only = GLOBALS_OFF | {"max_ratio_enabled": True}
    assert can_fire(_qbt(**ratio_only), _arr(_indexer(seed_ratio=-2)), "tv") is True


@pytest.mark.parametrize(
    "key", ["ratio_limit", "seeding_time_limit", "inactive_seeding_time_limit"]
)
def test_a_categorys_own_limit_fires(key):
    assert can_fire(_qbt(tv={key: 2}, **GLOBALS_OFF), _arr(_indexer()), "tv") is True


def test_category_unlimited_blocks_the_global():
    ratio_only = GLOBALS_OFF | {"max_ratio_enabled": True}
    assert can_fire(_qbt(tv={"ratio_limit": -1}, **ratio_only), _arr(_indexer()), "tv") is False


def test_an_unreachable_category_limit_blocks_the_global():
    ratio_only = GLOBALS_OFF | {"max_ratio_enabled": True}
    qbt = _qbt(tv={"ratio_limit": float("inf")}, **ratio_only)
    assert can_fire(qbt, _arr(_indexer()), "tv") is False


def test_an_indexer_goal_wins_over_a_blocking_category():
    qbt = _qbt(tv={"ratio_limit": -1}, **GLOBALS_OFF)
    assert can_fire(qbt, _arr(_indexer(seed_ratio=1)), "tv") is True


def test_the_inactive_gate_fires():
    inactive = GLOBALS_OFF | {"max_inactive_seeding_time_enabled": True}
    assert can_fire(_qbt(**inactive), _arr(_indexer()), "tv") is True


def test_an_absent_inactive_pref_is_not_armed():
    prefs = GLOBALS_OFF | {"max_inactive_seeding_time_enabled": _absent("inactive")}
    assert can_fire(_qbt(**prefs), _arr(_indexer()), "tv") is False


def test_a_usenet_only_arr_cannot_fire():
    assert can_fire(homelab_qbt(), _arr(_indexer(protocol="usenet")), "tv") is False


def test_an_arr_with_no_indexers_cannot_fire():
    assert can_fire(homelab_qbt(), _arr(), "tv") is False


def test_an_unusable_criterion_that_decides_is_unknown():
    assert can_fire(_qbt(**GLOBALS_OFF), _arr(_indexer(seed_ratio="2.0")), "tv") is None


def test_an_unusable_criterion_that_does_not_decide_is_ignored():
    seed_time = GLOBALS_OFF | {"max_seeding_time_enabled": True}
    assert can_fire(_qbt(**seed_time), _arr(_indexer(seed_ratio="2.0")), "tv") is True


def test_an_unreadable_category_limit_that_decides_is_unknown():
    assert can_fire(_qbt(tv={"ratio_limit": "2"}, **GLOBALS_OFF), _arr(_indexer()), "tv") is None


def test_unreadable_categories_make_reachability_unknown_unless_settled():
    unread = Unknown("insufficient-permission", "categories")
    off = qbt_with("main", categories=unread, **GLOBALS_OFF)
    assert can_fire(off, _arr(_indexer()), "tv") is None
    assert can_fire(off, _arr(_indexer(seed_ratio=1)), "tv") is True
    assert can_fire(off, _arr(_indexer()), "") is False


def test_an_unreadable_global_that_decides_is_unknown():
    prefs = GLOBALS_OFF | {"max_ratio_enabled": Unknown("insufficient-permission", "x")}
    assert can_fire(_qbt(**prefs), _arr(_indexer()), "tv") is None


def test_a_sonarr_season_pack_goal_fires():
    assert can_fire(_qbt(**GLOBALS_OFF), _arr(_indexer(season_pack=60)), "tv") is True


def test_a_season_pack_goal_means_nothing_to_radarr():
    arr = _arr(_indexer(season_pack=60), kind="radarr")
    assert can_fire(_qbt(**GLOBALS_OFF), arr, "tv") is False


def test_one_firing_indexer_is_enough():
    arr = _arr(_indexer(seed_ratio="junk"), _indexer(seed_ratio=1))
    assert can_fire(_qbt(**GLOBALS_OFF), arr, "tv") is True


# --- can_fire_unrecognised --------------------------------------------------


def test_homelab_fires_only_through_a_recognised_global():
    assert can_fire_unrecognised(homelab_qbt(), _arr(_indexer()), "tv") is False


@pytest.mark.parametrize(
    "key", ["ratio_limit", "seeding_time_limit", "inactive_seeding_time_limit"]
)
def test_a_categorys_own_limit_is_unrecognised(key):
    qbt = _qbt(tv={key: 2}, **GLOBALS_OFF)
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is True


def test_an_indexer_goal_is_recognised():
    assert can_fire_unrecognised(_qbt(**GLOBALS_OFF), _arr(_indexer(seed_ratio=2)), "tv") is False


def test_an_indexer_goal_shadows_the_categorys_own_limit():
    qbt = _qbt(tv={"ratio_limit": 2}, **GLOBALS_OFF)
    assert can_fire_unrecognised(qbt, _arr(_indexer(seed_ratio=2)), "tv") is False


def test_an_inherited_reachable_global_is_recognised():
    qbt = _qbt(tv={"ratio_limit": -2}, **GLOBALS_OFF | {"max_ratio_enabled": True})
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is False


def test_an_unreadable_category_limit_leaves_unrecognised_unknown():
    qbt = _qbt(tv={"ratio_limit": None}, **GLOBALS_OFF)
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is None


def test_an_arr_with_no_indexers_has_no_unrecognised_limit():
    assert can_fire_unrecognised(_qbt(tv={"ratio_limit": 2}), _arr(), "tv") is False


# --- season packs are their own seed-time population ------------------------

_BLOCKING = {"seed_ratio": -1, "seed_time": -1}


def test_a_junk_season_pack_with_blocking_criteria_is_unknown():
    arr = _arr(_indexer(season_pack="60", **_BLOCKING))
    assert can_fire(_qbt(**GLOBALS_OFF, max_ratio_act=1), arr, "tv") is None


def test_a_deferring_season_pack_reaches_the_global_seed_time():
    seed_time = GLOBALS_OFF | {"max_seeding_time_enabled": True}
    arr = _arr(_indexer(season_pack=-2, **_BLOCKING))
    assert can_fire(_qbt(**seed_time), arr, "tv") is True


def test_a_deferring_season_pack_reaches_the_categorys_own_seed_time():
    qbt = _qbt(tv={"seeding_time_limit": 60}, **GLOBALS_OFF)
    arr = _arr(_indexer(season_pack=-2, **_BLOCKING))
    assert can_fire_unrecognised(qbt, arr, "tv") is True


def test_a_blocking_season_pack_does_not_block_single_episodes():
    seed_time = GLOBALS_OFF | {"max_seeding_time_enabled": True}
    arr = _arr(_indexer(season_pack=-1))
    assert can_fire(_qbt(**seed_time), arr, "tv") is True


@pytest.mark.parametrize("season_pack", ["60", -2])
def test_radarr_ignores_the_season_pack(season_pack):
    seed_time = GLOBALS_OFF | {"max_seeding_time_enabled": True}
    arr = _arr(_indexer(season_pack=season_pack, **_BLOCKING), kind="radarr")
    assert can_fire(_qbt(**seed_time), arr, "tv") is False


def test_an_absent_season_pack_defers_rather_than_using_seed_time():
    """Sonarr v4 gives a season pack SeasonPackSeedTime only, never SeedTime."""
    seed_time = GLOBALS_OFF | {"max_seeding_time_enabled": True}
    arr = _arr(_indexer(**_BLOCKING))
    assert can_fire(_qbt(**seed_time), arr, "tv") is True


def test_an_absent_season_pack_reaches_the_categorys_own_seed_time():
    qbt = _qbt(tv={"seeding_time_limit": 60}, **GLOBALS_OFF)
    assert can_fire_unrecognised(qbt, _arr(_indexer(**_BLOCKING)), "tv") is True


def test_a_radarr_blocking_seed_time_has_no_season_pack_escape():
    seed_time = GLOBALS_OFF | {"max_seeding_time_enabled": True}
    arr = _arr(_indexer(**_BLOCKING), kind="radarr")
    assert can_fire(_qbt(**seed_time), arr, "tv") is False


# --- a category's own limit the same-arm global already covers --------------
#
# Sonarr reads the global pref for an arm when the torrent's own limit is -2,
# whatever the category. A torrent qBittorrent stops at the category's value
# has exactly that ratio or seeding time and stops accruing, so the arr
# recognises it when the same-arm global is reachable at or below that value.

_RATIO_ONLY = GLOBALS_OFF | {"max_ratio_enabled": True}
_SEED_TIME_ONLY = GLOBALS_OFF | {"max_seeding_time_enabled": True}


@pytest.mark.parametrize("global_ratio", [1.0, 2.0])
def test_a_category_ratio_the_global_covers_is_recognised(global_ratio):
    qbt = _qbt(tv={"ratio_limit": 2.0}, **_RATIO_ONLY | {"max_ratio": global_ratio})
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is False
    assert can_fire(qbt, _arr(_indexer()), "tv") is True


def test_a_category_ratio_below_the_global_is_unrecognised():
    qbt = _qbt(tv={"ratio_limit": 2.0}, **_RATIO_ONLY | {"max_ratio": 3.0})
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is True


def test_an_unreachable_global_ratio_covers_nothing():
    qbt = _qbt(tv={"ratio_limit": 2.0}, **_RATIO_ONLY | {"max_ratio": float("inf")})
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is True


def test_an_unreadable_global_ratio_value_leaves_recognition_unknown():
    unreadable = Unknown("insufficient-permission", "max_ratio")
    qbt = _qbt(tv={"ratio_limit": 2.0}, **_RATIO_ONLY | {"max_ratio": unreadable})
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is None
    # Whether a limit fires at all is still settled: the category's own one does.
    assert can_fire(qbt, _arr(_indexer()), "tv") is True


def test_an_unreadable_global_ratio_flag_leaves_recognition_unknown():
    unreadable = Unknown("insufficient-permission", "max_ratio_enabled")
    qbt = _qbt(tv={"ratio_limit": 2.0}, **GLOBALS_OFF | {"max_ratio_enabled": unreadable})
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is None


def test_a_category_seed_time_the_global_covers_is_recognised():
    qbt = _qbt(tv={"seeding_time_limit": 20160}, **_SEED_TIME_ONLY | {"max_seeding_time": 10080})
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is False


def test_a_category_seed_time_with_the_global_off_is_unrecognised():
    qbt = _qbt(tv={"seeding_time_limit": 20160}, **GLOBALS_OFF | {"max_seeding_time": 10080})
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is True


def test_a_category_seed_time_below_the_global_is_unrecognised():
    qbt = _qbt(tv={"seeding_time_limit": 10080}, **_SEED_TIME_ONLY | {"max_seeding_time": 20160})
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is True


def test_an_unreadable_global_seed_time_value_leaves_recognition_unknown():
    unreadable = Unknown("insufficient-permission", "max_seeding_time")
    qbt = _qbt(
        tv={"seeding_time_limit": 20160}, **_SEED_TIME_ONLY | {"max_seeding_time": unreadable}
    )
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is None


def test_a_season_pack_category_seed_time_the_global_covers_is_recognised():
    qbt = _qbt(tv={"seeding_time_limit": 60}, **_SEED_TIME_ONLY | {"max_seeding_time": 30})
    arr = _arr(_indexer(season_pack=-2, **_BLOCKING))
    assert can_fire_unrecognised(qbt, arr, "tv") is False
    assert can_fire(qbt, arr, "tv") is True


# The inactive arm reads the same coverage rule as the other two (lintarr#59).
# `HasReachedInactiveSeedingTimeLimit` has the same shape as its siblings: the
# torrent's own limit when it is >= 0, else `config.MaxInactiveSeedingTime` when
# the torrent's limit is -2.

_INACTIVE_ARMED = GLOBALS_OFF | {"max_inactive_seeding_time_enabled": True}


@pytest.mark.parametrize("global_inactive", [30, 60])
def test_a_category_inactive_limit_the_global_covers_is_recognised(global_inactive):
    armed = _INACTIVE_ARMED | {"max_inactive_seeding_time": global_inactive}
    qbt = _qbt(tv={"inactive_seeding_time_limit": 60}, **armed)
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is False
    assert can_fire(qbt, _arr(_indexer()), "tv") is True


def test_a_category_inactive_limit_below_the_global_is_unrecognised():
    armed = _INACTIVE_ARMED | {"max_inactive_seeding_time": 1440}
    qbt = _qbt(tv={"inactive_seeding_time_limit": 60}, **armed)
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is True


def test_a_category_inactive_limit_with_the_global_off_is_unrecognised():
    prefs = GLOBALS_OFF | {"max_inactive_seeding_time": 30}
    qbt = _qbt(tv={"inactive_seeding_time_limit": 60}, **prefs)
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is True


def test_an_unreadable_global_inactive_value_leaves_recognition_unknown():
    unreadable = Unknown("insufficient-permission", "max_inactive_seeding_time")
    armed = _INACTIVE_ARMED | {"max_inactive_seeding_time": unreadable}
    qbt = _qbt(tv={"inactive_seeding_time_limit": 60}, **armed)
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is None
    # Whether a limit fires at all is still settled: the category's own one does.
    assert can_fire(qbt, _arr(_indexer()), "tv") is True


def test_an_unreadable_global_inactive_flag_leaves_recognition_unknown():
    unreadable = Unknown("insufficient-permission", "max_inactive_seeding_time_enabled")
    prefs = GLOBALS_OFF | {"max_inactive_seeding_time_enabled": unreadable}
    qbt = _qbt(tv={"inactive_seeding_time_limit": 60}, **prefs)
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is None


def test_an_absent_global_inactive_value_beside_an_armed_flag_is_unknown():
    """A client that reports the flag reports the value, so absence here is not information."""
    armed = _INACTIVE_ARMED | {"max_inactive_seeding_time": _absent("max_inactive_seeding_time")}
    qbt = _qbt(tv={"inactive_seeding_time_limit": 60}, **armed)
    assert can_fire_unrecognised(qbt, _arr(_indexer()), "tv") is None
