"""The ``hardlink-futility`` shared check, against the spec's triple rules."""

import pytest

from lintarr.checks import hardlink_futility
from lintarr.checks.hardlink_futility import CROSS_MOUNT, INVARIANT_ID, PREMISE, check
from lintarr.facts import Unknown
from lintarr.models import ArrDownloadClient, ErrorRow, RemotePathMapping, RootFolder, StackFacts
from lintarr.outcomes import Outcome, Premise
from lintarr.run import run_checks
from tests.fixtures.clients import as_client
from tests.fixtures.homelab import qbt_with
from tests.fixtures.homelab_paths import arr_with, homelab_arr, homelab_qbt

_UNREAD = Unknown("field-absent", "not read in this test")
_SPLIT_MOUNTS = ("/", "/downloads", "/tv")
_SPLIT_DETAIL = (
    "sonarr[tv]: downloads in /downloads/tv are on mount /downloads, "
    "root folder /tv/shows is on mount /tv"
)


def _client(**overrides):
    """A qBittorrent with Tom's auto-TMM settings, then *overrides*."""
    base = {
        "save_path": "/data/torrents",
        "auto_tmm_enabled": True,
        "use_category_paths_in_manual_mode": False,
        "categories": {"tv": {"name": "tv", "savePath": "/data/torrents/tv"}},
    }
    return as_client(qbt_with("main", **(base | overrides)))


def _split_qbt():
    return _client(categories={"tv": {"name": "tv", "savePath": "/downloads/tv"}})


def _split_arr(**overrides):
    fields = {
        "mounts": _SPLIT_MOUNTS,
        "root_folders": (RootFolder("/tv/shows", True),),
    }
    return arr_with(**(fields | overrides))


def _qbt_client(category="tv", host="gluetun", enabled=True, implementation="QBittorrent"):
    return ArrDownloadClient("qBittorrent", implementation, enabled, host, category, False, "")


def test_homelab_passes():
    arrs = (homelab_arr("sonarr", "tv"), homelab_arr("radarr", "movies"))
    finding = check(as_client(homelab_qbt()), arrs)
    assert finding.outcome is Outcome.PASS
    assert finding.detail == (
        "downloads and library folders share a mount for sonarr[tv], radarr[movies]"
    )
    assert (finding.invariant, finding.conflict) == (INVARIANT_ID, CROSS_MOUNT)


def test_separate_download_and_library_mounts_fail():
    finding = check(_split_qbt(), (_split_arr(),))
    assert finding.outcome is Outcome.FAIL
    assert finding.premises == (Premise(PREMISE, True),)
    assert finding.detail == _SPLIT_DETAIL

    # Mutation twin: only the mount layout changes, to one /data volume.
    twin = check(
        _client(categories={"tv": {"name": "tv", "savePath": "/data/torrents/tv"}}),
        (
            arr_with(
                mounts=("/", "/data"),
                root_folders=(RootFolder("/data/media/tv", True),),
            ),
        ),
    )
    assert twin.outcome is Outcome.PASS


_HARDLINKS_OFF = "sonarr[tv]: hardlinks are off, so imports copy by design"


def test_hardlinks_off_passes_even_with_unknown_mounts():
    arr = _split_arr(copy_using_hardlinks=False, mounts=_UNREAD, root_folders=_UNREAD)
    finding = check(_split_qbt(), (arr,))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == _HARDLINKS_OFF


def test_hardlinks_off_arr_is_involved_not_absent():
    """It does send downloads here, so "no collected arr sends downloads" would be false."""
    off = _split_arr(copy_using_hardlinks=False)
    shared = arr_with(homelab_arr("radarr", "movies"), download_clients=(_qbt_client(),))
    finding = check(_client(), (off, shared))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == (
        f"downloads and library folders share a mount for radarr[movies]; {_HARDLINKS_OFF}"
    )


def test_hardlinks_off_arr_on_another_client_is_not_named():
    off = _split_arr(copy_using_hardlinks=False, download_clients=(_qbt_client(enabled=False),))
    assert check(_split_qbt(), (off,)).detail == "no collected arr sends downloads to this client"


def test_hardlinks_off_settles_even_unreadable_download_clients():
    off = _split_arr(copy_using_hardlinks=False, download_clients=_UNREAD)
    finding = check(_split_qbt(), (off,))
    assert (finding.outcome, finding.detail) == (Outcome.PASS, _HARDLINKS_OFF)


def test_hardlinks_unknown_on_split_mounts_skips():
    arr = _split_arr(copy_using_hardlinks=_UNREAD)
    finding = check(_split_qbt(), (arr,))
    assert finding.outcome is Outcome.SKIP
    assert finding.premises == (Premise(PREMISE, None),)
    assert "sonarr[tv]" in finding.detail


def test_hardlinks_unknown_on_one_mount_passes():
    """Same mount settles it without the hardlink setting."""
    arr = arr_with(copy_using_hardlinks=_UNREAD)
    assert check(as_client(homelab_qbt()), (arr,)).outcome is Outcome.PASS


def test_remote_path_mapping_is_applied_before_picking_the_mount():
    qbt = _client(categories={"tv": {"name": "tv", "savePath": "/downloads/tv"}})
    arr = arr_with(
        mounts=("/", "/data"),
        remote_path_mappings=(RemotePathMapping("GLUETUN", "/downloads/", "/data/torrents/"),),
    )
    assert check(qbt, (arr,)).outcome is Outcome.PASS


def test_remote_path_mapping_for_another_host_is_not_applied():
    qbt = _client(categories={"tv": {"name": "tv", "savePath": "/downloads/tv"}})
    arr = arr_with(
        mounts=("/", "/data"),
        remote_path_mappings=(RemotePathMapping("other", "/downloads/", "/data/torrents/"),),
    )
    finding = check(qbt, (arr,))
    assert finding.outcome is Outcome.SKIP
    assert finding.detail == (
        "sonarr[tv]: the arr reports no mount other than / for /downloads/tv; "
        "either it cannot see the download or that mount is not listed "
        "(network mounts are not)"
    )


def test_a_library_only_on_root_is_unknown_and_named():
    arr = arr_with(mounts=("/", "/data"), root_folders=(RootFolder("/tv", True),))
    finding = check(as_client(homelab_qbt()), (arr,))
    assert finding.outcome is Outcome.SKIP
    assert finding.detail == (
        "sonarr[tv]: the arr reports no mount for root folder /tv other than /, "
        "so the library is not on a volume"
    )


def test_bare_metal_arr_with_everything_on_root_passes():
    arr = arr_with(mounts=("/",))
    assert check(as_client(homelab_qbt()), (arr,)).outcome is Outcome.PASS


def test_blank_arr_category_uses_the_uncategorised_save_path():
    arr = _split_arr(download_clients=(_qbt_client(category=""),))
    qbt = _client(save_path="/downloads/tv", categories={})
    finding = check(qbt, (arr,))
    assert finding.outcome is Outcome.FAIL
    assert finding.detail == _SPLIT_DETAIL


def test_inaccessible_root_folder_is_ignored_and_named():
    roots = (RootFolder("/data/media/tv", True), RootFolder("/tv/old", False))
    arr = arr_with(mounts=("/", "/data", "/tv"), root_folders=roots)
    finding = check(as_client(homelab_qbt()), (arr,))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == (
        "downloads and library folders share a mount for sonarr[tv]; "
        "sonarr[tv]: root folder /tv/old is not accessible and was left out"
    )


def test_an_arr_with_no_root_folders_passes_and_says_nothing_imports():
    finding = check(as_client(homelab_qbt()), (arr_with(homelab_arr(), root_folders=()),))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == "sonarr[tv]: no root folders, so nothing imports"


def test_root_folder_with_unstated_accessibility_counts():
    arr = _split_arr(root_folders=(RootFolder("/tv/shows", None),))
    assert check(_split_qbt(), (arr,)).outcome is Outcome.FAIL


def test_no_arrs_is_not_applicable():
    finding = check(as_client(homelab_qbt()), ())
    assert finding.outcome is Outcome.NOT_APPLICABLE
    assert (finding.invariant, finding.instance) == (INVARIANT_ID, "qbittorrent[main]")


@pytest.mark.parametrize(
    "client",
    [
        _qbt_client(implementation="Sabnzbd"),
        _qbt_client(enabled=False),
        _qbt_client(category="anime"),
    ],
    ids=["usenet-only", "disabled", "category-this-client-lacks"],
)
def test_arrs_that_use_no_client_of_this_kind_pass_with_detail(client):
    arr = _split_arr(download_clients=(client,))
    finding = check(_split_qbt(), (arr,))
    assert finding.outcome is Outcome.PASS
    assert finding.detail == "no collected arr sends downloads to this client"


@pytest.mark.parametrize(
    "field", ["root_folders", "mounts", "remote_path_mappings", "download_clients"]
)
def test_any_needed_unknown_arr_input_skips(field):
    finding = check(_split_qbt(), (_split_arr(**{field: _UNREAD}),))
    assert finding.outcome is Outcome.SKIP
    assert finding.premises == (Premise(PREMISE, None),)
    assert finding.detail.startswith("sonarr[tv]: ")


def test_unknown_client_save_paths_skips():
    qbt = _client(save_path=_UNREAD)
    assert not hasattr(qbt.common.save_paths, "value")
    finding = check(qbt, (_split_arr(),))
    assert finding.outcome is Outcome.SKIP
    assert finding.premises == (Premise(PREMISE, None),)


@pytest.mark.parametrize("junk", [True, "5", None, {"a": 1}])
def test_junk_wearing_a_known_is_unknown(junk):
    for field in ("root_folders", "mounts", "remote_path_mappings", "download_clients"):
        finding = check(_split_qbt(), (_split_arr(**{field: junk}),))
        assert finding.outcome is Outcome.SKIP, field


@pytest.mark.parametrize(
    "field, junk",
    [
        ("category", None),
        ("category", 5),
        ("host", 5),
        ("host", ["gluetun"]),
        ("enabled", "yes"),
        ("enabled", None),
        ("implementation", None),
    ],
)
def test_a_download_client_with_unreadable_fields_is_unknown(field, junk):
    """Neither a crash in ``translate`` nor a silent "disabled"."""
    from dataclasses import replace as replace_field

    bad = replace_field(_qbt_client(), **{field: junk})
    finding = check(_split_qbt(), (_split_arr(download_clients=(bad,)),))
    assert finding.outcome is Outcome.SKIP
    assert finding.detail == (
        "sonarr[tv]: download client 'qBittorrent' has fields that could not be read"
    )


def test_a_save_path_that_translates_to_non_posix_names_both_paths():
    qbt = _client(categories={"tv": {"name": "tv", "savePath": "/downloads/tv"}})
    mapping = RemotePathMapping("gluetun", "/downloads/", "D:\\media\\")
    finding = check(qbt, (_split_arr(remote_path_mappings=(mapping,)),))
    assert finding.outcome is Outcome.SKIP
    assert finding.detail == (
        "sonarr[tv]: save path 'D:\\\\media/tv' (translated from '/downloads/tv') "
        "is not absolute POSIX"
    )


def test_an_untranslated_non_posix_save_path_names_it_once():
    qbt = _client(save_path="C:\\Downloads\\tv", categories={})
    arr = _split_arr(download_clients=(_qbt_client(category=""),))
    finding = check(qbt, (arr,))
    assert finding.outcome is Outcome.SKIP
    assert finding.detail == ("sonarr[tv]: save path 'C:\\\\Downloads\\\\tv' is not absolute POSIX")


def test_a_save_path_with_a_parent_segment_is_unknown_not_a_cross_mount_fail():
    """``/downloads/../media/tv`` really sits on /media beside the root folder, so a
    hardlink there works. Compared lexically it reads as /downloads: a false FAIL."""
    qbt = _client(categories={"tv": {"name": "tv", "savePath": "/downloads/../media/tv"}})
    arr = _split_arr(
        mounts=("/", "/downloads", "/media"),
        root_folders=(RootFolder("/media/tv/shows", True),),
    )
    finding = check(qbt, (arr,))
    assert finding.outcome is Outcome.SKIP
    assert finding.detail == (
        "sonarr[tv]: save path '/downloads/../media/tv' contains a .. segment, "
        "so the mount it is on cannot be read"
    )


def test_a_root_folder_with_a_parent_segment_is_unknown():
    arr = _split_arr(root_folders=(RootFolder("/tv/../downloads/shows", True),))
    finding = check(_split_qbt(), (arr,))
    assert finding.outcome is Outcome.SKIP
    assert finding.detail == (
        "sonarr[tv]: root folder '/tv/../downloads/shows' contains a .. segment, "
        "so the mount it is on cannot be read"
    )


def test_unknown_in_one_triple_does_not_hide_a_fail_in_another():
    unknown = arr_with(homelab_arr("sonarr", "anime"), mounts=_UNREAD)
    finding = check(_split_qbt(), (unknown, _split_arr()))
    assert finding.outcome is Outcome.FAIL
    assert finding.detail == _SPLIT_DETAIL


def test_fail_lists_every_conflicting_triple():
    roots = (RootFolder("/tv/shows", True), RootFolder("/kids/shows", True))
    arr = _split_arr(mounts=(*_SPLIT_MOUNTS, "/kids"), root_folders=roots)
    other = arr_with(_split_arr(), name="4k")
    finding = check(_split_qbt(), (arr, other))
    assert finding.outcome is Outcome.FAIL
    assert finding.detail == "; ".join(
        [
            _SPLIT_DETAIL,
            "sonarr[tv]: downloads in /downloads/tv are on mount /downloads, "
            "root folder /kids/shows is on mount /kids",
            _SPLIT_DETAIL.replace("sonarr[tv]", "sonarr[4k]"),
        ]
    )


def test_finding_instance_is_the_client_label():
    qbt = as_client(homelab_qbt("vpn"))
    assert check(qbt, (homelab_arr(),)).instance == "qbittorrent[vpn]"
    assert check(qbt, ()).instance == "qbittorrent[vpn]"


_SONARR = frozenset({"qbittorrent", "sonarr"})


def _hardlink_findings(facts, declared=_SONARR):
    return [f for f in run_checks(facts, declared=declared) if f.invariant == INVARIANT_ID]


def test_a_failed_arr_turns_pass_into_skip():
    failed = ErrorRow("arr[radarr]", "unreachable", "connection refused")
    ok = StackFacts(clients=(as_client(homelab_qbt()),), arrs=(homelab_arr(),))
    (passing,) = _hardlink_findings(ok)
    assert passing.outcome is Outcome.PASS

    broken = StackFacts(clients=ok.clients, arrs=ok.arrs, errors=(failed,))
    (skipped,) = _hardlink_findings(broken)
    assert skipped.outcome is Outcome.SKIP
    assert skipped.detail == "an arr could not be read, so its imports could not be checked"
    assert skipped.premises == (Premise(PREMISE, None),)


def test_a_failed_arr_turns_not_applicable_into_skip():
    failed = ErrorRow("arr[sonarr]", "unreachable", "connection refused")
    facts = StackFacts(clients=(as_client(homelab_qbt()),), arrs=(), errors=(failed,))
    (finding,) = _hardlink_findings(facts)
    assert finding.outcome is Outcome.SKIP


def test_a_failed_arr_does_not_hide_a_fail():
    failed = ErrorRow("arr[radarr]", "unreachable", "connection refused")
    facts = StackFacts(clients=(_split_qbt(),), arrs=(_split_arr(),), errors=(failed,))
    (finding,) = _hardlink_findings(facts)
    assert finding.outcome is Outcome.FAIL


def test_a_failed_arr_leaves_queue_liveness_alone():
    failed = ErrorRow("arr[radarr]", "unreachable", "connection refused")
    base = StackFacts(clients=(as_client(homelab_qbt()),), arrs=(homelab_arr(),))
    broken = StackFacts(clients=base.clients, arrs=base.arrs, errors=(failed,))

    def queue(facts):
        return [f for f in run_checks(facts, declared=_SONARR) if f.invariant == "queue-liveness"]

    assert queue(broken) == queue(base)


def test_module_exposes_the_global_constants():
    assert hardlink_futility.INVARIANT_ID == "hardlink-futility"
    assert hardlink_futility.CROSS_MOUNT == "cross-mount"
    assert hardlink_futility.PREMISE == "arr.hardlink_import_crosses_a_mount"
