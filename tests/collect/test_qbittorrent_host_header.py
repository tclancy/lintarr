"""Issue #17 — a ``Host``-header port mismatch is a 401, byte-identical to a bad password.

Measured on a disposable qBittorrent 5.2.4 (WebAPI 2.15.1) on 2026-10-02 and
recorded in ``docs/measurements/2026-10-02-qbittorrent-5.2.4-webui-ban.md``,
rows A4 and A6: with ``web_ui_host_header_validation_enabled`` on — which is the
default, and which ``web_ui_domain_list = '*'`` does **not** turn off for the
port check — a request whose ``Host`` header port differs from ``WebUI\\Port``
is refused

    HTTP 401, reason "Unauthorized", body ``Unauthorized``, 12 bytes

*with correct credentials*, identical to row A4's wrong password on status,
reason phrase, body, ``content-type``, and the absence of ``WWW-Authenticate``
and ``Retry-After``. The one observable difference lives inside the box, in
``GET /api/v2/log/main``, which lintarr cannot read before it has logged in.

So these tests do not pin a detection — there is none to pin. They pin a
**diagnosis**: that both causes are named, and that nothing claims to tell them
apart. The negative half is the load-bearing half. A later change that sniffs
at a 401 and picks one cause would pass every positive assertion here and
reintroduce the defect, which is why
``test_identical_responses_produce_identical_details`` asserts on the pair
rather than on either member.
"""

import httpx
import pytest

from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.collect.qbittorrent import AUTH_PATH, authenticate
from lintarr.config import QbtConfig
from tests.collect.test_qbittorrent_ban import BAN_BODY

CFG = QbtConfig(name="main", url="http://qbt:8081", username="admin", password="hunter2")

#: Row A4/A6 off the wire. Both rows are this response.
UNAUTHORIZED = ("Unauthorized", 401)


def _client(handler) -> ReadOnlyClient:
    return ReadOnlyClient(
        "http://qbt:8081", transport=httpx.MockTransport(handler), auth_path=AUTH_PATH
    )


def _refuse_401() -> ServiceError:
    """Drive ``authenticate`` into the measured 401 and hand back what it raised."""
    body, status = UNAUTHORIZED
    c = _client(lambda r: httpx.Response(status, text=body))
    with pytest.raises(ServiceError) as excinfo:
        authenticate(c, CFG)
    return excinfo.value


def test_the_fixture_is_the_response_that_was_measured():
    """Pin the fixture to rows A4/A6, not merely to "a 401".

    Everything else in this file is downstream of ``UNAUTHORIZED`` being the
    bytes qBittorrent actually sends. A fixture of ``httpx.Response(401)`` with
    no body would satisfy every assertion below and would not be the measured
    state — the whole defect is that the body is *identical* between a wrong
    password and a correct one behind a port mismatch, and a fixture with no
    body cannot represent that. 12 bytes is the measured length of both rows.
    """
    body, status = UNAUTHORIZED
    assert status == 401
    assert body == "Unauthorized"
    assert len(body.encode()) == 12


def test_the_401_still_reports_the_unauthorised_kind():
    """The kind is the half every other layer matches on, so it must not move.

    ``run.py``'s ``_attempted`` and the ``check --json`` consumers compare the
    kind for equality. Issue #17 is a *detail* defect; widening it into a kind
    change would be a silent break in surfaces this ticket never looked at.
    """
    assert _refuse_401().kind == "unauthorised"


def test_the_401_names_the_credentials_cause():
    detail = _refuse_401().detail.lower()
    assert "password" in detail
    assert "username" in detail


def test_the_401_names_the_host_header_port_cause():
    """The cause an operator cannot guess, with the config key they must check.

    ``QBIT_URL`` by name because that is the only end of the mismatch lintarr
    can see; ``WebUI\\Port`` by name because that is the other end, and the
    operator has to go read it out of qBittorrent to compare.
    """
    detail = _refuse_401().detail
    assert "QBIT_URL" in detail
    assert "WebUI\\Port" in detail


def test_the_401_does_not_claim_the_two_causes_are_distinguishable():
    """Criterion 2 of #17, as a positive assertion rather than an absence.

    Asserting only that the detail omits some wrong phrasing would pass on a
    detail that said nothing at all. This asserts the disclaimer is present and
    attached to the two causes.
    """
    detail = _refuse_401().detail.lower()
    assert "two causes" in detail
    assert "does not distinguish" in detail


def test_identical_responses_produce_identical_details():
    """The guard against a future change that guesses.

    Rows A4 and A6 are the same bytes. lintarr sees a response, not a cause, so
    the two must produce the same sentence — whatever the credentials actually
    were. A change that inspected the configured port, or the password's shape,
    or anything else at hand and then picked one cause would still satisfy every
    other test in this file; it fails here.
    """
    body, status = UNAUTHORIZED

    def refuse(cfg: QbtConfig) -> str:
        c = _client(lambda r: httpx.Response(status, text=body))
        with pytest.raises(ServiceError) as excinfo:
            authenticate(c, cfg)
        return excinfo.value.detail

    as_configured = refuse(CFG)
    other_credentials = refuse(
        QbtConfig(
            name="main",
            url="http://qbt:8081",
            username="operator",
            password="theRealOneAndItIsLonger",
        )
    )
    assert as_configured == other_credentials


def test_the_401_detail_carries_no_newline():
    """``run.py`` joins the detail into a one-line ``Finding.detail``.

    That string is emitted into ``check --json`` and indented by
    ``_render_findings`` with a bare two spaces, so an embedded newline renders
    unindented in one surface and leaks a raw newline into the other. The
    sentence is long enough — it is the longest detail in the codebase — that a
    later reflow into a multi-line literal is a live risk.
    """
    assert "\n" not in _refuse_401().detail


def test_the_password_is_absent_from_the_401_detail():
    """The detail now names a config key, so it is one edit from naming a value."""
    exc = _refuse_401()
    assert CFG.password not in exc.detail
    assert CFG.password not in str(exc)


def test_the_host_header_prose_is_absent_from_the_ban_refusal():
    """A ban is a 403. Offering the port as a candidate cause there would be wrong.

    The two refusals are measurably different responses with different fixes —
    wait or restart, versus check the port — and #17's whole complaint is a
    confident wrong answer. Pinning the prose *out* of the 403 paths is what
    keeps the fix from becoming a second instance of the defect.

    ``BAN_BODY`` is imported from the ban suite rather than retyped. A second
    copy of a measured literal is a copy that can drift: if upstream rewords the
    ban body, the two files disagree and this test starts asserting about a
    response qBittorrent no longer sends, while still passing.
    """
    c = _client(lambda r: httpx.Response(403, text=BAN_BODY))
    with pytest.raises(ServiceError) as excinfo:
        authenticate(c, CFG)
    assert excinfo.value.kind == "banned"
    assert "QBIT_URL" not in excinfo.value.detail
    assert "WebUI\\Port" not in excinfo.value.detail


def test_the_host_header_prose_is_absent_from_a_plain_forbidden():
    """403 "Forbidden" is an unauthenticated request, not a port mismatch."""
    c = _client(lambda r: httpx.Response(403, text="Forbidden"))
    with pytest.raises(ServiceError) as excinfo:
        authenticate(c, CFG)
    assert excinfo.value.kind == "unauthorised"
    assert "QBIT_URL" not in excinfo.value.detail


def test_exactly_one_login_attempt_on_a_401():
    """A host-header refusal does not spend ban budget, but a retry loop would.

    The module docstring's "five consecutive runs" budget is stated per-run
    precisely because this adapter attempts login once. That is worth pinning on
    the 401 path specifically: it is the path a well-meaning retry would most
    plausibly be added to, since the operator's credentials may be fine.
    """
    calls = []

    def handler(request):
        calls.append(request)
        body, status = UNAUTHORIZED
        return httpx.Response(status, text=body)

    with pytest.raises(ServiceError):
        authenticate(_client(handler), CFG)
    assert len(calls) == 1
