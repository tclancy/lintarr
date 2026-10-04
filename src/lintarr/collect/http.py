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

    Deliberately *not* ``.text``, and *not* ``decode_text``: this runs on the
    error path, where the body is a diagnostic and the status is the fact we
    came for. ``.text`` raises ``UnicodeDecodeError`` on a declared-but-absent
    multibyte charset, and that is not an ``httpx.HTTPError``, so it would not
    be caught and re-kinded here — it escapes ``_send`` *and*
    ``collect_stack``'s ``except ServiceError``, aborting every healthy
    service's collection over one unreadable error page. So: raw bytes,
    truncated, decoded with ``errors="replace"``, which cannot raise.

    Truncated because the body on this path is attacker- and
    misconfiguration-shaped — a reverse proxy's HTML error page, a captive
    portal — and it is retained for as long as anything holds the exception.
    It is *not* interpolated into the exception message: ``__init__`` formats
    only ``kind`` and ``detail``, so the bound caps what is held, not what is
    printed. qBittorrent's longest measured refusal body is 78 bytes; 512
    leaves room for a wordier release without carrying a page, and
    ``test_the_peek_bound_cannot_truncate_the_ban_marker`` is what keeps it
    above the one body a caller classifies on.
    """
    return response.content[:_ERROR_BODY_PEEK].decode("utf-8", errors="replace")


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
