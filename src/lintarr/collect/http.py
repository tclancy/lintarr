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
    def __init__(self, kind: ErrorKind, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind: ErrorKind = kind
        self.detail = detail


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
        if method not in ("GET", "POST"):
            raise ReadOnlyViolation(f"method {method!r} is not GET or POST")
        self._methods.append(method)
        try:
            response = self.__client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise ServiceError("unreachable", f"{path}: {type(exc).__name__}") from exc
        if response.status_code in (401, 403):
            raise ServiceError("unauthorised", f"{path}: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ServiceError("bad-response", f"{path}: HTTP {response.status_code}")
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
        """The single permitted mutating verb: qBittorrent's login."""
        if self._auth_path is None or path != self._auth_path:
            raise ReadOnlyViolation(f"POST to {path!r} is not the allow-listed auth path")
        return self._send("POST", path, data=data)
