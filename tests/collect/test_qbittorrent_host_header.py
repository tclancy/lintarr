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

    This is a self-check on a literal in this file, and that is its whole scope:
    it catches an edit to ``UNAUTHORIZED``, not upstream rewording a release
    note away from it. ``BAN_BODY`` is imported rather than retyped because the
    ban suite owns a named constant for it; the 401 row has no such owner — the
    other two suites spell it inline — so promoting it means editing PR #19's
    files, which this branch is stacked on and should not reshape.
    """
    body, status = UNAUTHORIZED
    assert status == 401
    assert body == "Unauthorized"
    assert len(body.encode()) == 12


def test_the_401_still_reports_the_unauthorised_kind():
    """The kind is the half every other layer matches on, so it must not move.

    The kind is surfaced verbatim in two places — ``run.py``'s
    ``f"could not read this service: {kind}"`` and ``cli.py``'s ``_to_dict``,
    which publishes it under its own ``"kind"`` JSON key — and the ban suite
    matches on it for equality. Issue #17 is a *detail* defect; widening it into
    a kind change would be a silent break in surfaces this ticket never looked
    at. (``run.py``'s ``_attempted`` splits the *label*, not the kind; an
    earlier draft of this docstring cited it, wrongly.)
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


#: The detail's final clause is its last conditional's consequence —
#: "if the credentials are, five consecutive failed runs will ban it", 64
#: characters. The bound is that plus a reword's worth of slack, and
#: deliberately far below the ~95 characters the shortest measured appended
#: verdict needed. Raise it only after re-reading what now follows the hedge.
_MAX_CHARS_AFTER_LAST_CONDITIONAL = 100


def test_the_401_does_not_claim_the_two_causes_are_distinguishable():
    """Criterion 2 of #17, as a positive assertion rather than an absence.

    Asserting only that the detail omits some wrong phrasing would pass on a
    detail that said nothing at all. This asserts the disclaimer is present and
    attached to the two causes.
    """
    detail = _refuse_401().detail.lower()
    assert "two causes" in detail
    assert "does not distinguish" in detail

    # Presence assertions alone are satisfied by a detail that leads with the
    # flat wrong answer and concedes the ambiguity in a trailing footnote —
    # measured, and it passed every other assertion in this file.
    #
    # The two guards below do different jobs, and NEITHER is "ban the flat
    # claim outright", which an earlier version of this comment claimed. No
    # assertion can be, because the claim has unbounded phrasings.
    #
    # 1. A denylist of the spellings actually measured to slip through. The
    #    first entry is also exactly what the legacy ``200 "Fails."`` path
    #    emits, so pinning that one is a real, targeted guard rather than a
    #    guess; the rest are the phrasings a review round got past the
    #    presence assertions above. A synonym nobody has written will pass,
    #    and that is a stated limit, not an oversight.
    for verdict in (
        "rejected the credentials",
        "the credentials are the cause",
        "rotate the password",
        "the port almost never",
    ):
        assert verdict not in detail, f"unhedged verdict in the 401 detail: {verdict!r}"

    # 2. A structural guard, and the one that kills an APPENDED verdict
    #    whatever words it uses: the detail must END at its final
    #    conditional's consequence, so there is no room after the last
    #    hedge for an unhedged sentence.
    #
    #    This replaced a term-based version of the same idea ("neither cause
    #    may be named after the last conditional"), which was a denylist
    #    wearing a structural costume: a measured survivor avoided it just by
    #    saying "login" and "QBIT_PASS" instead of "password" and "port".
    #    A length bound cannot be dodged by synonym, which is the whole point.
    #
    #    Limit: this bounds the tail, it does not read it. A reword that keeps
    #    the length and flips the meaning still needs guard 1. Raising the
    #    bound is a deliberate come-and-look-at-this, not a formality.
    tail = detail[detail.rindex("if ") :]
    assert len(tail) <= _MAX_CHARS_AFTER_LAST_CONDITIONAL, (
        f"the 401 detail runs {len(tail)} chars past its last conditional "
        f"(bound {_MAX_CHARS_AFTER_LAST_CONDITIONAL}): {tail!r}. Anything "
        f"appended after the final hedge reads as a verdict on which cause "
        f"applies — which is the one thing #17 says this message cannot do."
    )
    assert detail.index("two causes") < detail.index("username or password")


#: URLs that must all produce the same sentence. The **URL** set is the
#: load-bearing half of the identity test below, not the credentials: the
#: credentials are the one input the production code provably never reads, while
#: the configured port is the input issue #17's own option 2 invites a
#: maintainer to branch on ("lintarr knows the port in QBIT_URL"). A guesser
#: keyed on the port passes a credentials-only identity test — measured: it left
#: the suite fully green, which is how this list came to exist.
EQUIVALENT_URLS = (
    "http://qbt:8081",  # an explicit non-default port
    "http://nas:8080",  # qBittorrent's own default, i.e. "looks fine"
    "http://qbt",  # no port at all
    "https://qbt.example.com",  # the reverse-proxy case, implicit 443
)


def test_identical_responses_produce_identical_details():
    """The guard against a future change that guesses.

    Rows A4 and A6 are the same bytes. lintarr sees a response, not a cause, so
    every one of them must produce the same sentence — whatever the credentials
    were, and *whatever the configured URL looks like*. The URL cases are the
    ones that bite: ``https://qbt.example.com`` is the reverse proxy the detail
    itself names as a cause, and a branch that only mentioned the port when the
    port looked unusual would answer "rejected the credentials" there, which is
    #17 verbatim.
    """
    body, status = UNAUTHORIZED

    def refuse(cfg: QbtConfig) -> str:
        c = ReadOnlyClient(
            cfg.url,
            transport=httpx.MockTransport(lambda r: httpx.Response(status, text=body)),
            auth_path=AUTH_PATH,
        )
        with pytest.raises(ServiceError) as excinfo:
            authenticate(c, cfg)
        return excinfo.value.detail

    details = {
        url: refuse(QbtConfig(name="main", url=url, username="admin", password="hunter2"))
        for url in EQUIVALENT_URLS
    }
    # Asserted against the first URL rather than via set(...) == 1: a set
    # comparison names no URL when it fails, and the whole point is which one
    # diverged. The loop is over a non-empty literal tuple, so there is no
    # vacuous-pass shape here.
    assert len(details) == len(EQUIVALENT_URLS)
    for url, detail in details.items():
        assert detail == details[EQUIVALENT_URLS[0]], f"{url} produced a different sentence"

    # And the credentials half, which is cheap to keep.
    other_credentials = refuse(
        QbtConfig(
            name="main",
            url=EQUIVALENT_URLS[0],
            username="operator",
            password="theRealOneAndItIsLonger",
        )
    )
    assert other_credentials == details[EQUIVALENT_URLS[0]]


def test_the_401_detail_carries_no_newline():
    """Pinned for the surface this detail is *going* to reach, not one it reaches today.

    On this branch ``collect_stack`` records ``(label, kind)`` and drops the
    detail, so the sentence currently reaches a debugger and nothing else;
    carrying it through is issue #18 (PR #21, open). Once that lands, the detail
    is folded into a one-line ``Finding.detail`` which ``check --json`` also
    emits, and ``cli.py``'s ``_render_findings`` indents with a bare two spaces —
    so an embedded newline would render unindented in one surface and leak a raw
    newline into the other. Pinned now because the sentence is the longest detail
    in the codebase and a later reflow into a multi-line literal is the likely
    edit.
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
