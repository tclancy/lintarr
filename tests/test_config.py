from lintarr.config import load_config


def test_single_instance_of_each():
    cfg = load_config({
        "QBIT_URL": "http://gluetun:8080", "QBIT_USER": "admin", "QBIT_PASS": "s3cret",
        "SONARR_URL": "http://sonarr:8989", "SONARR_API_KEY": "abc",
    })
    assert [q.name for q in cfg.qbits] == ["main"]
    assert cfg.qbits[0].url == "http://gluetun:8080"
    assert [(a.name, a.kind) for a in cfg.arrs] == [("main", "sonarr")]


def test_named_extra_instances():
    cfg = load_config({
        "SONARR_URL": "http://sonarr:8989", "SONARR_API_KEY": "abc",
        "SONARR_URL__ANIME": "http://anime:8989", "SONARR_API_KEY__ANIME": "def",
    })
    assert sorted(a.name for a in cfg.arrs) == ["anime", "main"]


def test_declared_defaults_to_what_is_configured():
    cfg = load_config({"QBIT_URL": "http://q:8080", "QBIT_USER": "u", "QBIT_PASS": "p"})
    assert cfg.declared == frozenset({"qbittorrent"})


def test_declared_can_be_set_explicitly():
    cfg = load_config({
        "QBIT_URL": "http://q:8080", "QBIT_USER": "u", "QBIT_PASS": "p",
        "LINTARR_SERVICES": "qbittorrent,sonarr",
    })
    assert cfg.declared == frozenset({"qbittorrent", "sonarr"})


def test_url_without_credentials_is_an_error():
    import pytest
    with pytest.raises(ValueError, match="SONARR_API_KEY"):
        load_config({"SONARR_URL": "http://sonarr:8989"})


def test_password_not_in_repr():
    cfg = load_config({"QBIT_URL": "http://q:8080", "QBIT_USER": "u", "QBIT_PASS": "hunter2"})
    assert "hunter2" not in repr(cfg)
