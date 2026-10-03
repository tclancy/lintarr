"""The WebUI ban axiom, measured rather than documented. Issue #8.

Measured against a disposable qBittorrent 5.2.4 (WebAPI 2.15.1) container on
2026-10-02. Full run and every control in
``docs/measurements/2026-10-02-qbittorrent-5.2.4-webui-ban.md``; the harness
that produced it is ``tools/probe_qbt_ban.py``.

    login, correct password        204, empty
    login, wrong password          401, "Unauthorized"            (12 bytes)
    login, unknown username        401, "Unauthorized"            identical
    login, correct pw, bad Host    401, "Unauthorized"            identical
    login while banned             403, "Your IP address has been banned
                                        after too many failed authentication
                                        attempts."                (78 bytes)
    any other path while banned    403, "Forbidden"               (9 bytes)
    unauthenticated, not banned    403, "Forbidden"               identical

The fixtures below are those bytes. The two 401 rows and the two 9-byte 403
rows are indistinguishable *by measurement*, so the tests assert they stay
indistinguishable rather than pretending otherwise — see
``test_plain_forbidden_is_not_reported_as_a_ban``.
"""

import httpx
import pytest

from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.collect.qbittorrent import AUTH_PATH, authenticate
from lintarr.config import QbtConfig

CFG = QbtConfig(name="main", url="http://qbt", username="admin", password="pw")

BAN_BODY = "Your IP address has been banned after too many failed authentication attempts."


def _client(handler) -> ReadOnlyClient:
    return ReadOnlyClient("http://qbt", transport=httpx.MockTransport(handler), auth_path=AUTH_PATH)


def _authenticate_against(response: httpx.Response) -> ServiceError:
    with pytest.raises(ServiceError) as e:
        authenticate(_client(lambda r: response), CFG)
    return e.value


def test_measured_ban_response_is_reported_as_banned():
    """The exact 78 bytes qBittorrent 5.2.4 answers a banned IP with."""
    exc = _authenticate_against(httpx.Response(403, text=BAN_BODY))
    assert exc.kind == "banned"


def test_ban_is_reported_even_though_the_password_may_have_been_correct():
    """Measured: the ban refusal is byte-identical for a right and a wrong password.

    This is the whole reason ``banned`` is a separate kind. The operator's fix
    is to wait or restart; a new password cannot help, and telling them their
    credentials were rejected sends them to change a setting that is fine.
    """
    exc = _authenticate_against(httpx.Response(403, text=BAN_BODY))
    assert exc.kind == "banned"
    assert "credentials are not the fix" in exc.detail


def test_ban_detail_names_a_recovery_the_operator_can_perform():
    """A ban clears on time or on restart — both measured, both actionable."""
    exc = _authenticate_against(httpx.Response(403, text=BAN_BODY))
    assert "BanDuration" in exc.detail
    assert "restart" in exc.detail


def test_plain_forbidden_is_not_reported_as_a_ban():
    """403 "Forbidden" is how an *unauthenticated* request is refused.

    Guessing that any 403 meant a ban is the #7 defect: it reported an
    hour-long ban that did not exist. The body is the only thing separating
    the two, so a 403 without the ban body must stay ``unauthorised``.
    """
    exc = _authenticate_against(httpx.Response(403, text="Forbidden"))
    assert exc.kind == "unauthorised"
    assert "no IP ban is in force" in exc.detail


def test_401_unauthorized_is_not_reported_as_a_ban():
    """The #7 regression guard: a wrong password is a 401, never a ban."""
    exc = _authenticate_against(httpx.Response(401, text="Unauthorized"))
    assert exc.kind == "unauthorised"


def test_ban_body_without_the_ban_status_is_not_a_ban():
    """The status half of the predicate is load-bearing, not decoration.

    A 200 carrying that sentence is something in front of qBittorrent talking
    — a proxy, a captive portal — not qBittorrent refusing a login. Matching
    on the body alone would let such a page manufacture a ``banned`` verdict
    and send the operator to wait out an hour that was never imposed.
    """
    authenticate(_client(lambda r: httpx.Response(200, text=BAN_BODY)), CFG)


def test_ban_status_without_the_ban_body_is_not_a_ban():
    """And the body half is load-bearing too — this is the pair to the above."""
    assert _authenticate_against(httpx.Response(403, text="")).kind == "unauthorised"


def test_ban_marker_tolerates_a_reworded_tail():
    """Deliberate tolerance: the leading clause is what names the condition.

    Pinning all 78 bytes would turn a cosmetic upstream reword into a silent
    loss of the ``banned`` kind, which fails in the direction that misleads
    the operator.
    """
    reworded = "Your IP address has been banned. Try again in 3600 seconds."
    assert _authenticate_against(httpx.Response(403, text=reworded)).kind == "banned"


def test_ban_marker_is_case_insensitive():
    assert _authenticate_against(httpx.Response(403, text=BAN_BODY.upper())).kind == "banned"


def test_ban_detail_does_not_leak_the_password():
    exc = _authenticate_against(httpx.Response(403, text=BAN_BODY))
    assert CFG.password not in str(exc)
    assert CFG.password not in exc.body


def test_exactly_one_login_attempt_is_made_against_a_ban():
    """Retrying into a ban is what extends it — measured, every attempt re-logs."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403, text=BAN_BODY)

    with pytest.raises(ServiceError):
        authenticate(_client(handler), CFG)
    assert len(calls) == 1


def test_an_undecodable_ban_body_still_classifies():
    """The body is read off the error path, so it must not be able to raise.

    A declared-but-absent multibyte charset raises ``UnicodeDecodeError`` out
    of ``.text``. On the error path that would discard the status — the fact we
    came for — and surface as a ``bad-response`` or escape entirely.
    """
    exc = _authenticate_against(
        httpx.Response(
            403,
            content=BAN_BODY.encode(),
            headers={"content-type": "text/plain; charset=utf-16"},
        )
    )
    assert exc.kind == "banned"


def test_a_ban_reaches_the_error_report_as_banned():
    """End to end: the measured response must arrive at the outcome layer.

    ``ErrorKind`` has declared ``banned`` since P0a with nothing producing it.
    This closes the loop from wire bytes to the row the operator reads, which
    no test above does — ``authenticate`` raising the right kind is necessary
    and not sufficient, because ``collect_stack`` is what records it.
    """
    from lintarr.collect.stack import collect_stack
    from lintarr.config import load_config

    env = {"QBIT_URL": "http://qbt:8080", "QBIT_USER": "admin", "QBIT_PASS": "pw"}

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == AUTH_PATH, "collection must stop at the ban"
        return httpx.Response(403, text=BAN_BODY)

    facts = collect_stack(load_config(env), transport=httpx.MockTransport(handle))
    assert facts.qbits == ()
    assert facts.errors == (("qbittorrent[main]", "banned"),)
