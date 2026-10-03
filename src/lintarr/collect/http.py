"""A deliberately crippled HTTP client.

lintarr holds every credential in the stack, so it must not be *able* to
mutate it. This client exposes GET only, plus one allow-listed auth POST for
qBittorrent's login, which is a POST by protocol.
"""

import json
from typing import Any, Literal

import httpx

type ErrorKind = Literal["unreachable", "unauthorised", "banned", "bad-response"]

_TIMEOUT = 15.0


class ReadOnlyViolation(RuntimeError):
    """An adapter attempted a mutating request."""


class ServiceError(RuntimeError):
    """A service could not be read, and *kind* says what the operator must fix.

    ``status`` and ``body`` carry the HTTP evidence forward so an adapter can
    classify on the response rather than on a substring of ``detail``. Both are
    absent for errors with no response at all — a connection failure, a
    timeout — which is why ``status`` is ``None`` rather than ``0``.
    """

    def __init__(
        self, kind: ErrorKind, detail: str, *, status: int | None = None, body: str = ""
    ) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind: ErrorKind = kind
        self.detail = detail
        self.status = status
        self.body = body


_ERROR_BODY_PEEK = 512


def peek_error_body(response: httpx.Response) -> str:
    """A bounded, never-raising look at an error response body.

    Deliberately *not* ``.text``: this runs on the error path, where the body
    is a diagnostic and the status is the fact we came for. ``.text`` raises on
    a declared-but-absent multibyte charset, which would turn a 403 into a
    ``bad-response`` and lose the status entirely. So: raw bytes, truncated,
    decoded with ``errors="replace"``.

    Truncated because the body on this path is attacker- and
    misconfiguration-shaped — a reverse proxy's HTML error page, a captive
    portal — and it ends up inside an exception message. qBittorrent's longest
    measured refusal body is 78 bytes; 512 leaves room for a wordier release
    without carrying a page.
    """
    return response.content[:_ERROR_BODY_PEEK].decode("utf-8", errors="replace")


class ReadOnlyClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        auth_path: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._auth_path = auth_path
        self._methods: list[str] = []
        self.__client = httpx.Client(
            base_url=base_url, transport=transport, timeout=_TIMEOUT, headers=headers
        )

    @property
    def methods_used(self) -> tuple[str, ...]:
        return tuple(self._methods)

    def close(self) -> None:
        self.__client.close()

    def __enter__(self) -> "ReadOnlyClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _send(self, method: Literal["GET", "POST"], path: str, **kw: Any) -> httpx.Response:
        """The single choke point every request passes through.

        The allow-list lives *here* rather than only in ``post_auth`` because
        POST is qBittorrent's mutation verb: ``/api/v2/torrents/delete``,
        ``/api/v2/torrents/pause`` and ``/api/v2/app/setPreferences`` are all
        POSTs. Enforcing at the public wrapper alone would leave ``_send``
        itself as an open mutation channel.
        """
        if method not in ("GET", "POST"):
            raise ReadOnlyViolation(f"method {method!r} is not GET or POST")
        if method == "POST" and (self._auth_path is None or path != self._auth_path):
            raise ReadOnlyViolation(f"POST to {path!r} is not the allow-listed auth path")
        self._methods.append(method)
        try:
            response = self.__client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise ServiceError("unreachable", f"{path}: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            kind: ErrorKind = (
                "unauthorised" if response.status_code in (401, 403) else "bad-response"
            )
            raise ServiceError(
                kind,
                f"{path}: HTTP {response.status_code}",
                status=response.status_code,
                body=peek_error_body(response),
            )
        return response

    def get_text(self, path: str) -> str:
        return self._send("GET", path).text

    def get_json(self, path: str) -> Any:
        response = self._send("GET", path)
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise ServiceError("bad-response", f"{path}: body is not JSON") from exc

    def post_auth(self, path: str, data: dict[str, str]) -> httpx.Response:
        """The single permitted mutating verb: qBittorrent's login.

        The same check runs again inside ``_send``. That duplication is
        deliberate defence in depth: neither layer may be assumed to be the
        only one standing.
        """
        if self._auth_path is None or path != self._auth_path:
            raise ReadOnlyViolation(f"POST to {path!r} is not the allow-listed auth path")
        return self._send("POST", path, data=data)
