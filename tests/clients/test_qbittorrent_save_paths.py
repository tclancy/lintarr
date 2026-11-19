"""The qBittorrent rule for ``ClientFacts.save_paths``."""

import pytest

from lintarr.clients.qbittorrent.save_paths import save_paths
from lintarr.facts import Known, Unknown
from tests.collect.test_qbittorrent_facts import _collect
from tests.fixtures.homelab import qbt_with

CATS = {
    "movies": {"name": "movies", "savePath": "/data/torrents/movies"},
    "tv": {"name": "tv", "savePath": "/data/torrents/tv"},
}


def _qbt(**kw):
    base = {
        "save_path": "/data/torrents",
        "auto_tmm_enabled": True,
        "use_category_paths_in_manual_mode": False,
        "categories": CATS,
    }
    return qbt_with(**(base | kw))


def _mapping(**kw):
    fact = save_paths(_qbt(**kw))
    assert isinstance(fact, Known), fact
    return fact.value


def test_uncategorised_key_is_the_default_save_path():
    assert _mapping()[""] == "/data/torrents"


def test_auto_tmm_uses_each_category_save_path():
    assert _mapping() == {
        "": "/data/torrents",
        "movies": "/data/torrents/movies",
        "tv": "/data/torrents/tv",
    }


def test_empty_category_save_path_is_default_plus_name():
    assert _mapping(categories={"tv": {"name": "tv", "savePath": ""}})["tv"] == "/data/torrents/tv"


def test_relative_category_save_path_is_joined_to_default():
    cats = {"tv": {"name": "tv", "savePath": "shows/tv"}}
    assert _mapping(categories=cats)["tv"] == "/data/torrents/shows/tv"


def test_manual_mode_maps_every_category_to_the_default():
    m = _mapping(auto_tmm_enabled=False)
    assert m == {"": "/data/torrents", "movies": "/data/torrents", "tv": "/data/torrents"}


def test_manual_mode_with_category_paths_on_uses_category_paths():
    m = _mapping(auto_tmm_enabled=False, use_category_paths_in_manual_mode=True)
    assert m["tv"] == "/data/torrents/tv"


_UNKNOWN = Unknown("field-absent", "unread")


@pytest.mark.parametrize(
    "override",
    [
        {"save_path": _UNKNOWN},
        {"auto_tmm_enabled": _UNKNOWN},
        {"use_category_paths_in_manual_mode": _UNKNOWN, "auto_tmm_enabled": False},
        {"categories": _UNKNOWN},
        {"categories": ["tv"]},
        {"categories": {"tv": "/data/torrents/tv"}},
        {"categories": {"tv": {"name": "tv", "savePath": 5}}},
        {"save_path": 5},
        {"auto_tmm_enabled": "true"},
    ],
    ids=[
        "save_path-unknown",
        "auto_tmm-unknown",
        "manual-flag-unknown",
        "categories-unknown",
        "categories-not-a-dict",
        "entry-not-a-dict",
        "savepath-not-a-string",
        "save_path-not-a-string",
        "auto_tmm-not-a-bool",
    ],
)
def test_any_needed_unknown_input_makes_the_mapping_unknown(override):
    assert isinstance(save_paths(_qbt(**override)), Unknown)


def test_an_unneeded_manual_flag_does_not_matter():
    assert isinstance(save_paths(_qbt(use_category_paths_in_manual_mode=_UNKNOWN)), Known)


def test_known_mapping_cites_its_sources():
    qbt, _ = _collect(prefs={"save_path": "/d", "auto_tmm_enabled": True}, categories=CATS)
    cited = save_paths(qbt)
    assert isinstance(cited, Known)
    assert "GET /api/v2/app/preferences" in cited.source
    assert "GET /api/v2/torrents/categories" in cited.source
    assert cited.service_version == "v5.2.3"


def test_collect_reads_the_three_new_prefs():
    prefs = {
        "save_path": "/data/torrents",
        "auto_tmm_enabled": True,
        "use_category_paths_in_manual_mode": False,
    }
    qbt, _ = _collect(prefs=prefs, categories=CATS)
    assert qbt.save_path.value == "/data/torrents"
    assert qbt.auto_tmm_enabled.value is True
    assert qbt.use_category_paths_in_manual_mode.value is False
    assert save_paths(qbt).value["tv"] == "/data/torrents/tv"
