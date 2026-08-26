"""qBittorrent adapter.

Authentication is a POST by protocol, so it uses the client's single
allow-listed carve-out. It is attempted exactly once per run: qBittorrent bans
an IP after WebUI\\MaxAuthenticationFailCount failures (default 3, ban 3600s),
so a retry loop would eventually lock lintarr out of the stack it is checking.
"""

from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.config import QbtConfig
from lintarr.facts import read
from lintarr.models import QbtInstance

AUTH_PATH = "/api/v2/auth/login"


def authenticate(client: ReadOnlyClient, cfg: QbtConfig) -> None:
    """Log in once. Never retries — see module docstring.

    qBittorrent 5.2.3, measured live on 2026-08-26, does not follow its own
    documented login protocol:

        correct password:   HTTP 204, empty body
        wrong password:     HTTP 401, body "Unauthorized"
        unauthenticated GET: HTTP 403, body "Forbidden"

    Older releases are documented (and believed, not yet independently
    measured) to still return HTTP 200 with body "Ok." on success and
    HTTP 200 with body "Fails." on bad credentials. Both generations are
    handled below.

    ``ReadOnlyClient._send`` raises ``ServiceError("unauthorised")`` for
    both 401 and 403 before this function ever sees the response, so both
    arrive here as that exception, not as an httpx.Response.

    The real "you are banned" response shape has NOT been measured against
    a live instance — see issue #7. Guessing one mapping already produced
    the exact defect this fix corrects (401 misreported as a ban), so
    nothing is mapped to "banned" here. A 403 is reported as
    "unauthorised" with a note that its shape is unverified, rather than
    labelled with a kind nobody has confirmed.
    """
    try:
        response = client.post_auth(AUTH_PATH, {"username": cfg.username, "password": cfg.password})
    except ServiceError as exc:
        if exc.kind == "unauthorised" and "403" in exc.detail:
            raise ServiceError(
                "unauthorised",
                "qBittorrent refused login with HTTP 403 — this may mean an IP "
                "ban is active, but the ban response shape has not been "
                "measured against a live instance (see issue #7), so it is "
                "reported as unauthorised rather than guessed at",
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
