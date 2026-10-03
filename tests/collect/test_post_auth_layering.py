"""Each layer of the read-only allow-list, proved on its own.

lintarr holds every credential in the stack, so the guarantee that it *cannot*
mutate anything is the project's load-bearing claim. It is enforced twice on
purpose — once in ``post_auth``, once in ``_send`` — and issue #6 recorded that
the outer one is unverifiable through the public surface: ``_send``'s check
fires first, so deleting ``post_auth``'s kills no test. Measured on main:
removing it leaves all 236 tests green.

"Defence in depth" means neither layer may be *assumed* to be the one standing.
A test that only ever observes the pair cannot tell two live layers from one
live layer and one that stopped working, which is the whole of what depth buys.
So these stub ``_send`` out and ask ``post_auth`` alone.

Kept out of ``test_http.py`` deliberately: that file is in flight on open PR
#15, and a residuals fix should not land a conflict in it.
"""

import httpx
import pytest

from lintarr.collect.http import ReadOnlyClient, ReadOnlyViolation
from lintarr.collect.qbittorrent import AUTH_PATH

_MUTATING_POSTS = (
    "/api/v2/torrents/delete",
    "/api/v2/torrents/pause",
    "/api/v2/app/setPreferences",
    # A prefix of the allow-listed path, in case the check ever becomes a
    # ``startswith``: the allow-list is an equality test and must stay one.
    "/api/v2/auth",
    "/api/v2/auth/login/../../torrents/delete",
)


def _client(*, auth_path: str | None = AUTH_PATH) -> ReadOnlyClient:
    """A client whose transport refuses everything.

    Belt and braces: if a test's stub ever fails to intercept, the request
    fails loudly instead of silently reaching a real socket.
    """

    def handle(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"a request escaped the allow-list: {request.method} {request.url}")

    return ReadOnlyClient(
        "http://qbt:8080", transport=httpx.MockTransport(handle), auth_path=auth_path
    )


@pytest.fixture
def recorded_send(monkeypatch):
    """Replace ``_send`` with a recorder, so only ``post_auth`` is under test.

    The stub returns a sentinel rather than raising. That is the point: with
    the inner layer neutralised, a ``post_auth`` that has stopped checking
    *succeeds*, which is what makes the assertion below fail loudly instead of
    passing for the wrong reason.
    """

    def make(client: ReadOnlyClient) -> list[tuple]:
        calls: list[tuple] = []

        def _send(method, path, **kw):
            calls.append((method, path, kw))
            return "sent"

        monkeypatch.setattr(client, "_send", _send)
        return calls

    return make


@pytest.mark.parametrize("path", _MUTATING_POSTS)
def test_post_auth_refuses_a_path_without_consulting_the_inner_layer(path, recorded_send):
    client = _client()
    calls = recorded_send(client)
    with pytest.raises(ReadOnlyViolation):
        client.post_auth(path, {"hashes": "all"})
    assert calls == [], f"post_auth passed {path!r} down to _send"


def test_post_auth_still_lets_the_allow_listed_path_through(recorded_send):
    """The control. Without it, a ``post_auth`` that raised on *everything*
    would satisfy every assertion above while breaking login outright."""
    client = _client()
    calls = recorded_send(client)
    assert client.post_auth(AUTH_PATH, {"username": "u", "password": "p"}) == "sent"
    assert calls == [("POST", AUTH_PATH, {"data": {"username": "u", "password": "p"}})]


def test_a_client_with_no_auth_path_permits_no_post_at_all(recorded_send):
    """An arr client is built without ``auth_path``, so it has no carve-out.

    ``self._auth_path is None`` is a separate clause from the path comparison
    and needs its own case: ``None != path`` would be true anyway, so a mutant
    dropping the ``is None`` test survives on the paths above alone.
    """
    client = _client(auth_path=None)
    calls = recorded_send(client)
    with pytest.raises(ReadOnlyViolation):
        client.post_auth(AUTH_PATH, {"username": "u", "password": "p"})
    assert calls == []


def test_the_inner_layer_also_refuses_on_its_own():
    """The other half of the depth claim, from the other side.

    ``post_auth`` is bypassed entirely here — ``_send`` is called directly, as
    an adapter holding the client could. Together with the tests above this is
    what establishes that there are two live layers rather than one.

    Takes no ``recorded_send``, and that is the point: stubbing ``_send`` is
    exactly what this test must not do. ``test_http.py`` asserts the same
    refusal; the duplication is deliberate, because the claim here is
    *relative* — layer two is live **while** layer one is neutralised in the
    tests above — and a reader checking that claim should not have to go to
    another file to find the other half of it.
    """
    client = _client()
    with pytest.raises(ReadOnlyViolation):
        client._send("POST", "/api/v2/torrents/delete", data={"hashes": "all"})
    assert client.methods_used == (), "a refused request must not be recorded as sent"


def test_the_inner_layer_refuses_a_verb_that_is_neither_get_nor_post():
    client = _client()
    with pytest.raises(ReadOnlyViolation):
        client._send("DELETE", "/api/v2/torrents/delete")
