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
        if exc.kind == "unauthorised":
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


def collect_qbt(client: ReadOnlyClient, cfg: QbtConfig) -> QbtInstance:
    """Authenticate once, then read version, preferences and categories."""
    authenticate(client, cfg)
    version = client.get_text("/api/v2/app/version").strip()
    prefs = client.get_json(_PREFS)
    facts = {k: read(prefs, k, source=f"GET {_PREFS}", version=version) for k in _PREF_KEYS}
    categories = read(
        {"categories": client.get_json(_CATEGORIES)},
        "categories",
        source=f"GET {_CATEGORIES}",
        version=version,
    )
    return QbtInstance(name=cfg.name, version=version, categories=categories, **facts)
