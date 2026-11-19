"""A fake backend proves the seam: registration is the only edit a new kind needs."""

import dataclasses
import json

import httpx
import pytest
from click.testing import CliRunner

from lintarr.cli import _render_findings, cli
from lintarr.clients import BACKENDS
from lintarr.clients.qbittorrent import queue_liveness
from lintarr.clients.qbittorrent.therefore import THEREFORE
from lintarr.config import load_config
from lintarr.outcomes import Finding, Outcome
from tests.fakes.backend import FAKE
from tests.test_check_cli import _CLEARED, WEDGED_PREFS, _transport

FAKE_ENV = {
    "DOWNLOAD_CLIENT_KIND": "fake",
    "DOWNLOAD_CLIENT_URL": "http://fake",
    "DOWNLOAD_CLIENT_API_KEY": "k",
}


def _check(env, transport=None):
    result = CliRunner().invoke(
        cli,
        ["check", "--json"],
        env={**_CLEARED, **env},
        obj={"transport": transport or _transport(WEDGED_PREFS)},
    )
    return result, json.loads(result.output)["findings"]


def test_a_registered_fake_loads(fake_backend):
    (client,) = load_config(FAKE_ENV).clients
    assert client.kind == "fake"


def test_the_fake_without_its_key_is_a_config_error(fake_backend):
    env = {k: v for k, v in FAKE_ENV.items() if k != "DOWNLOAD_CLIENT_API_KEY"}
    with pytest.raises(ValueError):
        load_config(env)


def test_a_credential_the_fake_does_not_use_is_rejected(fake_backend):
    env = FAKE_ENV | {"DOWNLOAD_CLIENT_USER": "u"}
    with pytest.raises(ValueError, match="DOWNLOAD_CLIENT_USER") as exc:
        load_config(env)
    assert "fake" in str(exc.value)


def test_a_fake_instance_yields_its_own_check_and_no_queue_liveness(fake_backend):
    result, findings = _check(FAKE_ENV)
    assert result.exit_code == 0
    assert [(f["invariant"], f["instance"]) for f in findings] == [("fake-check", "fake[main]")]


def test_a_broken_fake_fails_its_check(fake_backend):
    result, findings = _check(FAKE_ENV | {"DOWNLOAD_CLIENT_URL": "http://fake/broken"})
    assert result.exit_code == 1
    assert findings[0]["outcome"] == "FAIL"


def test_shared_checks_run_on_every_kind_and_own_checks_only_on_their_kind(
    fake_backend, shared_probe
):
    env = {
        "DOWNLOAD_CLIENT_KIND__A": "fake",
        "DOWNLOAD_CLIENT_URL__A": "http://fake",
        "DOWNLOAD_CLIENT_API_KEY__A": "k",
        "DOWNLOAD_CLIENT_KIND__B": "qbittorrent",
        "DOWNLOAD_CLIENT_URL__B": "http://qbt:8080",
        "DOWNLOAD_CLIENT_USER__B": "admin",
        "DOWNLOAD_CLIENT_PASS__B": "pw",
        "ARR_URL": "http://sonarr:8989",
        "ARR_API_KEY": "sk",
    }
    _, findings = _check(env)
    pairs = {(f["invariant"], f["instance"]) for f in findings}
    assert {p for p in pairs if p[0] == "shared-probe"} == {
        ("shared-probe", "fake[a]"),
        ("shared-probe", "qbittorrent[b]"),
    }
    assert {p for p in pairs if p[0] == "fake-check"} == {("fake-check", "fake[a]")}
    assert {p for p in pairs if p[0] == "queue-liveness"} == {("queue-liveness", "qbittorrent[b]")}


def test_an_unreachable_fake_is_an_error_and_exit_two(fake_backend):
    result, findings = _check(FAKE_ENV | {"DOWNLOAD_CLIENT_URL": "http://fake/down"})
    assert result.exit_code == 2
    assert [(f["instance"], f["outcome"]) for f in findings] == [("fake[main]", "ERROR")]


def test_without_registration_the_fake_is_an_unknown_kind():
    env = FAKE_ENV
    with pytest.raises(ValueError, match="fake"):
        load_config(env)


def test_the_fake_never_touches_the_network(fake_backend):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request {request.url}")

    result, _ = _check(FAKE_ENV, transport=httpx.MockTransport(refuse))
    assert result.exit_code == 0


#: A qBittorrent ``therefore`` key, reused by the fake with its own text.
_SHARED_KEY = (queue_liveness.INVARIANT_ID, queue_liveness.SEEDING)
_FAKE_THEREFORE = "the fake's own remedy, not qBittorrent's."


@pytest.fixture
def fake_with_therefore(monkeypatch):
    fake = dataclasses.replace(FAKE, therefore={_SHARED_KEY: _FAKE_THEREFORE})
    monkeypatch.setitem(BACKENDS, "fake", fake)


def _therefore_line(instance: str) -> str | None:
    finding = Finding(
        invariant=_SHARED_KEY[0],
        instance=instance,
        outcome=Outcome.FAIL,
        conflict=_SHARED_KEY[1],
    )
    rendered = _render_findings([finding])
    if "Therefore: " not in rendered:
        return None
    return rendered.split("Therefore: ", 1)[1].split("\n\n", 1)[0]


def test_a_therefore_line_comes_from_the_producing_backend_only(fake_with_therefore):
    """Two backends may both name a check ``queue-liveness`` with a ``seeding``
    conflict. Each finding must render its own backend's remedy: a union
    lookup gave the fake qBittorrent's advice, and its own line never showed."""
    assert _therefore_line("fake[main]") == _FAKE_THEREFORE
    assert _therefore_line("qbittorrent[main]") == THEREFORE[_SHARED_KEY]


def test_a_label_whose_kind_is_not_a_backend_gets_no_therefore_line(fake_with_therefore):
    assert _therefore_line("arr[main]") is None
    assert _therefore_line("nosuch[main]") is None
