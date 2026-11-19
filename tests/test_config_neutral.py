"""Client-neutral configuration: ``DOWNLOAD_CLIENT_*`` and ``ARR_*``.

One test per load-time rule in the spec's Configuration section, plus the
legacy names, which are rejected rather than read so a half-migrated
environment can never run against the services it meant to name.
"""

import os
import re

import pytest
from click.testing import CliRunner

from lintarr.cli import cli
from lintarr.config import load_config

NEUTRAL = {
    "DOWNLOAD_CLIENT_KIND": "qbittorrent",
    "DOWNLOAD_CLIENT_URL": "http://q:8080/",
    "DOWNLOAD_CLIENT_USER": "admin",
    "DOWNLOAD_CLIENT_PASS": "hunter2",
    "ARR_URL__MOVIES": "http://radarr:7878",
    "ARR_API_KEY__MOVIES": "mk",
    "ARR_URL__TV": "http://sonarr:8989",
    "ARR_API_KEY__TV": "tk",
}

#: Every legacy variable, bare and suffixed, and the exact name that replaces it.
#: Arr replacements carry an instance name from the old app name, so a Sonarr
#: and a Radarr can never be told to rename onto the same variable.
LEGACY = {
    "QBIT_URL": "DOWNLOAD_CLIENT_URL",
    "QBIT_URL__4K": "DOWNLOAD_CLIENT_URL__4K",
    "QBIT_USER": "DOWNLOAD_CLIENT_USER",
    "QBIT_USER__4K": "DOWNLOAD_CLIENT_USER__4K",
    "QBIT_PASS": "DOWNLOAD_CLIENT_PASS",
    "QBIT_PASS__4K": "DOWNLOAD_CLIENT_PASS__4K",
    "SONARR_URL": "ARR_URL__SONARR",
    "SONARR_URL__4K": "ARR_URL__SONARR_4K",
    "SONARR_API_KEY": "ARR_API_KEY__SONARR",
    "SONARR_API_KEY__4K": "ARR_API_KEY__SONARR_4K",
    "RADARR_URL": "ARR_URL__RADARR",
    "RADARR_URL__4K": "ARR_URL__RADARR_4K",
    "RADARR_API_KEY": "ARR_API_KEY__RADARR",
    "RADARR_API_KEY__4K": "ARR_API_KEY__RADARR_4K",
}


def _message(env) -> str:
    with pytest.raises(ValueError) as excinfo:
        load_config(env)
    return str(excinfo.value)


def test_a_full_neutral_environment_loads():
    cfg = load_config(NEUTRAL)
    assert [(c.name, c.kind, c.url) for c in cfg.clients] == [
        ("main", "qbittorrent", "http://q:8080")
    ]
    assert (cfg.clients[0].username, cfg.clients[0].password) == ("admin", "hunter2")
    assert [(a.name, a.url, a.api_key) for a in cfg.arrs] == [
        ("movies", "http://radarr:7878", "mk"),
        ("tv", "http://sonarr:8989", "tk"),
    ]
    assert cfg.declared_explicit is None


def test_lintarr_services_is_read_as_the_explicit_declaration():
    cfg = load_config(NEUTRAL | {"LINTARR_SERVICES": " QBittorrent, sonarr ,"})
    assert cfg.declared_explicit == frozenset({"qbittorrent", "sonarr"})


@pytest.mark.parametrize(("legacy", "replacement"), sorted(LEGACY.items()))
def test_a_legacy_name_is_rejected_naming_its_replacement(legacy, replacement):
    """Pinned as the pairing: ``ARR_URL`` alone is a substring of ``SONARR_URL``."""
    message = _message({legacy: "x"})
    assert f"rename {legacy} to {replacement};" in f"{message};"
    if legacy.startswith("QBIT_"):
        assert "DOWNLOAD_CLIENT_KIND=qbittorrent" in message


def test_every_legacy_name_present_is_named_at_once():
    env = {"QBIT_URL": "x", "QBIT_PASS__4K": "x", "RADARR_API_KEY__HD": "x"}
    message = _message(env)
    for name, replacement in [
        ("QBIT_URL", "DOWNLOAD_CLIENT_URL"),
        ("QBIT_PASS__4K", "DOWNLOAD_CLIENT_PASS__4K"),
        ("RADARR_API_KEY__HD", "ARR_API_KEY__RADARR_HD"),
    ]:
        assert f"rename {name} to {replacement}" in message
    assert "DOWNLOAD_CLIENT_KIND=qbittorrent" in message


def test_arr_only_legacy_names_do_not_mention_a_client_kind():
    assert "DOWNLOAD_CLIENT_KIND" not in _message({"SONARR_URL": "x", "SONARR_API_KEY": "k"})


def test_a_legacy_name_alongside_the_neutral_names_is_still_rejected():
    """Review Focus 1: a half-migrated environment must not run on the half it reads."""
    message = _message(NEUTRAL | {"SONARR_URL": "http://old:8989"})
    assert "rename SONARR_URL to ARR_URL__SONARR" in message


_RENAME = re.compile(r"rename (\w+) to (\w+)")


def test_following_the_renames_for_a_sonarr_and_a_radarr_keeps_both():
    """Both apps map to the one ``ARR_URL`` base. Suggesting ``ARR_URL`` for each
    would have an operator overwrite one arr with the other, and with
    ``LINTARR_SERVICES`` unset the lost arr is never declared, so the run goes
    green over a service it never read. Applied literally, the message must
    produce two arrs."""
    legacy = {
        "SONARR_URL": "http://sonarr:8989",
        "SONARR_API_KEY": "sk",
        "RADARR_URL": "http://radarr:7878",
        "RADARR_API_KEY": "rk",
    }
    renames = dict(_RENAME.findall(_message(legacy)))
    assert renames.keys() == legacy.keys()
    assert len(set(renames.values())) == len(renames), renames

    migrated = {renames[key]: value for key, value in legacy.items()}
    cfg = load_config(migrated)
    assert [(a.name, a.url, a.api_key) for a in cfg.arrs] == [
        ("radarr", "http://radarr:7878", "rk"),
        ("sonarr", "http://sonarr:8989", "sk"),
    ]


@pytest.mark.parametrize("suffix", ["", "__VPN"])
def test_a_url_without_a_kind_is_an_error(suffix):
    env = {f"DOWNLOAD_CLIENT_URL{suffix}": "http://q", f"DOWNLOAD_CLIENT_USER{suffix}": "u"}
    env[f"DOWNLOAD_CLIENT_PASS{suffix}"] = "p"
    assert f"DOWNLOAD_CLIENT_KIND{suffix}" in _message(env)


@pytest.mark.parametrize("suffix", ["", "__VPN"])
def test_a_kind_without_a_url_is_an_error(suffix):
    assert f"DOWNLOAD_CLIENT_URL{suffix}" in _message(
        {f"DOWNLOAD_CLIENT_KIND{suffix}": "qbittorrent"}
    )


def test_an_unknown_kind_names_itself_and_the_known_kinds():
    message = _message(NEUTRAL | {"DOWNLOAD_CLIENT_KIND": "Transmission"})
    assert "Transmission" in message
    assert "qbittorrent" in message


def test_the_kind_is_normalised_before_lookup():
    """Review Focus 2."""
    cfg = load_config(NEUTRAL | {"DOWNLOAD_CLIENT_KIND": " QBittorrent "})
    assert [c.kind for c in cfg.clients] == ["qbittorrent"]


@pytest.mark.parametrize("missing", ["DOWNLOAD_CLIENT_USER", "DOWNLOAD_CLIENT_PASS"])
def test_a_missing_required_credential_is_named(missing):
    env = {k: v for k, v in NEUTRAL.items() if k != missing}
    assert missing in _message(env)


def test_a_credential_the_backend_does_not_use_is_an_error():
    message = _message(NEUTRAL | {"DOWNLOAD_CLIENT_API_KEY": "k"})
    assert "DOWNLOAD_CLIENT_API_KEY" in message
    assert "qbittorrent" in message


@pytest.mark.parametrize(
    "credential", ["DOWNLOAD_CLIENT_USER", "DOWNLOAD_CLIENT_PASS", "DOWNLOAD_CLIENT_API_KEY"]
)
def test_an_orphaned_credential_names_the_missing_url(credential):
    assert "DOWNLOAD_CLIENT_URL__X" in _message(NEUTRAL | {f"{credential}__X": "u"})


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({"ARR_URL": "http://a"}, "ARR_API_KEY"),
        ({"ARR_URL__TV": "http://a"}, "ARR_API_KEY__TV"),
        ({"ARR_API_KEY": "k"}, "ARR_URL"),
        ({"ARR_API_KEY__TV": "k"}, "ARR_URL__TV"),
    ],
)
def test_an_arr_half_configured_names_the_missing_variable(env, missing):
    assert f"{missing} missing" in _message(env)


def test_the_same_instance_name_in_both_families_does_not_cross_wire():
    """Review Focus 5: ``__TV`` on a client and on an arr are two instances."""
    cfg = load_config(
        {
            "DOWNLOAD_CLIENT_KIND__TV": "qbittorrent",
            "DOWNLOAD_CLIENT_URL__TV": "http://qtv:8080",
            "DOWNLOAD_CLIENT_USER__TV": "admin",
            "DOWNLOAD_CLIENT_PASS__TV": "pw",
            "ARR_URL__TV": "http://sonarr:8989",
            "ARR_API_KEY__TV": "tk",
        }
    )
    (client,) = cfg.clients
    (arr,) = cfg.arrs
    assert (client.name, client.url, client.username, client.password, client.api_key) == (
        "tv",
        "http://qtv:8080",
        "admin",
        "pw",
        None,
    )
    assert (arr.name, arr.url, arr.api_key) == ("tv", "http://sonarr:8989", "tk")


def test_client_repr_masks_secrets_and_omits_the_username():
    cfg = load_config(NEUTRAL)
    text = repr(cfg.clients[0])
    assert "hunter2" not in text
    assert "admin" not in text
    assert "qbittorrent" in text
    assert "http://q:8080" in text


_CLEARED = {
    k: None
    for k in os.environ
    if k.startswith(("QBIT_", "SONARR_", "RADARR_", "LINTARR_", "DOWNLOAD_CLIENT_", "ARR_"))
}


@pytest.mark.parametrize(
    ("env", "named"),
    [
        ({"QBIT_URL": "http://q:8080"}, "rename QBIT_URL to DOWNLOAD_CLIENT_URL"),
        (
            {"SONARR_URL__ANIME": "http://s:8989"},
            "rename SONARR_URL__ANIME to ARR_URL__SONARR_ANIME",
        ),
    ],
)
@pytest.mark.parametrize("command", ["check", "dump-facts"])
def test_legacy_names_through_the_cli_are_a_usage_error(env, named, command):
    result = CliRunner().invoke(cli, [command], env=_CLEARED | env)
    assert result.exit_code == 2, result.output
    assert named in result.output
    assert "Traceback" not in result.output
