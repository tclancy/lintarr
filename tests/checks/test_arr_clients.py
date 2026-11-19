"""The shared arr-client matcher, moved out of hardlink-futility."""

from lintarr.checks.arr_clients import (
    NO_ARR_SENDS_HERE,
    arr_label,
    clients_that_are,
    is_this_client,
    is_well_formed,
    save_paths_of,
    tuple_of,
)
from lintarr.facts import Unknown
from lintarr.models import ArrDownloadClient, RootFolder
from tests.fixtures.clients import as_client
from tests.fixtures.homelab_paths import arr_with, homelab_arr, homelab_qbt

_UNREAD = Unknown("field-absent", "not read in this test")
_SAVE_PATHS = {"tv": "/data/torrents/tv"}


def _d(category="tv", host="gluetun", enabled=True, implementation="QBittorrent"):
    return ArrDownloadClient("qBittorrent", implementation, enabled, host, category, False, "")


def _match(clients, save_paths=_SAVE_PATHS):
    arr = arr_with(homelab_arr(), download_clients=clients)
    return clients_that_are(arr, as_client(homelab_qbt()), save_paths)


def test_a_matching_client_is_returned():
    d = _d()
    assert _match((d,)) == (d,)


def test_another_kind_is_not_this_client():
    assert _match((_d(implementation="Sabnzbd"),)) == ()


def test_a_disabled_client_is_not_this_client():
    assert _match((_d(enabled=False),)) == ()


def test_a_category_the_client_lacks_is_not_this_client():
    assert _match((_d(category="movies"),)) == ()


def test_unread_save_paths_let_any_client_of_the_kind_match():
    d = _d(category="movies")
    assert _match((d,), save_paths=None) == (d,)


def test_a_malformed_client_gives_a_reason():
    result = _match((_d(host=7),))
    assert result == "sonarr[tv]: download client 'qBittorrent' has fields that could not be read"


def test_an_unreadable_list_gives_a_reason():
    arr = arr_with(homelab_arr(), download_clients=_UNREAD)
    result = clients_that_are(arr, as_client(homelab_qbt()), _SAVE_PATHS)
    assert result == "sonarr[tv]: its download clients could not be read"


def test_helpers_are_public():
    arr = homelab_arr()
    assert arr_label(arr) == "sonarr[tv]"
    assert tuple_of(arr.root_folders, RootFolder) is not None
    assert tuple_of(arr.root_folders, str) is None
    assert save_paths_of(as_client(homelab_qbt())) is not None
    assert is_well_formed(_d())
    assert is_this_client(_d(), as_client(homelab_qbt()), None)
    assert NO_ARR_SENDS_HERE == "no collected arr sends downloads to this client"
