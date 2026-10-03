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
that sequence spends none of it.
"""

from lintarr.collect.http import ReadOnlyClient, ServiceError
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

    What is *not* distinguishable, and is not claimed to be: a request whose
    ``Host`` header port differs from ``WebUI\\Port`` is refused with HTTP 401
    "Unauthorized", byte-for-byte an ordinary bad password, even when the
    credentials are correct. It does not increment the failure counter. See
    issue #17 — nothing here can tell the two apart, so nothing here tries.
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
        raise
    # _send() already raises ServiceError for any status >= 400, so a
    # response reaching here is always a 2xx — the modern 204 with an empty
    # body, the legacy 200 with body "Ok.", or the legacy 200 with body
    # "Fails.", which is qBittorrent's old-protocol way of saying no.
    if response.status_code == 200 and response.text.strip() == "Fails.":
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
