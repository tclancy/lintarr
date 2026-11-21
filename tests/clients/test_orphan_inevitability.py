"""The ``orphan-inevitability`` qBittorrent check, against the spec's steps 0-4.

The arr removes a download only when Remove Completed is on, it can still see
the torrent (no post-import category), and the limit that stopped it is one it
recognises: an indexer goal or an inherited global, and a category's own only
when the same-arm global is reachable at or below it.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from lintarr.checks.arr_clients import NO_ARR_SENDS_HERE
from lintarr.clients.base import ArrDependency
from lintarr.clients.qbittorrent import BACKEND, orphan_inevitability
from lintarr.clients.qbittorrent.orphan_inevitability import (
    INVARIANT_ID,
    NOTHING_RECLAIMS,
    PREMISE_FILES_STAY,
    PREMISE_NOTHING_RECLAIMS,
    REMOVE_KEEPS_FILES,
    check,
)
from lintarr.facts import Known, Unknown
from lintarr.models import ErrorRow, IndexerFacts, StackFacts
from lintarr.outcomes import Outcome, Premise
from lintarr.run import run_checks
from tests.fixtures.clients import as_client
from tests.fixtures.homelab import qbt_with
from tests.fixtures.homelab_paths import QBT_CATEGORIES, arr_with, homelab_arr, homelab_qbt

GLOBALS_OFF = {
    "max_ratio_enabled": False,
    "max_seeding_time_enabled": False,
    "max_inactive_seeding_time_enabled": False,
}

_FILES_STAY = (Premise(PREMISE_FILES_STAY, True),)
_NOTHING = (Premise(PREMISE_NOTHING_RECLAIMS, True),)


def _fact(value):
    return Known(value=value, source="GET /x", read_at=datetime.now(UTC), service_version="v1")


def _absent(name):
    return Unknown("field-absent", name)


def _indexer(*, seed_ratio=None, seed_time=None):
    """One enabled torrent indexer; a criterion left as ``None`` is absent (unset)."""
    return IndexerFacts(
        name="1337x",
        protocol=_fact("torrent"),
        enable_rss=_fact(True),
        enable_automatic_search=_fact(True),
        enable_interactive_search=_fact(True),
        seed_ratio=_absent("seed_ratio") if seed_ratio is None else _fact(seed_ratio),
        seed_time=_absent("seed_time") if seed_time is None else _fact(seed_time),
        season_pack_seed_time=_absent("season_pack_seed_time"),
    )


def _arr(kind="sonarr", name="tv", *, indexers=None, **client):
    """The homelab arr with one explicit torrent indexer and its client's fields overridden."""
    base = homelab_arr(kind, name)
    (d,) = base.download_clients.value
    return arr_with(
        base,
        indexers=(_indexer(),) if indexers is None else indexers,
        download_clients=(replace(d, **client),),
    )


def _qbt(categories=None, **prefs):
    """The homelab qBittorrent with per-category overrides merged in and prefs overridden."""
    merged = {name: dict(body) for name, body in QBT_CATEGORIES.items()}
    for name, body in (categories or {}).items():
        merged[name] = merged.get(name, {"name": name}) | body
    return as_client(qbt_with("main", categories=merged, **prefs))


# --- the homelab and the action -------------------------------------------


def test_homelab_passes_because_qbittorrent_removes_files():
    """Review focus 1: global action 3, ratio and seeding time on, categories Default -2."""
    arrs = (_arr("sonarr", "tv"), _arr("radarr", "movies"))
    assert all(a.download_clients.value[0].remove_completed is False for a in arrs)
    finding = check(as_client(homelab_qbt()), arrs)
    assert finding.outcome is Outcome.PASS
    assert finding.detail == (
        "sonarr[tv]: qBittorrent removes files at the limit; "
        "radarr[movies]: qBittorrent removes files at the limit"
    )
    assert finding.instance == "qbittorrent[main]"


def test_homelab_twin_with_action_one_fails_remove_keeps_files():
    finding = check(_qbt(max_ratio_act=1), (_arr(),))
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == REMOVE_KEEPS_FILES
    assert finding.premises == _FILES_STAY
    assert finding.detail == (
        "sonarr[tv] (category tv): a limit can fire; action is Remove, which keeps files"
    )


def test_stop_without_arr_removal_fails_nothing_reclaims():
    finding = check(_qbt(max_ratio_act=0), (_arr(remove_completed=False),))
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == NOTHING_RECLAIMS
    assert finding.premises == _NOTHING
    assert finding.detail == (
        "sonarr[tv] (category tv): a limit can fire; "
        "action is Stop and the arr's Remove Completed is off"
    )


def test_stop_with_arr_removal_and_global_limits_passes():
    finding = check(_qbt(max_ratio_act=0), (_arr(remove_completed=True),))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == (
        "sonarr[tv]: qBittorrent stops at the limit and the arr removes completed downloads"
    )


def test_stop_with_arr_removal_but_category_own_limit_fails():
    """Review focus 3: the arr never checks a category's limits.

    It checks the global ratio instead, so the global must sit above the
    category's 2.0 for the stopped torrent to be one it never removes. The
    homelab's 1.5 would cover it (see the final-fix-wave tests below).
    """
    qbt = _qbt({"tv": {"ratio_limit": 2.0}}, max_ratio_act=0, max_ratio=3.0)
    finding = check(qbt, (_arr(remove_completed=True),))
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == NOTHING_RECLAIMS
    assert finding.premises == _NOTHING
    assert finding.detail == (
        "sonarr[tv] (category tv): a limit can fire; action is Stop and the limit "
        "that fires is category tv's own, which the arr does not check"
    )


def test_imported_category_means_the_arr_cannot_remove():
    """Review focus 4: action and limits come from the imported category."""
    arr = _arr(remove_completed=True, imported_category="tv-done")
    # The category the arr grabs into removes files; the imported one only stops.
    qbt = _qbt(
        {
            "tv": {"share_limit_action": "RemoveWithContent"},
            "tv-done": {"share_limit_action": "Stop"},
        },
        max_ratio_act=3,
    )
    finding = check(qbt, (arr,))
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == NOTHING_RECLAIMS
    assert finding.detail == (
        "sonarr[tv] (category tv-done): a limit can fire; action is Stop and the arr "
        "re-labels imports to category tv-done, so it can no longer remove them"
    )


def test_imported_category_with_remove_with_content_passes():
    """The twin: the imported category's own action is what decides."""
    arr = _arr(remove_completed=False, imported_category="tv-done")
    qbt = _qbt(
        {
            "tv": {"share_limit_action": "Stop"},
            "tv-done": {"share_limit_action": "RemoveWithContent"},
        },
        max_ratio_act=0,
    )
    assert check(qbt, (arr,)).outcome is Outcome.PASS


def test_imported_category_equal_to_the_category_is_not_a_relabel():
    arr = _arr(remove_completed=True, imported_category="tv")
    assert check(_qbt(max_ratio_act=0), (arr,)).outcome is Outcome.PASS


def test_super_seeding_fails_nothing_reclaims():
    finding = check(_qbt(max_ratio_act=2), (_arr(remove_completed=True),))
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == NOTHING_RECLAIMS
    assert finding.premises == _NOTHING
    assert "action is EnableSuperSeeding" in finding.detail


def test_default_action_minus_one_fails_nothing_reclaims():
    finding = check(_qbt(max_ratio_act=-1), (_arr(remove_completed=True),))
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == NOTHING_RECLAIMS
    assert finding.premises == _NOTHING


def test_category_remove_beats_global_remove_with_content():
    qbt = _qbt({"tv": {"share_limit_action": "Remove"}}, max_ratio_act=3)
    finding = check(qbt, (_arr(),))
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == REMOVE_KEEPS_FILES


def test_action_three_never_reads_reachability():
    """Review focus 5: junk criteria and unreadable global limits under action 3 still PASS."""
    junk = _indexer(seed_ratio="lots", seed_time=[1])
    qbt = _qbt(
        {"tv": {"ratio_limit": "x", "seeding_time_limit": None}},
        max_ratio_enabled=Unknown("insufficient-permission", "max_ratio_enabled"),
        max_seeding_time_enabled="maybe",
    )
    finding = check(qbt, (_arr(indexers=(junk,), remove_completed=None),))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == "sonarr[tv]: qBittorrent removes files at the limit"


def test_unlimited_seeding_passes_with_detail():
    """Review focus 2: no limit can fire, so nothing is orphaned. PASS, not SKIP."""
    qbt = _qbt(max_ratio_act=0, **GLOBALS_OFF)
    finding = check(qbt, (_arr(remove_completed=False),))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == "sonarr[tv]: no limit can fire; seeding is unlimited"


def test_an_arr_with_no_torrent_indexer_cannot_fire():
    finding = check(_qbt(max_ratio_act=1), (_arr(indexers=()),))
    assert finding.outcome is Outcome.PASS


# --- aggregation -------------------------------------------------------------


def test_remove_keeps_files_wins_over_nothing_reclaims():
    qbt = _qbt({"tv": {"share_limit_action": "Stop"}, "movies": {"share_limit_action": "Remove"}})
    arrs = (_arr("sonarr", "tv", remove_completed=False), _arr("radarr", "movies"))
    finding = check(qbt, arrs)
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == REMOVE_KEEPS_FILES
    assert finding.premises == _FILES_STAY
    assert finding.detail == (
        "radarr[movies] (category movies): a limit can fire; action is Remove, which keeps files"
    )


def test_unknown_remove_completed_skips_only_for_stop():
    arr = _arr(remove_completed=None)
    stop = check(_qbt(max_ratio_act=0), (arr,))
    assert stop.outcome is Outcome.SKIP
    assert stop.premises == (Premise(PREMISE_NOTHING_RECLAIMS, None),)
    assert "Remove Completed" in stop.detail
    assert check(_qbt(max_ratio_act=3), (arr,)).outcome is Outcome.PASS
    assert check(_qbt(max_ratio_act=1), (arr,)).outcome is Outcome.FAIL
    assert check(_qbt(max_ratio_act=2), (arr,)).outcome is Outcome.FAIL
    # Stop, but nothing can fire: the setting is not needed.
    assert check(_qbt(max_ratio_act=0, **GLOBALS_OFF), (arr,)).outcome is Outcome.PASS


@pytest.mark.parametrize(
    "qbt",
    [
        _qbt({"tv": {"share_limit_action": "Pause"}}),
        _qbt(max_ratio_act=Unknown("insufficient-permission", "max_ratio_act")),
        as_client(qbt_with("main", categories=Unknown("insufficient-permission", "categories"))),
    ],
    ids=["unknown-name", "unreadable-global", "unreadable-categories"],
)
def test_unknown_action_skips(qbt):
    finding = check(qbt, (_arr(),))
    assert finding.outcome is Outcome.SKIP
    assert "share-limit action could not be read" in finding.detail


def test_unknown_can_fire_skips():
    qbt = _qbt(
        max_ratio_act=1,
        max_ratio_enabled=Unknown("insufficient-permission", "max_ratio_enabled"),
        max_seeding_time_enabled=False,
    )
    finding = check(qbt, (_arr(),))
    assert finding.outcome is Outcome.SKIP
    assert finding.premises == (Premise(PREMISE_FILES_STAY, None),)


def test_unknown_in_one_arr_does_not_hide_a_fail_in_another():
    qbt = _qbt({"tv": {"share_limit_action": "Pause"}, "movies": {"share_limit_action": "Stop"}})
    arrs = (_arr("sonarr", "tv"), _arr("radarr", "movies", remove_completed=False))
    finding = check(qbt, arrs)
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == NOTHING_RECLAIMS
    assert finding.detail.startswith("radarr[movies] (category movies)")


def test_unreadable_download_clients_skip():
    arr = arr_with(_arr(), download_clients=Unknown("field-absent", "downloadclient"))
    finding = check(as_client(homelab_qbt()), (arr,))
    assert finding.outcome is Outcome.SKIP
    assert finding.detail == "sonarr[tv]: its download clients could not be read"


def test_no_arrs_is_not_applicable():
    finding = check(as_client(homelab_qbt()), ())
    assert finding.outcome is Outcome.NOT_APPLICABLE
    assert finding.invariant == INVARIANT_ID
    assert finding.instance == "qbittorrent[main]"


def test_arrs_using_another_client_pass_with_detail():
    arr = _arr(implementation="Transmission")
    finding = check(_qbt(max_ratio_act=1), (arr,))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == NO_ARR_SENDS_HERE


# --- run-level and the backend ----------------------------------------------

_SONARR = frozenset({"qbittorrent", "sonarr"})


def _orphan_findings(facts):
    return [f for f in run_checks(facts, declared=_SONARR) if f.invariant == INVARIANT_ID]


def test_failed_arr_turns_pass_into_skip():
    failed = ErrorRow("arr[radarr]", "unreachable", "connection refused")
    ok = StackFacts(clients=(as_client(homelab_qbt()),), arrs=(_arr(),))
    (passing,) = _orphan_findings(ok)
    assert passing.outcome is Outcome.PASS

    broken = StackFacts(clients=ok.clients, arrs=ok.arrs, errors=(failed,))
    (skipped,) = _orphan_findings(broken)
    assert skipped.outcome is Outcome.SKIP
    assert skipped.detail == "an arr could not be read, so its downloads could not be checked"
    assert skipped.premises == (Premise(PREMISE_NOTHING_RECLAIMS, None),)


def test_failed_arr_does_not_hide_a_fail():
    failed = ErrorRow("arr[radarr]", "unreachable", "connection refused")
    facts = StackFacts(clients=(_qbt(max_ratio_act=1),), arrs=(_arr(),), errors=(failed,))
    (finding,) = _orphan_findings(facts)
    assert finding.outcome is Outcome.FAIL


def test_backend_registers_the_check_after_queue_liveness():
    assert BACKEND.checks[-1] is orphan_inevitability.check
    assert len(BACKEND.checks) == 2
    assert BACKEND.arr_dependent == {
        INVARIANT_ID: ArrDependency(
            PREMISE_NOTHING_RECLAIMS,
            "an arr could not be read, so its downloads could not be checked",
        )
    }


def test_module_exposes_the_names():
    assert (INVARIANT_ID, REMOVE_KEEPS_FILES, NOTHING_RECLAIMS) == (
        "orphan-inevitability",
        "remove-keeps-files",
        "nothing-reclaims",
    )
    assert (PREMISE_FILES_STAY, PREMISE_NOTHING_RECLAIMS) == (
        "arr.limit_fires_and_files_stay",
        "arr.limit_fires_and_nothing_reclaims",
    )


# --- fix round 1 --------------------------------------------------------------

#: Ratio unreadable and every other arm off, so whether a limit can fire is unknown.
_REACH_UNKNOWN = {
    "max_ratio_enabled": Unknown("insufficient-permission", "max_ratio_enabled"),
    "max_seeding_time_enabled": False,
}


@pytest.mark.parametrize(
    "qbt",
    [
        _qbt(max_ratio_act=1, **_REACH_UNKNOWN),
        _qbt(max_ratio_act=0, **_REACH_UNKNOWN),
        _qbt({"tv": {"share_limit_action": "Pause"}}),
    ],
    ids=["files-stay-unknown", "nothing-reclaims-unknown", "both-unknown"],
)
def test_every_skip_carries_the_nothing_reclaims_conflict(qbt):
    """The edge key must not flip between conflicts while the outcome stays SKIP."""
    finding = check(qbt, (_arr(remove_completed=False),))
    assert finding.outcome is Outcome.SKIP
    assert finding.conflict == NOTHING_RECLAIMS


def test_skip_premises_still_name_what_was_unknown():
    files = check(
        _qbt(max_ratio_act=1, **_REACH_UNKNOWN),
        (_arr(),),
    )
    assert files.premises == (Premise(PREMISE_FILES_STAY, None),)
    both = check(_qbt({"tv": {"share_limit_action": "Pause"}}), (_arr(),))
    assert both.premises == (
        Premise(PREMISE_FILES_STAY, None),
        Premise(PREMISE_NOTHING_RECLAIMS, None),
    )


def test_unknown_remove_completed_and_unknown_reach_names_both():
    qbt = _qbt(max_ratio_act=0, **_REACH_UNKNOWN)
    finding = check(qbt, (_arr(remove_completed=None),))
    assert finding.outcome is Outcome.SKIP
    assert finding.detail == (
        "sonarr[tv] (category tv): the arr's Remove Completed setting could not be read; "
        "sonarr[tv] (category tv): whether a seed limit can fire could not be read"
    )


# --- final fix wave -----------------------------------------------------------


def test_stop_with_a_category_ratio_the_global_covers_passes():
    """Sonarr reads the global ratio for a -2 torrent, so it removes this one."""
    qbt = _qbt(
        {"tv": {"ratio_limit": 2.0}},
        max_ratio_act=0,
        max_ratio_enabled=True,
        max_ratio=1.0,
    )
    finding = check(qbt, (_arr(remove_completed=True),))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == (
        "sonarr[tv]: qBittorrent stops at the limit and the arr removes completed downloads"
    )


def test_stop_with_an_unreadable_global_ratio_beside_a_category_ratio_skips():
    qbt = _qbt(
        {"tv": {"ratio_limit": 2.0}},
        max_ratio_act=0,
        max_ratio=Unknown("insufficient-permission", "max_ratio"),
    )
    finding = check(qbt, (_arr(remove_completed=True),))
    assert finding.outcome is Outcome.SKIP
    assert finding.premises == (Premise(PREMISE_NOTHING_RECLAIMS, None),)


# --- the global inactive limit covers a category's own one (lintarr#59) -------


def test_stop_with_a_category_inactive_limit_the_global_covers_passes():
    """The false FAIL lintarr#59 reported: the arr does check the global inactive limit."""
    qbt = _qbt(
        {"tv": {"inactive_seeding_time_limit": 60}},
        max_ratio_act=0,
        max_inactive_seeding_time_enabled=True,
        max_inactive_seeding_time=30,
    )
    finding = check(qbt, (_arr(remove_completed=True),))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == (
        "sonarr[tv]: qBittorrent stops at the limit and the arr removes completed downloads"
    )


def test_stop_with_a_category_inactive_limit_the_global_does_not_cover_fails():
    """The twin, and the FAIL this check still owes: armed above the category's value."""
    qbt = _qbt(
        {"tv": {"inactive_seeding_time_limit": 60}},
        max_ratio_act=0,
        max_inactive_seeding_time_enabled=True,
        max_inactive_seeding_time=1440,
    )
    finding = check(qbt, (_arr(remove_completed=True),))
    assert finding.outcome is Outcome.FAIL
    assert finding.conflict == NOTHING_RECLAIMS
    assert finding.premises == _NOTHING


def test_stop_with_an_unreadable_global_inactive_value_skips():
    qbt = _qbt(
        {"tv": {"inactive_seeding_time_limit": 60}},
        max_ratio_act=0,
        max_inactive_seeding_time_enabled=True,
        max_inactive_seeding_time=Unknown("insufficient-permission", "max_inactive_seeding_time"),
    )
    finding = check(qbt, (_arr(remove_completed=True),))
    assert finding.outcome is Outcome.SKIP
    assert finding.premises == (Premise(PREMISE_NOTHING_RECLAIMS, None),)


def test_default_action_minus_one_is_named_in_the_detail():
    finding = check(_qbt(max_ratio_act=-1), (_arr(remove_completed=True),))
    assert finding.detail == (
        "sonarr[tv] (category tv): a limit can fire; action is Default, which removes nothing"
    )


# --- #63: a PASS detail must not claim a limit fires when none can ------------


def test_stop_with_arr_removal_and_no_limit_at_all_says_seeding_is_unlimited():
    """Remove Completed is on, but nothing can ever stop a torrent.

    The old line said "qBittorrent stops at the limit and the arr removes
    completed downloads", which reads as a limit firing. Nothing fires here.
    """
    qbt = _qbt(max_ratio_act=0, **GLOBALS_OFF)
    finding = check(qbt, (_arr(remove_completed=True),))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == "sonarr[tv]: no limit can fire; seeding is unlimited"


def test_stop_with_arr_removal_keeps_the_stop_line_when_a_limit_does_fire():
    """The twin, so the two reasons stay mapped to their own lines.

    The homelab's global ratio and seeding time are on, so a limit fires and
    the arr recognises it.
    """
    finding = check(_qbt(max_ratio_act=0), (_arr(remove_completed=True),))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == (
        "sonarr[tv]: qBittorrent stops at the limit and the arr removes completed downloads"
    )
