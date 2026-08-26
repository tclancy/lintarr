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
    """Log in once. Never retries — see module docstring."""
    try:
        response = client.post_auth(AUTH_PATH, {"username": cfg.username, "password": cfg.password})
    except ServiceError as exc:
        # Only 403 means banned. A 401 on this path is characteristically a
        # reverse proxy in front of qBittorrent (Authelia forward-auth is
        # common in this exact stack) refusing the request before qBittorrent
        # ever sees it. Reporting that as a ban states a falsehood as fact and
        # sends the user to wait out an hour that would never expire.
        if exc.kind == "unauthorised" and "403" in exc.detail:
            raise ServiceError(
                "banned",
                "qBittorrent refused login with HTTP 403 — the IP is most likely "
                "banned for repeated failures; it clears after WebUI\\BanDuration "
                "(default 3600s)",
            ) from exc
        raise
    if response.text.strip() != "Ok.":
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
