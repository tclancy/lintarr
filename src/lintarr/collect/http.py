"""A deliberately crippled HTTP client.

lintarr holds every credential in the stack, so it must not be *able* to
mutate it. This client exposes GET only, plus one allow-listed auth POST for
qBittorrent's login, which is a POST by protocol.
"""

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


def decode_text(response: httpx.Response, path: str) -> str:
    """Decode a response body to text, or raise ``ServiceError``.

    Lifted out of ``get_text`` because the qBittorrent auth path reads
    ``.text`` off a ``post_auth`` response directly, and the width of this
    guard belongs in one place rather than retyped at each call site.

    ``.text`` decodes with ``errors="replace"``, so an *undeclared* bad
    encoding comes back as replacement characters rather than raising. What
    does raise is a **declared** multibyte charset whose BOM is missing:
    ``content-type: text/plain; charset=utf-16`` over the plain ASCII bytes
    ``b"v5.2.3"`` raises ``UnicodeDecodeError`` before any adapter sees it.
    That is a `ValueError`, and uncaught it aborts the whole run — worse than
    the ``get_json`` case it mirrors, because the qBittorrent version read is
    the *first* instance ``collect_stack`` visits.
    """
    try:
        return response.text
    except ValueError as exc:
        raise ServiceError(
            "bad-response", f"{path}: body is not decodable text ({type(exc).__name__})"
        ) from exc


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
        if response.status_code in (401, 403):
            raise ServiceError("unauthorised", f"{path}: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ServiceError("bad-response", f"{path}: HTTP {response.status_code}")
        return response

    def get_text(self, path: str) -> str:
        return decode_text(self._send("GET", path), path)

    def get_json(self, path: str) -> Any:
        """Parse a response body as JSON, or raise ``ServiceError``.

        ``ValueError`` is a deliberate width, not a careless one: a body that
        is not JSON fails in two ways and both are ``bad-response``.
        ``json.JSONDecodeError`` when the bytes decode to text that is not
        JSON, and ``UnicodeDecodeError`` when they do not decode to text at
        all — a 200 carrying binary under a JSON content-type, which is what a
        reverse proxy or captive portal in front of an arr answers with.
        ``RecursionError`` is named separately because it is *not* a
        ``ValueError``: ``json.loads`` raises it on a deeply nested body, and
        it reaches ``collect_stack`` by the same route.

        Anything uncaught here escapes ``collect_stack``'s ``except
        ServiceError`` and aborts every *healthy* instance's collection too,
        which is the one thing that module promises will not happen.
        """
        response = self._send("GET", path)
        try:
            return response.json()
        except (ValueError, RecursionError) as exc:
            raise ServiceError(
                "bad-response", f"{path}: body is not JSON ({type(exc).__name__})"
            ) from exc

    def post_auth(self, path: str, data: dict[str, str]) -> httpx.Response:
        """The single permitted mutating verb: qBittorrent's login.

        The same check runs again inside ``_send``. That duplication is
        deliberate defence in depth: neither layer may be assumed to be the
        only one standing.
        """
        if self._auth_path is None or path != self._auth_path:
            raise ReadOnlyViolation(f"POST to {path!r} is not the allow-listed auth path")
        return self._send("POST", path, data=data)
