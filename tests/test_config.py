import pytest

from lintarr.config import load_config


def test_single_instance_of_each():
    cfg = load_config(
        {
            "QBIT_URL": "http://gluetun:8080",
            "QBIT_USER": "admin",
            "QBIT_PASS": "s3cret",
            "SONARR_URL": "http://sonarr:8989",
            "SONARR_API_KEY": "abc",
        }
    )
    assert [q.name for q in cfg.qbits] == ["main"]
    assert cfg.qbits[0].url == "http://gluetun:8080"
    assert [(a.name, a.kind) for a in cfg.arrs] == [("main", "sonarr")]


def test_named_extra_instances():
    cfg = load_config(
        {
            "SONARR_URL": "http://sonarr:8989",
            "SONARR_API_KEY": "abc",
            "SONARR_URL__ANIME": "http://anime:8989",
            "SONARR_API_KEY__ANIME": "def",
        }
    )
    # The full triple, not just the names: crossing the wiring so every arr
    # got the first instance's url and api_key would satisfy a names-only
    # assertion while pointing both instances at the same service.
    assert sorted((a.name, a.url, a.api_key) for a in cfg.arrs) == [
        ("anime", "http://anime:8989", "def"),
        ("main", "http://sonarr:8989", "abc"),
    ]


def test_declared_defaults_to_what_is_configured():
    cfg = load_config({"QBIT_URL": "http://q:8080", "QBIT_USER": "u", "QBIT_PASS": "p"})
    assert cfg.declared == frozenset({"qbittorrent"})


def test_declared_can_be_set_explicitly():
    cfg = load_config(
        {
            "QBIT_URL": "http://q:8080",
            "QBIT_USER": "u",
            "QBIT_PASS": "p",
            "LINTARR_SERVICES": "qbittorrent,sonarr",
        }
    )
    assert cfg.declared == frozenset({"qbittorrent", "sonarr"})


def test_url_without_credentials_is_an_error():
    with pytest.raises(ValueError, match="SONARR_API_KEY"):
        load_config({"SONARR_URL": "http://sonarr:8989"})


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({"QBIT_USER": "u", "QBIT_PASS": "p"}, "QBIT_URL"),
        ({"QBIT_USER": "u"}, "QBIT_URL"),
        ({"QBIT_PASS": "p"}, "QBIT_URL"),
        ({"QBIT_USER__VPN": "u", "QBIT_PASS__VPN": "p"}, "QBIT_URL__VPN"),
        ({"SONARR_API_KEY": "abc"}, "SONARR_URL"),
        ({"SONARR_API_KEY__ANIME": "abc"}, "SONARR_URL__ANIME"),
        ({"RADARR_API_KEY": "abc"}, "RADARR_URL"),
        ({"RADARR_API_KEY__4K": "abc"}, "RADARR_URL__4K"),
    ],
)
def test_credentials_without_a_url_are_an_error(env, missing):
    """One typo in a URL variable must not produce a clean run over an unchecked service.

    ``QBIT_URLL`` + credentials used to yield no instance *and* no declared
    service, so nothing was collected and no SKIP was ever raised.
    """
    with pytest.raises(ValueError, match=f"{missing} missing"):
        load_config(env)


def test_orphaned_credential_error_names_the_missing_variable():
    with pytest.raises(ValueError) as e:
        load_config({"QBIT_URLL": "http://q:8080", "QBIT_USER": "u", "QBIT_PASS": "p"})
    assert "QBIT_URL" in str(e.value)


def test_password_not_in_repr():
    cfg = load_config({"QBIT_URL": "http://q:8080", "QBIT_USER": "u", "QBIT_PASS": "hunter2"})
    assert "hunter2" not in repr(cfg)
