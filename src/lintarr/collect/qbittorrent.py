"""qBittorrent adapter.

Authentication is a POST by protocol, so it uses the client's single
allow-listed carve-out. It is attempted exactly once per run: qBittorrent bans
an IP after WebUI\\MaxAuthenticationFailCount failures (default 3, ban 3600s),
so a retry loop would eventually lock lintarr out of the stack it is checking.
"""

from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.config import QbtConfig

AUTH_PATH = "/api/v2/auth/login"


def authenticate(client: ReadOnlyClient, cfg: QbtConfig) -> None:
    """Log in once. Never retries — see module docstring."""
    try:
        response = client.post_auth(
            AUTH_PATH, {"username": cfg.username, "password": cfg.password}
        )
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
