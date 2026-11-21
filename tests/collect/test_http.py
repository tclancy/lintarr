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


@pytest.mark.parametrize(
    "path",
    ["/api/v2/torrents/delete", "/api/v2/torrents/pause", "/api/v2/app/setPreferences"],
)
def test_send_rejects_post_to_a_non_auth_path(path):
    """POST *is* qBittorrent's mutation verb — _send itself must refuse it."""
    c = _client(lambda r: httpx.Response(200, json={}), auth_path="/api/v2/auth/login")
    with pytest.raises(ReadOnlyViolation):
        c._send("POST", path)
    assert c.methods_used == ()


def test_send_rejects_post_when_no_auth_path_is_configured():
    c = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ReadOnlyViolation):
        c._send("POST", "/api/v2/auth/login")


def test_send_permits_post_to_the_configured_auth_path():
    c = _client(lambda r: httpx.Response(200, text="Ok."), auth_path="/api/v2/auth/login")
    assert c._send("POST", "/api/v2/auth/login", data={"username": "u"}).text == "Ok."
    assert c.methods_used == ("POST",)


def test_underlying_httpx_client_is_not_casually_reachable():
    c = _client(lambda r: httpx.Response(200, json={}))
    assert not hasattr(c, "_client")


def test_rejected_mutating_verb_is_not_recorded_in_methods_used():
    c = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ReadOnlyViolation):
        c._send("DELETE", "/api/v2/torrents/delete")
    assert c.methods_used == ()


def test_undecodable_body_is_bad_response():
    """A 200 whose bytes are not decodable text is a bad response, not a crash.

    ``response.json()`` raises ``UnicodeDecodeError`` rather than
    ``JSONDecodeError`` when the body cannot be turned into text at all, and
    only the latter used to be caught — so this escaped as an unhandled
    exception past ``collect_stack``'s ``except ServiceError``.
    """
    body = b'\xff\xfe{"version":"4.0.0"}'

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    with pytest.raises(ServiceError) as e:
        _client(handler).get_json("/x")
    assert e.value.kind == "bad-response"
    # Pin the *cause*, not just the kind. Without this the fixture's potency is
    # an unstated property of those bytes: give the BOM an even-length payload
    # (b'\xff\xfex\x00x\x00' decodes to "xx") and the body fails as a plain
    # JSONDecodeError instead, so both new tests would pass against the narrow
    # catch they exist to rule out.
    assert "UnicodeDecodeError" in e.value.detail


class _RecursionErrorOnDecode(httpx.Response):
    """A 200 whose JSON decode raises ``RecursionError``, by construction.

    The fixture this replaced provoked the error by *depth*: 20,000 nested
    lists, which raised on every interpreter the project had then run on. How
    deep CPython can nest before its stack guard fires is the interpreter's
    business and it moved. Measured here: 3.13.7 raises at 20,000 deep;
    3.14.10 parses 20,000 without complaint and raises at 100,000, with the
    recursion limit at its default 1000 in both. So the old body stopped
    raising and the test read ``DID NOT RAISE ServiceError`` — a red pre-push
    gate on a clean tree, over a guard that was never broken (#70).

    Depth was only ever a *means* of reaching ``response.json()``'s raise, so
    this raises from there directly: ``get_json``'s ``except`` is the subject,
    and this fixture reaches it on any interpreter, at any recursion limit.
    ``test_a_genuinely_deep_body_is_handled_whatever_the_interpreter_does``
    keeps the real nested body, which is a different question.
    """

    def json(self, **kwargs: object) -> object:
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON object")


def test_a_recursion_error_from_the_json_decode_is_bad_response():
    """``RecursionError`` is not a ``ValueError``, so it has to be named.

    Same escape route as the undecodable body, different base class — so
    widening the catch to ``ValueError`` does not cover it. Uncaught it goes
    straight past ``collect_stack``'s ``except ServiceError`` and aborts every
    *healthy* instance's collection.

    Drop ``RecursionError`` from the ``except`` in ``get_json`` and this test
    fails, on 3.13.7 and on 3.14.10 alike. Measured (#70).
    """

    def handler(request):
        return _RecursionErrorOnDecode(
            200, content=b"[]", headers={"content-type": "application/json"}
        )

    with pytest.raises(ServiceError) as e:
        _client(handler).get_json("/x")
    assert e.value.kind == "bad-response"
    assert "RecursionError" in e.value.detail


def _list_depth(value: object) -> int:
    """How many nested lists deep ``value`` goes — iteratively, on purpose.

    A recursive walk of a 200,000-deep list would raise the very error the
    test around this is trying to observe the absence of.
    """
    depth = 0
    while isinstance(value, list):
        depth += 1
        value = value[0] if value else None
    return depth


def test_a_genuinely_deep_body_is_handled_whatever_the_interpreter_does():
    """A real deeply nested body never escapes as an uncaught exception.

    The companion to the test above, and the half that keeps a genuine body in
    the suite. It deliberately does **not** pin which way the interpreter goes,
    because that is the thing that moved under #70: at this depth 3.13.7 and
    3.14.10 both raise, a future CPython may parse it, and either is fine. What
    must hold on all of them is that ``get_json`` returns a value or raises
    ``ServiceError`` — never ``RecursionError``, which ``collect_stack`` does
    not catch.

    Both branches assert, so neither is a free pass: the raising branch pins
    the kind, and the parsing branch pins that the body really was 200,000 deep
    and came back whole rather than truncated by a lenient parser.
    """
    depth = 200_000
    body = b"[" * depth + b"]" * depth

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    try:
        parsed = _client(handler).get_json("/x")
    except ServiceError as exc:
        assert exc.kind == "bad-response"
    else:
        assert _list_depth(parsed) == depth


def test_declared_charset_with_no_bom_is_bad_response():
    """``get_text`` has the same hole, and a *plainer* body reaches it.

    ``.text`` decodes with ``errors="replace"``, so an undeclared bad encoding
    comes back as replacement characters. A **declared** multibyte charset
    whose BOM is absent raises instead: ``charset=utf-16`` over the ASCII bytes
    ``b"v5.2.3"`` is a ``UnicodeDecodeError`` before any adapter sees it.
    """

    def handler(request):
        return httpx.Response(
            200, content=b"v5.2.3", headers={"content-type": "text/plain; charset=utf-16"}
        )

    with pytest.raises(ServiceError) as e:
        _client(handler).get_text("/api/v2/app/version")
    assert e.value.kind == "bad-response"
    assert "UnicodeDecodeError" in e.value.detail


def test_undeclared_bad_encoding_still_decodes_to_text():
    """The control for the test above: this is the case that does NOT raise.

    Without it, the guard looks like it covers every undecodable body, and the
    next reader has no way to tell which half of the behaviour was measured.
    """

    def handler(request):
        return httpx.Response(200, content=b"\xff\xfev5.2.3", headers={})

    assert _client(handler).get_text("/api/v2/app/version")
