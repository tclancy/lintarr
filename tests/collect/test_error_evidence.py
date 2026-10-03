"""``ServiceError`` carries the HTTP evidence an adapter needs to classify.

Separate from ``test_http.py`` deliberately: this is a contract about what
survives the raise, and the qBittorrent ban axiom (issue #8) is its only
current consumer. Before it existed, ``_send`` formatted the status into a
prose ``detail`` and dropped the body, so ``authenticate`` classified by
testing ``"403" in exc.detail`` — a substring of a message, which any path
containing those three characters also satisfies.
"""

import httpx
import pytest

from lintarr.collect.http import _ERROR_BODY_PEEK, ReadOnlyClient, ServiceError


def _client(handler) -> ReadOnlyClient:
    return ReadOnlyClient("http://svc", transport=httpx.MockTransport(handler))


def _get(response: httpx.Response) -> ServiceError:
    with pytest.raises(ServiceError) as e:
        _client(lambda r: response).get_text("/thing")
    return e.value


def test_status_and_body_survive_the_raise():
    exc = _get(httpx.Response(403, text="Forbidden"))
    assert exc.status == 403
    assert exc.body == "Forbidden"


def test_status_is_the_number_not_a_substring_of_the_detail():
    """A path can contain "403"; only ``status`` distinguishes the two."""
    with pytest.raises(ServiceError) as e:
        _client(lambda r: httpx.Response(500, text="boom")).get_text("/api/403/thing")
    assert e.value.status == 500
    assert "403" in e.value.detail


def test_bad_response_also_carries_its_evidence():
    exc = _get(httpx.Response(502, text="bad gateway"))
    assert exc.kind == "bad-response"
    assert (exc.status, exc.body) == (502, "bad gateway")


def test_body_is_truncated():
    """An error body ends up inside an exception message, so it is bounded."""
    exc = _get(httpx.Response(403, text="x" * (_ERROR_BODY_PEEK * 3)))
    assert len(exc.body) == _ERROR_BODY_PEEK


def test_an_undecodable_error_body_does_not_mask_the_status():
    """The status is the fact; the body is a diagnostic.

    ``.text`` raises ``UnicodeDecodeError`` on a declared multibyte charset
    with no BOM. Reading the error body that way would convert a 403 into a
    ``bad-response`` — or escape ``collect_stack``'s ``except ServiceError``
    entirely — and lose the one thing the caller needs.
    """
    exc = _get(
        httpx.Response(
            403, content=b"Forbidden", headers={"content-type": "text/plain; charset=utf-16"}
        )
    )
    assert exc.kind == "unauthorised"
    assert exc.status == 403
    assert isinstance(exc.body, str)


def test_a_connection_failure_has_no_status_and_no_body():
    """``None`` rather than ``0``: there was no response, not a response of 0."""

    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ServiceError) as e:
        _client(handler).get_text("/thing")
    assert e.value.kind == "unreachable"
    assert e.value.status is None
    assert e.value.body == ""
