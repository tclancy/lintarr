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

from lintarr.collect.http import _ERROR_BODY_PEEK, ReadOnlyClient, ServiceError
from lintarr.collect.qbittorrent import AUTH_PATH, authenticate
from lintarr.config import QbtConfig

SENTINEL_PASSWORD = "s3ntinel-appears-in-no-prose"
CFG = QbtConfig(name="main", url="http://qbt", username="admin", password=SENTINEL_PASSWORD)

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


def test_the_ban_error_carries_the_evidence_it_was_classified_on():
    """Re-raising must not drop the status and body that justified the verdict.

    A mutation round zeroed both on the re-raise and the whole suite stayed
    green: forwarding them was the right instinct and entirely unverified code.
    """
    exc = _authenticate_against(httpx.Response(403, text=BAN_BODY))
    assert (exc.status, exc.body) == (403, BAN_BODY)


def test_the_peek_bound_cannot_truncate_the_ban_marker():
    """The diagnostic bound must never be able to cost a classification.

    Every other test here computes its fixture *from* ``_ERROR_BODY_PEEK``, so
    they prove truncation happens at the constant and never that the constant
    is big enough. Measured: the suite survives ``_ERROR_BODY_PEEK = 31``,
    which is exactly ``len(_BAN_BODY_MARKER)`` — on the cliff with no margin.
    This is the only assertion that ties the bound to the body it must admit.
    """
    assert _ERROR_BODY_PEEK >= len(BAN_BODY.encode())


def test_ban_is_reported_even_though_the_password_may_have_been_correct():
    """Measured: the ban refusal is byte-identical for a right and a wrong password.

    This is the whole reason ``banned`` is a separate kind. The operator's fix
    is to wait or restart; a new password cannot help, and telling them their
    credentials were rejected sends them to change a setting that is fine.
    """
    exc = _authenticate_against(httpx.Response(403, text=BAN_BODY))
    assert exc.kind == "banned"
    assert "credentials are not the fix" in exc.detail


def test_ban_detail_names_a_recovery_for_whoever_reads_the_detail():
    """A ban clears on time or on restart — both measured, both actionable.

    Note what this does not claim: ``collect_stack`` records ``(label, kind)``
    and drops ``detail``, so today this text reaches a traceback and a debugger
    and not the CLI's ``ERROR qbittorrent[main]: banned`` line. The *kind* is
    the part that reaches the operator, and that is the part that matters. See
    issue #18 for carrying the detail through.
    """
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


@pytest.mark.parametrize("status,kind", [(401, "unauthorised"), (502, "bad-response")])
def test_the_ban_body_under_another_error_status_is_not_a_ban(status, kind):
    """The status half of the predicate is load-bearing, not decoration.

    A mutation round proved the earlier version of this test could not say so:
    it used a **200**, which never raises, so the predicate was never reached
    and dropping the status check left it green. An absence assertion has to
    reach the state it names. These statuses do raise, so they are the inputs
    that actually distinguish the two predicates.

    What this does *not* buy: a fronting proxy quoting that sentence would most
    likely answer **403** too, so the predicate cannot screen out the realistic
    shape of that confound — only one that picks a different status. Nothing in
    the measurement offers a further signal to narrow on (no
    ``WWW-Authenticate``, no ``Retry-After``, ``text/plain`` throughout), and a
    403 carrying that clause does mean *some* IP ban is in force, so ``banned``
    is still the more useful kind to land on. The detail hedges accordingly
    rather than promising the ban is qBittorrent's own.
    """
    assert _authenticate_against(httpx.Response(status, text=BAN_BODY)).kind == kind


def test_a_2xx_carrying_the_ban_body_still_authenticates():
    """And a *successful* response is not reinterpreted by the ban check either.

    Kept separate from the parametrized rows above because it asserts something
    different: not "a different kind", but that ``authenticate`` returns at all.
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


def test_a_ban_body_declaring_a_multibyte_charset_still_classifies():
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


def test_a_ban_body_with_undecodable_bytes_appended_still_classifies():
    """The row above is valid UTF-8, so it proves less than it looks like.

    qBittorrent would not send this, but the peek's own truncation can split a
    character and produce it, and a proxy can append anything. The marker is
    ASCII and sits at the front, so replacement characters further along must
    not cost the classification.
    """
    exc = _authenticate_against(httpx.Response(403, content=BAN_BODY.encode() + b"\xff\xfe"))
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
