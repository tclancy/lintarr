import httpx
import pytest

from lintarr.collect.http import ReadOnlyClient, ReadOnlyViolation, ServiceError


def _client(handler, **kw) -> ReadOnlyClient:
    return ReadOnlyClient("http://svc", transport=httpx.MockTransport(handler), **kw)


def test_get_json_returns_payload():
    c = _client(lambda r: httpx.Response(200, json={"a": 1}))
    assert c.get_json("/x") == {"a": 1}


def test_records_methods_used():
    c = _client(lambda r: httpx.Response(200, json={}))
    c.get_json("/x")
    assert c.methods_used == ("GET",)


def test_post_auth_rejected_when_path_not_allowlisted():
    c = _client(lambda r: httpx.Response(200), auth_path="/api/v2/auth/login")
    with pytest.raises(ReadOnlyViolation):
        c.post_auth("/api/v2/torrents/delete", {})


def test_post_auth_allowed_on_the_one_permitted_path():
    c = _client(lambda r: httpx.Response(200, text="Ok."), auth_path="/api/v2/auth/login")
    assert c.post_auth("/api/v2/auth/login", {"username": "u"}).text == "Ok."
    assert c.methods_used == ("POST",)


def test_connect_failure_is_unreachable():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ServiceError) as e:
        _client(boom).get_json("/x")
    assert e.value.kind == "unreachable"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_status_is_unauthorised(status):
    with pytest.raises(ServiceError) as e:
        _client(lambda r: httpx.Response(status)).get_json("/x")
    assert e.value.kind == "unauthorised"


def test_non_json_body_is_bad_response():
    with pytest.raises(ServiceError) as e:
        _client(lambda r: httpx.Response(200, text="not json")).get_json("/x")
    assert e.value.kind == "bad-response"


def test_headers_are_sent_with_requests():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={})

    c = ReadOnlyClient(
        "http://svc",
        transport=httpx.MockTransport(handler),
        headers={"X-Api-Key": "secret-key"},
    )
    c.get_json("/x")
    assert seen["x-api-key"] == "secret-key"


def test_send_rejects_mutating_verbs():
    c = _client(lambda r: httpx.Response(200, json={}))
    for verb in ("DELETE", "PUT", "PATCH"):
        with pytest.raises(ReadOnlyViolation):
            c._send(verb, "/api/v2/torrents/delete")


def test_underlying_httpx_client_is_not_casually_reachable():
    c = _client(lambda r: httpx.Response(200, json={}))
    assert not hasattr(c, "_client")


def test_rejected_mutating_verb_is_not_recorded_in_methods_used():
    c = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ReadOnlyViolation):
        c._send("DELETE", "/api/v2/torrents/delete")
    assert c.methods_used == ()
