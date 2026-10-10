"""qBittorrent adapter.

Authentication is a POST by protocol, so it uses the client's single
allow-listed carve-out. It is attempted exactly once per run: qBittorrent bans
an IP for ``WebUI\\BanDuration`` once it has seen
``WebUI\\MaxAuthenticationFailCount`` *consecutive* failures from it, so a
retry loop would eventually lock lintarr out of the stack it is checking.

Measured defaults, qBittorrent 5.2.4 (see
``docs/measurements/2026-10-02-qbittorrent-5.2.4-webui-ban.md``):
``MaxAuthenticationFailCount`` is **5**, not 3, and ``BanDuration`` is 3600s.
The ban engages on the *next* attempt after the fifth failure — the fifth is
still answered as an ordinary refusal. A successful login resets the counter,
and restarting qBittorrent clears an active ban. Since this adapter attempts
login exactly once, the budget is five consecutive *runs* that fail to
authenticate, not five attempts inside one run — and one success anywhere in
that sequence spends none of it. Nor does every failed login spend it: a
``Host``-header port mismatch is refused 401 without incrementing the counter
at all (issue #17), so the one misconfiguration most likely to fail every run
forever is also the one that cannot ban anybody.
"""

from lintarr.collect.http import ReadOnlyClient, ServiceError, decode_text
from lintarr.config import QbtConfig
from lintarr.facts import read
from lintarr.models import QbtInstance

AUTH_PATH = "/api/v2/auth/login"

# qBittorrent 5.2.4 answers a banned IP's login attempt with exactly:
#   "Your IP address has been banned after too many failed authentication attempts."
# Matched as a case-insensitive prefix rather than in full: the leading clause
# is the part that names the condition, and pinning the trailing wording would
# turn a cosmetic upstream reword into a silent loss of the `banned` kind.
# Narrow enough to be safe because it is only ever consulted for a 403 on the
# login path — see authenticate().
_BAN_BODY_MARKER = "your ip address has been banned"


def _is_ban_refusal(exc: ServiceError) -> bool:
    """True iff *exc* is qBittorrent's measured WebUI ban refusal.

    Both halves are load-bearing. The status alone is not enough — an ordinary
    unauthenticated 403 shares it — and the body alone is not enough either,
    because a 200 carrying that sentence is some proxy's doing and not
    qBittorrent refusing a login.
    """
    return exc.status == 403 and _BAN_BODY_MARKER in exc.body.lower()


# Both causes of a login 401, in one sentence, because the response cannot
# separate them (issue #17). Measured on 5.2.4: a request whose ``Host`` header
# port differs from ``WebUI\\Port`` is refused 401 ``Unauthorized`` with
# *correct* credentials, identical to a wrong password on status, reason phrase,
# body, content-type and the absence of ``WWW-Authenticate`` — rows A4 and A6 of
# ``docs/measurements/2026-10-02-qbittorrent-5.2.4-webui-ban.md``. The only
# difference is in ``GET /api/v2/log/main``, which needs a session lintarr does
# not have yet, so naming both is the whole of what is available.
#
# One line, not a wrapped literal — for the surface this is *going* to reach.
# Today ``collect_stack`` records ``(label, kind)`` and drops the detail, so this
# sentence reaches a debugger and nothing else; carrying it through to the
# operator is issue #18 (PR #21, open and not a code dependency of this change).
# Once that lands, ``run.py`` folds the detail into a one-line ``Finding.detail``
# that ``check --json`` also emits, and ``_render_findings`` indents it with a
# bare two spaces, so an embedded newline would render unindented in one surface
# and leak into the other.
_LOGIN_401_DETAIL = (
    "qBittorrent refused the login with HTTP 401, and that refusal has two "
    "causes it does not distinguish: either the username or password is wrong, "
    "or the port in QBIT_URL (QBIT_URL__<NAME> for a second instance) is not "
    "the port the WebUI listens on "
    "(WebUI\\Port) — a reverse proxy in front of it, or a container published "
    "as -p 18080:8080 — which fails qBittorrent's host-header validation and is "
    "refused 401 even when the credentials are correct. Rule the port out "
    "first, but do not leave this running while you do: if the port is the "
    "cause, a host-header refusal does not count towards "
    "WebUI\\MaxAuthenticationFailCount, so retrying will neither ban this IP "
    "nor start working — if the credentials are, five consecutive failed runs "
    "will ban it"
)


def authenticate(client: ReadOnlyClient, cfg: QbtConfig) -> None:
    """Log in once. Never retries — see module docstring.

    qBittorrent does not follow its own documented login protocol. Measured
    live against 5.2.3 on 2026-08-26 and re-measured against 5.2.4 (WebAPI
    2.15.1) on 2026-10-02, both agreeing:

        correct password:    HTTP 204, empty body, sets QBT_SID_<port>
        wrong password:      HTTP 401, body "Unauthorized"
        unknown username:    HTTP 401, body "Unauthorized"  (identical)
        login while banned:  HTTP 403, body "Your IP address has been banned
                             after too many failed authentication attempts."
        unauthenticated GET: HTTP 403, body "Forbidden"

    Older releases are documented (and believed, not yet independently
    measured) to still return HTTP 200 with body "Ok." on success and
    HTTP 200 with body "Fails." on bad credentials. Both generations are
    handled below.

    ``ReadOnlyClient._send`` raises ``ServiceError("unauthorised")`` for
    both 401 and 403 before this function ever sees the response, so both
    arrive here as that exception, not as an httpx.Response. It carries
    ``status`` and ``body`` for exactly this classification.

    **The ban is distinguishable, so ``banned`` has a real producer** (issue
    #8). It is distinguishable on the login path *only*: a non-login request
    from a banned IP answers HTTP 403 "Forbidden", byte-identical to an
    ordinary unauthenticated request, and that is why the only ban check lives
    here. The refusal is also identical whether the password sent was right or
    wrong, which is what makes it a separate fix for the operator — waiting or
    restarting, never a new password.

    **A 401 is not distinguishable, and is reported as both causes** (issue
    #17). A request whose ``Host`` header port differs from ``WebUI\\Port`` is
    refused with HTTP 401 "Unauthorized", byte-for-byte an ordinary bad
    password, even when the credentials are correct. Nothing here can tell the
    two apart and nothing here tries; what it does instead is name both in
    ``_LOGIN_401_DETAIL``, which is the only thing that helps the operator who
    is actually stuck. The 401 branch exists *only* to replace ``_send``'s
    ``"/api/v2/auth/login: HTTP 401"`` with that sentence — it classifies
    nothing, and the ``unauthorised`` kind is unchanged.
    """
    try:
        response = client.post_auth(AUTH_PATH, {"username": cfg.username, "password": cfg.password})
    except ServiceError as exc:
        if _is_ban_refusal(exc):
            raise ServiceError(
                "banned",
                "this IP has been banned after too many consecutive failed "
                "logins — qBittorrent's own measured refusal, unless something "
                "in front of it is answering 403 with the same wording. The "
                "credentials are not the fix: wait out WebUI\\BanDuration "
                "(default 3600s) or restart qBittorrent, which clears a "
                "qBittorrent-side ban immediately",
                status=exc.status,
                body=exc.body,
            ) from exc
        if exc.status == 403:
            raise ServiceError(
                "unauthorised",
                "qBittorrent refused login with HTTP 403 but not with its ban "
                "body, so no IP ban is in force — on a measured 5.2.x that is "
                "how an unauthenticated request is refused",
                status=exc.status,
                body=exc.body,
            ) from exc
        if exc.status == 401:
            raise ServiceError(
                "unauthorised", _LOGIN_401_DETAIL, status=exc.status, body=exc.body
            ) from exc
        raise
    # _send() already raises ServiceError for any status >= 400, so a
    # response reaching here is always a 2xx — the modern 204 with an empty
    # body, the legacy 200 with body "Ok.", or the legacy 200 with body
    # "Fails.", which is qBittorrent's old-protocol way of saying no.
    #
    # This message keeps its flat claim, and the evidence is better than "we did
    # not look" — but weaker than a row in the axiom table, so here is its
    # provenance. Under a remapped publish *every* request came back 401,
    # "login or not": that is the exploratory encounter behind the measurement
    # doc's "Re-measuring" note, not one of its A1-D4 rows, and
    # ``tools/probe_qbt_ban.py`` cannot reproduce the non-login half — under
    # ``-p 18080:8080`` it exits in ``assert_same_instance`` at the outside
    # login, before issuing anything else. Taking it at face value, host-header
    # validation fires ahead of any login handling, so a 200 "Fails." body
    # cannot be the host-header shape on a build that shares that path. What is
    # flatly unmeasured is whether a release old enough to still speak 200
    # "Fails." shares it, and lintarr has no such instance to point at. So:
    # narrow here, not widened on a guess about a response nobody has seen.
    if response.status_code == 200 and decode_text(response, AUTH_PATH).strip() == "Fails.":
        raise ServiceError("unauthorised", "qBittorrent rejected the credentials")


_PREFS = "/api/v2/app/preferences"
_CATEGORIES = "/api/v2/torrents/categories"

_PREF_KEYS = (
    "queueing_enabled",
    "max_active_downloads",
    "max_active_uploads",
    "max_active_torrents",
    "dont_count_slow_torrents",
    "max_ratio_enabled",
    "max_ratio",
    "max_ratio_act",
    "max_seeding_time_enabled",
    "max_seeding_time",
    # The third release gate. `appcontroller.cpp` derives it as
    # `globalMaxInactiveSeedingMinutes() >= 0`, which is exactly the condition
    # `processTorrentShareLimits`' third arm fires on, so the flag alone is the
    # whole predicate and the paired `max_inactive_seeding_time` value would be
    # a fact nothing reads — the defect lintarr#32 was filed about.
    "max_inactive_seeding_time_enabled",
)


_VERSION = "/api/v2/app/version"


def _read_version(client: ReadOnlyClient) -> str:
    """Read the WebUI version, or fail — never substitute an empty string.

    Mirrors the arr adapter deliberately: an unparseable version is ERROR in
    both, with no unexplained seam between the two adapters.
    """
    version = client.get_text(_VERSION).strip()
    if not version:
        raise ServiceError("bad-response", f"{_VERSION}: empty version string")
    return version


def _json_object(client: ReadOnlyClient, path: str) -> dict[str, object]:
    """Fetch *path* and insist the body is a JSON object.

    A 200 carrying a list or a bare string (a proxy's error page rendered as
    JSON, say) would otherwise flow into ``read()`` and raise out of the
    adapter, aborting every other service's collection.
    """
    payload = client.get_json(path)
    if not isinstance(payload, dict):
        raise ServiceError("bad-response", f"{path}: expected a JSON object")
    return payload


def collect_qbt(client: ReadOnlyClient, cfg: QbtConfig) -> QbtInstance:
    """Authenticate once, then read version, preferences and categories."""
    authenticate(client, cfg)
    version = _read_version(client)
    prefs = _json_object(client, _PREFS)
    facts = {k: read(prefs, k, source=f"GET {_PREFS}", version=version) for k in _PREF_KEYS}
    categories = read(
        {"categories": _json_object(client, _CATEGORIES)},
        "categories",
        source=f"GET {_CATEGORIES}",
        version=version,
    )
    return QbtInstance(name=cfg.name, version=version, categories=categories, **facts)
