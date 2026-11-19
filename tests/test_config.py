import pytest

from lintarr.config import load_config

QBT = {
    "DOWNLOAD_CLIENT_KIND": "qbittorrent",
    "DOWNLOAD_CLIENT_URL": "http://q:8080",
    "DOWNLOAD_CLIENT_USER": "u",
    "DOWNLOAD_CLIENT_PASS": "p",
}


def test_single_instance_of_each():
    cfg = load_config(
        {
            "DOWNLOAD_CLIENT_KIND": "qbittorrent",
            "DOWNLOAD_CLIENT_URL": "http://gluetun:8080",
            "DOWNLOAD_CLIENT_USER": "admin",
            "DOWNLOAD_CLIENT_PASS": "s3cret",
            "ARR_URL": "http://sonarr:8989",
            "ARR_API_KEY": "abc",
        }
    )
    assert [(q.name, q.kind) for q in cfg.clients] == [("main", "qbittorrent")]
    assert cfg.clients[0].url == "http://gluetun:8080"
    assert [(a.name, a.url) for a in cfg.arrs] == [("main", "http://sonarr:8989")]


def test_named_extra_instances():
    cfg = load_config(
        {
            "ARR_URL": "http://sonarr:8989",
            "ARR_API_KEY": "abc",
            "ARR_URL__ANIME": "http://anime:8989",
            "ARR_API_KEY__ANIME": "def",
        }
    )
    # The full triple, not just the names: crossing the wiring so every arr
    # got the first instance's url and api_key would satisfy a names-only
    # assertion while pointing both instances at the same service.
    assert sorted((a.name, a.url, a.api_key) for a in cfg.arrs) == [
        ("anime", "http://anime:8989", "def"),
        ("main", "http://sonarr:8989", "abc"),
    ]


def test_no_declaration_is_none_so_it_can_be_derived_after_collection():
    """The default set needs detected arr kinds; see ``run.declared_services``."""
    cfg = load_config(QBT)
    assert cfg.declared_explicit is None


def test_declared_can_be_set_explicitly():
    cfg = load_config(
        {
            **QBT,
            "LINTARR_SERVICES": "qbittorrent,sonarr",
        }
    )
    assert cfg.declared_explicit == frozenset({"qbittorrent", "sonarr"})


def test_url_without_credentials_is_an_error():
    with pytest.raises(ValueError, match="ARR_API_KEY"):
        load_config({"ARR_URL": "http://sonarr:8989"})


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({"DOWNLOAD_CLIENT_USER": "u", "DOWNLOAD_CLIENT_PASS": "p"}, "DOWNLOAD_CLIENT_URL"),
        ({"DOWNLOAD_CLIENT_USER": "u"}, "DOWNLOAD_CLIENT_URL"),
        ({"DOWNLOAD_CLIENT_PASS": "p"}, "DOWNLOAD_CLIENT_URL"),
        (
            {"DOWNLOAD_CLIENT_USER__VPN": "u", "DOWNLOAD_CLIENT_PASS__VPN": "p"},
            "DOWNLOAD_CLIENT_URL__VPN",
        ),
        ({"ARR_API_KEY": "abc"}, "ARR_URL"),
        ({"ARR_API_KEY__ANIME": "abc"}, "ARR_URL__ANIME"),
    ],
)
def test_credentials_without_a_url_are_an_error(env, missing):
    """One typo in a URL variable must not produce a clean run over an unchecked service.

    ``DOWNLOAD_CLIENT_URLL`` + credentials used to yield no instance *and* no declared
    service, so nothing was collected and no SKIP was ever raised.
    """
    with pytest.raises(ValueError, match=f"{missing} missing"):
        load_config(env)


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        (
            {"DOWNLOAD_CLIENT_URLL": "http://q:8080", "DOWNLOAD_CLIENT_USER": "u"},
            "DOWNLOAD_CLIENT_URL",
        ),
        # The suffixed typo. "DOWNLOAD_CLIENT_URL" is a prefix of
        # "DOWNLOAD_CLIENT_URL__VPN", so a
        # bare substring check cannot tell the right variable from a longer one
        # sharing its prefix — but measured, these cases buy no mutation
        # coverage the parametrised sibling above does not already have: the
        # typo'd keys are inert (neither `_instances` nor `_credential_suffixes`
        # matches "DOWNLOAD_CLIENT_URLL__VPN"), so they traverse the same code path with
        # extra unread variables. Kept for the scenario, not for the coverage.
        (
            {"DOWNLOAD_CLIENT_URLL__VPN": "http://q:8080", "DOWNLOAD_CLIENT_USER__VPN": "u"},
            "DOWNLOAD_CLIENT_URL__VPN",
        ),
        (
            {"ARR_URLL__ANIME": "http://s:8989", "ARR_API_KEY__ANIME": "k"},
            "ARR_URL__ANIME",
        ),
    ],
)
def test_orphaned_credential_error_names_the_missing_variable(env, missing):
    """A typo'd URL variable must be named exactly, suffix and all.

    Distinct from ``test_credentials_without_a_url_are_an_error`` above: there
    the URL variable is absent entirely, here a misspelt one is *present*, so
    these exercise the path where the operator has something to go and fix.

    The assertion is ``f"{missing} missing"`` rather than ``missing in ...`` to
    match its parametrised sibling, and that consistency is the whole of what
    the change buys. ``"DOWNLOAD_CLIENT_URL" in message`` is satisfied by any
    message mentioning ``DOWNLOAD_CLIENT_URL__VPN``, so the trailing " missing" is what supplies
    the word boundary — but the sibling already asserts that for every suffixed
    variable, so no mutant survives here that did not survive there.

    (#6 recorded this as weak because ``"DOWNLOAD_CLIENT_URL"`` is a substring
    of the typo'd ``DOWNLOAD_CLIENT_URLL``. Measured: the typo'd name never appears in the
    message at all, so that is not the hole. The hole is the prefix of a
    *suffixed* sibling, which the second case above is here to close.)
    """
    with pytest.raises(ValueError, match=f"{missing} missing"):
        load_config(env)


def test_password_not_in_repr():
    cfg = load_config(QBT | {"DOWNLOAD_CLIENT_PASS": "hunter2"})
    assert "hunter2" not in repr(cfg)
