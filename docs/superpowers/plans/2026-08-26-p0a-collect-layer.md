# lintarr P0a — Collect Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read a live arr stack's configuration into a fully source-annotated `StackFacts` snapshot, where every unread field is explicitly `Unknown` rather than defaulted.

**Architecture:** Per-service adapters behind a read-only HTTP client produce immutable fact objects. A fact is a `Known[T] | Unknown` union — never a nullable value — so a genuinely-read `None` is distinguishable from a field that was never read. No invariants, no solver, no reporting: this layer only gathers and labels.

**Tech Stack:** Python 3.13, httpx, click, pytest, Hypothesis, ruff, hatchling, uv.

**Spec:** `docs/superpowers/specs/2026-08-26-lintarr-design.md`

## Global Constraints

- Python `>=3.13`. Uses PEP 695 `type` statements and generic dataclasses.
- Package name `lintarr`, MIT licence, src layout, hatchling build backend.
- Use `uv` for all dependency and run operations. Never `pip`.
- ruff `line-length = 100`, `target-version = "py313"`, lint select `["E", "F", "I"]`.
- **Adapters are read-only.** Every HTTP request must be a GET, with exactly one carve-out: qBittorrent's `POST /api/v2/auth/login`. Enforced by test, not convention.
- **Never default an unread fact.** Absence is always `Unknown` with a reason.
- **Exactly one qBittorrent login attempt per run.** This is the ban-avoidance mechanism — qBittorrent bans an IP after `WebUI\MaxAuthenticationFailCount` failures (default 3, ban 3600s).
- Credentials never appear in logs, exception messages, `repr()`, or CLI output.
- All timestamps UTC.

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/lintarr/__init__.py`
- Create: `tests/test_package.py`
- Create: `.gitignore` (already exists — verify contents)

**Interfaces:**
- Consumes: nothing
- Produces: importable `lintarr` package with `lintarr.__version__: str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_package.py
def test_package_exposes_version():
    import lintarr

    assert isinstance(lintarr.__version__, str)
    assert lintarr.__version__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_package.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr'`

- [ ] **Step 3: Write minimal implementation**

```toml
# pyproject.toml
[project]
name = "lintarr"
version = "0.1.0"
description = "Static consistency checker for the *arr stack"
readme = "README.md"
license = "MIT"
requires-python = ">=3.13"
dependencies = [
    "click>=8.1",
    "httpx>=0.27",
]

[project.scripts]
lintarr = "lintarr.cli:cli"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/lintarr"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I"]

[dependency-groups]
dev = [
    "hypothesis>=6.100",
    "pytest>=8.0",
    "ruff>=0.6",
]
```

```python
# src/lintarr/__init__.py
"""lintarr — static consistency checker for the *arr stack."""

__version__ = "0.1.0"
```

Create `README.md` with a single line so hatchling's `readme` reference resolves:

```markdown
# lintarr

Static consistency checker for the *arr stack.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_package.py -v`
Expected: PASS

Then: `uv run ruff check .`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml README.md src/lintarr/__init__.py tests/test_package.py
git commit -m "feat: scaffold lintarr package"
```

---

### Task 2: Fact types

**Files:**
- Create: `src/lintarr/facts.py`
- Create: `tests/test_facts.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Known[T]` — frozen dataclass, fields `value: T`, `source: str`, `read_at: datetime`, `service_version: str`
  - `Unknown` — frozen dataclass, fields `reason: UnknownReason`, `detail: str`
  - `type UnknownReason = Literal["service-absent", "field-absent", "insufficient-permission"]`
  - `type Fact[T] = Known[T] | Unknown`
  - `def is_known(f: Fact[T]) -> TypeGuard[Known[T]]`
  - `def read(payload: Mapping[str, Any], key: str, *, source: str, version: str) -> Fact[Any]` — returns `Known` if `key` is present (**even when its value is `None`**), else `Unknown("field-absent", ...)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_facts.py
from datetime import UTC, datetime

from lintarr.facts import Known, Unknown, is_known, read


def test_known_carries_provenance():
    f = Known(
        value=5,
        source="GET /api/v2/app/preferences",
        read_at=datetime.now(UTC),
        service_version="v5.2.3",
    )
    assert is_known(f)
    assert f.value == 5


def test_unknown_is_not_known():
    assert not is_known(Unknown(reason="field-absent", detail="max_ratio absent"))


def test_read_present_key_is_known():
    f = read({"max_ratio": 1.5}, "max_ratio", source="GET /prefs", version="v5.2.3")
    assert is_known(f)
    assert f.value == 1.5
    assert f.source == "GET /prefs"


def test_read_present_key_with_null_value_is_known_not_unknown():
    """A genuinely-read None must be distinguishable from an unread field."""
    f = read({"max_ratio": None}, "max_ratio", source="GET /prefs", version="v5.2.3")
    assert is_known(f)
    assert f.value is None


def test_read_absent_key_is_unknown_field_absent():
    f = read({}, "max_ratio", source="GET /prefs", version="v5.2.3")
    assert not is_known(f)
    assert f.reason == "field-absent"
    assert "max_ratio" in f.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_facts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr.facts'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/facts.py
"""Facts and the explicit absence of facts.

A fact is either read or unknown, never defaulted. Defaulting an unread
setting to a plausible value is how a checker produces a confident wrong
answer, so absence is represented in the type rather than as ``None``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypeGuard

type UnknownReason = Literal["service-absent", "field-absent", "insufficient-permission"]


@dataclass(frozen=True, slots=True)
class Known[T]:
    value: T
    source: str
    read_at: datetime
    service_version: str


@dataclass(frozen=True, slots=True)
class Unknown:
    reason: UnknownReason
    detail: str


type Fact[T] = Known[T] | Unknown


def is_known[T](f: Fact[T]) -> TypeGuard[Known[T]]:
    """True when *f* was actually read from a service."""
    return isinstance(f, Known)


def read(payload: Mapping[str, Any], key: str, *, source: str, version: str) -> Fact[Any]:
    """Read *key* from *payload*.

    Key present -> ``Known``, including when its value is ``None``: services
    legitimately return nulls, and that is a different thing from never having
    been read.
    """
    if key not in payload:
        return Unknown(reason="field-absent", detail=f"{key!r} absent from {source}")
    return Known(
        value=payload[key],
        source=source,
        read_at=datetime.now(UTC),
        service_version=version,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_facts.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/facts.py tests/test_facts.py
git commit -m "feat(facts): Known/Unknown union so unread fields cannot be defaulted"
```

---

### Task 3: Service configuration from environment

**Files:**
- Create: `src/lintarr/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `QbtConfig` — frozen dataclass: `name: str`, `url: str`, `username: str`, `password: str`
  - `ArrConfig` — frozen dataclass: `name: str`, `kind: Literal["sonarr", "radarr"]`, `url: str`, `api_key: str`
  - `LintarrConfig` — frozen dataclass: `qbits: tuple[QbtConfig, ...]`, `arrs: tuple[ArrConfig, ...]`, `declared: frozenset[str]`
  - `def load_config(env: Mapping[str, str]) -> LintarrConfig`

Environment shape. The unsuffixed form is the single-instance case; a
`__<NAME>` suffix declares an additional named instance.

```
QBIT_URL, QBIT_USER, QBIT_PASS
SONARR_URL, SONARR_API_KEY
SONARR_URL__ANIME, SONARR_API_KEY__ANIME
RADARR_URL, RADARR_API_KEY
LINTARR_SERVICES=qbittorrent,sonarr        # services expected to exist
```

`declared` exists so a qBittorrent-only user is not permanently non-zero: a
service absent from `LINTARR_SERVICES` produces no `SKIP` at all. When
`LINTARR_SERVICES` is unset, it defaults to whatever was actually configured.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from lintarr.config import load_config


def test_single_instance_of_each():
    cfg = load_config(
        {
            "QBIT_URL": "http://gluetun:8080",
            "QBIT_USER": "admin",
            "QBIT_PASS": "s3cret",
            "SONARR_URL": "http://sonarr:8989",
            "SONARR_API_KEY": "abc",
        }
    )
    assert [q.name for q in cfg.qbits] == ["main"]
    assert cfg.qbits[0].url == "http://gluetun:8080"
    assert [(a.name, a.kind) for a in cfg.arrs] == [("main", "sonarr")]


def test_named_extra_instances():
    cfg = load_config(
        {
            "SONARR_URL": "http://sonarr:8989",
            "SONARR_API_KEY": "abc",
            "SONARR_URL__ANIME": "http://anime:8989",
            "SONARR_API_KEY__ANIME": "def",
        }
    )
    assert sorted(a.name for a in cfg.arrs) == ["anime", "main"]


def test_declared_defaults_to_what_is_configured():
    cfg = load_config({"QBIT_URL": "http://q:8080", "QBIT_USER": "u", "QBIT_PASS": "p"})
    assert cfg.declared == frozenset({"qbittorrent"})


def test_declared_can_be_set_explicitly():
    cfg = load_config(
        {
            "QBIT_URL": "http://q:8080",
            "QBIT_USER": "u",
            "QBIT_PASS": "p",
            "LINTARR_SERVICES": "qbittorrent,sonarr",
        }
    )
    assert cfg.declared == frozenset({"qbittorrent", "sonarr"})


def test_url_without_credentials_is_an_error():
    import pytest

    with pytest.raises(ValueError, match="SONARR_API_KEY"):
        load_config({"SONARR_URL": "http://sonarr:8989"})


def test_password_not_in_repr():
    cfg = load_config({"QBIT_URL": "http://q:8080", "QBIT_USER": "u", "QBIT_PASS": "hunter2"})
    assert "hunter2" not in repr(cfg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr.config'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/config.py
"""Connection configuration, read from the environment.

API keys are required; there is no auto-discovery. See the spec's Credentials
section for why.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

type ArrKind = Literal["sonarr", "radarr"]

_SECRET = "***"


@dataclass(frozen=True, slots=True)
class QbtConfig:
    name: str
    url: str
    username: str
    password: str = field(repr=False)

    def __repr__(self) -> str:
        return f"QbtConfig(name={self.name!r}, url={self.url!r}, password={_SECRET})"


@dataclass(frozen=True, slots=True)
class ArrConfig:
    name: str
    kind: ArrKind
    url: str
    api_key: str = field(repr=False)

    def __repr__(self) -> str:
        return f"ArrConfig(name={self.name!r}, kind={self.kind!r}, url={self.url!r}, api_key={_SECRET})"


@dataclass(frozen=True, slots=True)
class LintarrConfig:
    qbits: tuple[QbtConfig, ...]
    arrs: tuple[ArrConfig, ...]
    declared: frozenset[str]


def _instances(env: Mapping[str, str], prefix: str) -> dict[str, str]:
    """Map instance name -> URL for ``<PREFIX>_URL`` and ``<PREFIX>_URL__<NAME>``."""
    out: dict[str, str] = {}
    base = f"{prefix}_URL"
    for key, value in env.items():
        if key == base:
            out["main"] = value
        elif key.startswith(f"{base}__"):
            out[key.removeprefix(f"{base}__").lower()] = value
    return out


def _suffix(name: str) -> str:
    return "" if name == "main" else f"__{name.upper()}"


def load_config(env: Mapping[str, str]) -> LintarrConfig:
    qbits = []
    for name, url in sorted(_instances(env, "QBIT").items()):
        s = _suffix(name)
        user, password = env.get(f"QBIT_USER{s}"), env.get(f"QBIT_PASS{s}")
        if user is None or password is None:
            raise ValueError(f"QBIT_URL{s} set but QBIT_USER{s}/QBIT_PASS{s} missing")
        qbits.append(QbtConfig(name=name, url=url.rstrip("/"), username=user, password=password))

    arrs = []
    for kind in ("sonarr", "radarr"):
        prefix = kind.upper()
        for name, url in sorted(_instances(env, prefix).items()):
            s = _suffix(name)
            api_key = env.get(f"{prefix}_API_KEY{s}")
            if api_key is None:
                raise ValueError(f"{prefix}_URL{s} set but {prefix}_API_KEY{s} missing")
            arrs.append(ArrConfig(name=name, kind=kind, url=url.rstrip("/"), api_key=api_key))

    configured = {"qbittorrent"} if qbits else set()
    configured |= {a.kind for a in arrs}

    raw = env.get("LINTARR_SERVICES")
    declared = (
        frozenset(s.strip().lower() for s in raw.split(",") if s.strip())
        if raw
        else frozenset(configured)
    )
    return LintarrConfig(qbits=tuple(qbits), arrs=tuple(arrs), declared=declared)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/config.py tests/test_config.py
git commit -m "feat(config): env-based multi-instance connection config"
```

---

### Task 4: Read-only HTTP client

**Files:**
- Create: `src/lintarr/collect/__init__.py`
- Create: `src/lintarr/collect/http.py`
- Create: `tests/collect/__init__.py`
- Create: `tests/collect/test_http.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `ReadOnlyViolation(RuntimeError)`
  - `ServiceError(RuntimeError)` — fields `kind: ErrorKind`, `detail: str`
  - `type ErrorKind = Literal["unreachable", "unauthorised", "banned", "bad-response"]`
  - `ReadOnlyClient` — constructed with `base_url: str`, `transport: httpx.BaseTransport | None`, `auth_path: str | None`
    - `get_json(path: str) -> Any`
    - `get_text(path: str) -> str`
    - `post_auth(path: str, data: dict[str, str]) -> httpx.Response` — raises `ReadOnlyViolation` unless `path == auth_path`
    - `methods_used: tuple[str, ...]` — every HTTP method issued, for the read-only guarantee test

The `post_auth` carve-out exists because qBittorrent's login is a POST. It is
deliberately a *separate method* rather than a general `post`, so the read-only
rule is expressed in the type surface rather than in a comment.

- [ ] **Step 1: Write the failing test**

```python
# tests/collect/test_http.py
import httpx
import pytest

from lintarr.collect.http import ReadOnlyClient, ReadOnlyViolation, ServiceError


def _client(handler, **kw) -> ReadOnlyClient:
    return ReadOnlyClient("http://svc", transport=httpx.MockTransport(handler), **kw)


def test_get_json_returns_payload():
    c = _client(lambda r: httpx.Response(200, json={"a": 1}))
    assert c.get_json("/x") == {"a": 1}


def test_records_methods_used():
    c = _client(lambda r: httpx.Response(200, json={}))
    c.get_json("/x")
    assert c.methods_used == ("GET",)


def test_post_auth_rejected_when_path_not_allowlisted():
    c = _client(lambda r: httpx.Response(200), auth_path="/api/v2/auth/login")
    with pytest.raises(ReadOnlyViolation):
        c.post_auth("/api/v2/torrents/delete", {})


def test_post_auth_allowed_on_the_one_permitted_path():
    c = _client(lambda r: httpx.Response(200, text="Ok."), auth_path="/api/v2/auth/login")
    assert c.post_auth("/api/v2/auth/login", {"username": "u"}).text == "Ok."
    assert c.methods_used == ("POST",)


def test_connect_failure_is_unreachable():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ServiceError) as e:
        _client(boom).get_json("/x")
    assert e.value.kind == "unreachable"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_status_is_unauthorised(status):
    with pytest.raises(ServiceError) as e:
        _client(lambda r: httpx.Response(status)).get_json("/x")
    assert e.value.kind == "unauthorised"


def test_non_json_body_is_bad_response():
    with pytest.raises(ServiceError) as e:
        _client(lambda r: httpx.Response(200, text="not json")).get_json("/x")
    assert e.value.kind == "bad-response"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/collect/test_http.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr.collect'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/collect/__init__.py
"""Service adapters. Read-only by construction."""
```

```python
# src/lintarr/collect/http.py
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
    ) -> None:
        self._auth_path = auth_path
        self._methods: list[str] = []
        self._client = httpx.Client(base_url=base_url, transport=transport, timeout=_TIMEOUT)

    @property
    def methods_used(self) -> tuple[str, ...]:
        return tuple(self._methods)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ReadOnlyClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _send(self, method: str, path: str, **kw: Any) -> httpx.Response:
        self._methods.append(method)
        try:
            response = self._client.request(method, path, **kw)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/collect/test_http.py -v`
Expected: PASS (8 tests, counting both parametrised auth statuses)

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/collect/ tests/collect/
git commit -m "feat(collect): GET-only HTTP client with a single auth POST carve-out"
```

---

### Task 5: qBittorrent authentication with ban avoidance

**Files:**
- Create: `src/lintarr/collect/qbittorrent.py`
- Create: `tests/collect/test_qbittorrent_auth.py`

**Interfaces:**
- Consumes: `ReadOnlyClient`, `ServiceError` (Task 4); `QbtConfig` (Task 3)
- Produces:
  - `def authenticate(client: ReadOnlyClient, cfg: QbtConfig) -> None` — raises `ServiceError` with `kind="unauthorised"` on `Fails.`, `kind="banned"` on HTTP 403
  - `AUTH_PATH: str = "/api/v2/auth/login"`

qBittorrent returns HTTP 200 with body `Ok.` on success and `Fails.` on bad
credentials; it returns 403 once the IP is banned. **`authenticate` is called
at most once per run** — that is the ban-avoidance mechanism, since the ban is
triggered by repeated failures. There is no retry loop here by design.

- [ ] **Step 1: Write the failing test**

```python
# tests/collect/test_qbittorrent_auth.py
import httpx
import pytest

from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.collect.qbittorrent import AUTH_PATH, authenticate
from lintarr.config import QbtConfig

CFG = QbtConfig(name="main", url="http://qbt", username="admin", password="pw")


def _client(handler) -> ReadOnlyClient:
    return ReadOnlyClient("http://qbt", transport=httpx.MockTransport(handler), auth_path=AUTH_PATH)


def test_ok_body_authenticates():
    c = _client(lambda r: httpx.Response(200, text="Ok."))
    authenticate(c, CFG)
    assert c.methods_used == ("POST",)


def test_fails_body_is_unauthorised_not_banned():
    c = _client(lambda r: httpx.Response(200, text="Fails."))
    with pytest.raises(ServiceError) as e:
        authenticate(c, CFG)
    assert e.value.kind == "unauthorised"


def test_forbidden_is_reported_as_banned():
    """403 on login means the IP is banned, which is a different fix to bad creds."""
    c = _client(lambda r: httpx.Response(403, text="banned"))
    with pytest.raises(ServiceError) as e:
        authenticate(c, CFG)
    assert e.value.kind == "banned"


def test_exactly_one_login_attempt_is_made_on_failure():
    """Retrying is what triggers qBittorrent's IP ban, so there must be no retry."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="Fails.")

    with pytest.raises(ServiceError):
        authenticate(_client(handler), CFG)
    assert len(calls) == 1


def test_password_absent_from_error_message():
    c = _client(lambda r: httpx.Response(200, text="Fails."))
    with pytest.raises(ServiceError) as e:
        authenticate(c, CFG)
    assert "pw" not in str(e.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/collect/test_qbittorrent_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr.collect.qbittorrent'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/collect/qbittorrent.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/collect/test_qbittorrent_auth.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/collect/qbittorrent.py tests/collect/test_qbittorrent_auth.py
git commit -m "feat(qbt): single-attempt login that distinguishes banned from bad credentials"
```

---

### Task 6: qBittorrent facts

**Files:**
- Create: `src/lintarr/models.py`
- Modify: `src/lintarr/collect/qbittorrent.py` (append `collect_qbt`)
- Create: `tests/collect/test_qbittorrent_facts.py`

**Interfaces:**
- Consumes: `Fact`, `read`, `Unknown` (Task 2); `ReadOnlyClient` (Task 4); `authenticate` (Task 5)
- Produces:
  - `QbtInstance` — frozen dataclass: `name: str`, `version: str`, plus `Fact` fields `queueing_enabled`, `max_active_downloads`, `max_active_uploads`, `max_active_torrents`, `dont_count_slow_torrents`, `max_ratio_enabled`, `max_ratio`, `max_ratio_act`, `max_seeding_time_enabled`, `max_seeding_time`, and `categories: Fact[dict[str, dict]]`
  - `def collect_qbt(client: ReadOnlyClient, cfg: QbtConfig) -> QbtInstance`

`max_active_*` values of `0` and `-1` are both legal and must survive
untransformed — `0` binds immediately, `-1` means unlimited.

- [ ] **Step 1: Write the failing test**

```python
# tests/collect/test_qbittorrent_facts.py
import httpx

from lintarr.collect.http import ReadOnlyClient
from lintarr.collect.qbittorrent import AUTH_PATH, collect_qbt
from lintarr.config import QbtConfig
from lintarr.facts import is_known

CFG = QbtConfig(name="main", url="http://qbt", username="admin", password="pw")

PREFS = {
    "queueing_enabled": True,
    "max_active_downloads": 6,
    "max_active_uploads": 3,
    "max_active_torrents": 10,
    "dont_count_slow_torrents": True,
    "max_ratio_enabled": False,
    "max_ratio": -1,
    "max_ratio_act": 0,
    "max_seeding_time_enabled": False,
    "max_seeding_time": -1,
}


def _handler(prefs=PREFS, version="v5.2.3", categories=None):
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case p if p == AUTH_PATH:
                return httpx.Response(200, text="Ok.")
            case "/api/v2/app/version":
                return httpx.Response(200, text=version)
            case "/api/v2/app/preferences":
                return httpx.Response(200, json=prefs)
            case "/api/v2/torrents/categories":
                return httpx.Response(200, json=categories or {})
            case other:
                return httpx.Response(404, text=other)

    return handle


def _collect(**kw):
    client = ReadOnlyClient(
        "http://qbt", transport=httpx.MockTransport(_handler(**kw)), auth_path=AUTH_PATH
    )
    return collect_qbt(client, CFG), client


def test_reads_all_three_active_limits():
    qbt, _ = _collect()
    assert qbt.max_active_downloads.value == 6
    assert qbt.max_active_uploads.value == 3
    assert qbt.max_active_torrents.value == 10


def test_version_is_captured():
    qbt, _ = _collect()
    assert qbt.version == "v5.2.3"


def test_zero_and_unlimited_sentinels_survive():
    prefs = PREFS | {"max_active_downloads": 0, "max_active_torrents": -1}
    qbt, _ = _collect(prefs=prefs)
    assert qbt.max_active_downloads.value == 0
    assert qbt.max_active_torrents.value == -1


def test_absent_preference_becomes_unknown_field_absent():
    prefs = {k: v for k, v in PREFS.items() if k != "dont_count_slow_torrents"}
    qbt, _ = _collect(prefs=prefs)
    assert not is_known(qbt.dont_count_slow_torrents)
    assert qbt.dont_count_slow_torrents.reason == "field-absent"


def test_only_get_after_the_single_auth_post():
    _, client = _collect()
    assert client.methods_used[0] == "POST"
    assert set(client.methods_used[1:]) == {"GET"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/collect/test_qbittorrent_facts.py -v`
Expected: FAIL — `ImportError: cannot import name 'collect_qbt'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/models.py
"""Normalised snapshot types.

Multi-instance from day one: separate 4K and anime arr instances, and multiple
download clients per arr, are the common case, and retrofitting multiplicity
after invariants exist is expensive.
"""

from dataclasses import dataclass
from typing import Any

from lintarr.facts import Fact


@dataclass(frozen=True, slots=True)
class QbtInstance:
    name: str
    version: str
    queueing_enabled: Fact[bool]
    max_active_downloads: Fact[int]
    max_active_uploads: Fact[int]
    max_active_torrents: Fact[int]
    dont_count_slow_torrents: Fact[bool]
    max_ratio_enabled: Fact[bool]
    max_ratio: Fact[float]
    max_ratio_act: Fact[int]
    max_seeding_time_enabled: Fact[bool]
    max_seeding_time: Fact[int]
    categories: Fact[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class IndexerFacts:
    name: str
    enabled: bool
    protocol: str
    seed_ratio: Fact[float]
    seed_time: Fact[int]
    season_pack_seed_time: Fact[int]


@dataclass(frozen=True, slots=True)
class ArrInstance:
    name: str
    kind: str
    version: str
    indexers: tuple[IndexerFacts, ...]


@dataclass(frozen=True, slots=True)
class StackFacts:
    qbits: tuple[QbtInstance, ...]
    arrs: tuple[ArrInstance, ...]
    errors: tuple[tuple[str, str], ...] = ()
```

Append to `src/lintarr/collect/qbittorrent.py`:

```python
from lintarr.facts import read
from lintarr.models import QbtInstance

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/collect/test_qbittorrent_facts.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/models.py src/lintarr/collect/qbittorrent.py tests/collect/test_qbittorrent_facts.py
git commit -m "feat(qbt): collect all three active limits, slow-torrent exemption and categories"
```

---

### Task 7: Arr facts — indexers and seed criteria

**Files:**
- Create: `src/lintarr/collect/arr.py`
- Create: `tests/collect/test_arr.py`

**Interfaces:**
- Consumes: `Fact`, `read`, `Unknown` (Task 2); `ReadOnlyClient` (Task 4); `ArrConfig` (Task 3); `IndexerFacts`, `ArrInstance` (Task 6)
- Produces: `def collect_arr(client: ReadOnlyClient, cfg: ArrConfig) -> ArrInstance`

**Seed criteria live on the indexer, not the download client.** Verified against
a live Sonarr: `/api/v3/downloadclient` carries no seed fields at all, while
`/api/v3/indexer` entries carry a `fields` list containing
`seedCriteria.seedRatio`, `seedCriteria.seedTime` and
`seedCriteria.seasonPackSeedTime`. A field present with value `null` means
"configured but unset", which is `Known(None)` — different from the field being
absent altogether.

The API key goes in the `X-Api-Key` header, set on the client by the caller in
Task 8; `collect_arr` itself only issues paths.

- [ ] **Step 1: Write the failing test**

```python
# tests/collect/test_arr.py
import httpx

from lintarr.collect.arr import collect_arr
from lintarr.collect.http import ReadOnlyClient
from lintarr.config import ArrConfig
from lintarr.facts import is_known

CFG = ArrConfig(name="main", kind="sonarr", url="http://sonarr", api_key="k")


def _indexer(name, *, enable=True, protocol="torrent", fields=None):
    default = [
        {"name": "minimumSeeders", "value": 1},
        {"name": "seedCriteria.seedRatio", "value": None},
        {"name": "seedCriteria.seedTime", "value": None},
        {"name": "seedCriteria.seasonPackSeedTime", "value": None},
    ]
    return {
        "name": name,
        "enable": enable,
        "protocol": protocol,
        "fields": default if fields is None else fields,
    }


def _collect(indexers, version="4.0.15.2941"):
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/api/v3/system/status":
                return httpx.Response(200, json={"version": version})
            case "/api/v3/indexer":
                return httpx.Response(200, json=indexers)
            case other:
                return httpx.Response(404, text=other)

    client = ReadOnlyClient("http://sonarr", transport=httpx.MockTransport(handle))
    return collect_arr(client, CFG), client


def test_reads_version():
    arr, _ = _collect([])
    assert arr.version == "4.0.15.2941"


def test_unset_seed_ratio_is_known_none_not_unknown():
    """Field present with a null value means configured-but-unset."""
    arr, _ = _collect([_indexer("1337x")])
    ratio = arr.indexers[0].seed_ratio
    assert is_known(ratio)
    assert ratio.value is None


def test_set_seed_ratio_is_read():
    fields = [{"name": "seedCriteria.seedRatio", "value": 2.0}]
    arr, _ = _collect([_indexer("EZTV", fields=fields)])
    assert arr.indexers[0].seed_ratio.value == 2.0


def test_missing_seed_field_entirely_is_unknown():
    arr, _ = _collect([_indexer("Old", fields=[{"name": "minimumSeeders", "value": 1}])])
    assert not is_known(arr.indexers[0].seed_ratio)
    assert arr.indexers[0].seed_ratio.reason == "field-absent"


def test_disabled_and_usenet_indexers_are_kept_with_their_flags():
    arr, _ = _collect(
        [
            _indexer("Off", enable=False),
            _indexer("News", protocol="usenet"),
        ]
    )
    assert [(i.name, i.enabled, i.protocol) for i in arr.indexers] == [
        ("Off", False, "torrent"),
        ("News", True, "usenet"),
    ]


def test_issues_only_get_requests():
    _, client = _collect([])
    assert set(client.methods_used) == {"GET"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/collect/test_arr.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr.collect.arr'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/collect/arr.py
"""Sonarr/Radarr adapter (v3 API — identical shape for both).

Seed criteria live on the *indexer*, not the download client. Verified against
a live Sonarr: /api/v3/downloadclient carries no seed fields, while each
/api/v3/indexer entry has a ``fields`` list containing
``seedCriteria.seedRatio``, ``seedCriteria.seedTime`` and
``seedCriteria.seasonPackSeedTime``.
"""

from typing import Any

from lintarr.collect.http import ReadOnlyClient
from lintarr.config import ArrConfig
from lintarr.facts import read
from lintarr.models import ArrInstance, IndexerFacts

_STATUS = "/api/v3/system/status"
_INDEXER = "/api/v3/indexer"

_SEED_FIELDS = {
    "seed_ratio": "seedCriteria.seedRatio",
    "seed_time": "seedCriteria.seedTime",
    "season_pack_seed_time": "seedCriteria.seasonPackSeedTime",
}


def _fields_as_mapping(indexer: dict[str, Any]) -> dict[str, Any]:
    """Flatten the arr ``fields`` list into ``{name: value}``.

    A name absent here means the running version does not expose it; a name
    present with ``None`` means configured-but-unset. Those are different facts.
    """
    return {f["name"]: f.get("value") for f in indexer.get("fields", [])}


def collect_arr(client: ReadOnlyClient, cfg: ArrConfig) -> ArrInstance:
    version = str(client.get_json(_STATUS).get("version", "")).strip()
    indexers = []
    for raw in client.get_json(_INDEXER):
        mapping = _fields_as_mapping(raw)
        source = f"GET {_INDEXER}[{raw.get('name')}]"
        indexers.append(
            IndexerFacts(
                name=raw.get("name", ""),
                enabled=bool(raw.get("enable", False)),
                protocol=raw.get("protocol", ""),
                **{
                    attr: read(mapping, key, source=source, version=version)
                    for attr, key in _SEED_FIELDS.items()
                },
            )
        )
    return ArrInstance(name=cfg.name, kind=cfg.kind, version=version, indexers=tuple(indexers))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/collect/test_arr.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/collect/arr.py tests/collect/test_arr.py
git commit -m "feat(arr): read per-indexer seed criteria, which is where they actually live"
```

---

### Task 8: Stack assembly and the read-only guarantee

**Files:**
- Create: `src/lintarr/collect/stack.py`
- Create: `tests/collect/test_stack.py`
- Create: `tests/test_readonly_guarantee.py`

**Interfaces:**
- Consumes: everything from Tasks 3–7
- Produces:
  - `def collect_stack(cfg: LintarrConfig, *, transport: httpx.BaseTransport | None = None) -> StackFacts`
  - `StackFacts.errors: tuple[tuple[str, str], ...]` — `(instance_label, error_kind)` for each configured-but-failing service

A configured service that cannot be reached must **not** abort the run and must
**not** be silently skipped: it is recorded in `errors` so the outcome layer can
raise `ERROR` later.

- [ ] **Step 1: Write the failing test**

```python
# tests/collect/test_stack.py
import httpx

from lintarr.collect.stack import collect_stack
from lintarr.config import load_config

ENV = {
    "QBIT_URL": "http://qbt:8080",
    "QBIT_USER": "admin",
    "QBIT_PASS": "pw",
    "SONARR_URL": "http://sonarr:8989",
    "SONARR_API_KEY": "k",
    "SONARR_URL__ANIME": "http://anime:8989",
    "SONARR_API_KEY__ANIME": "k2",
}


def _transport(sonarr_down=False):
    def handle(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "qbt":
            match path:
                case "/api/v2/auth/login":
                    return httpx.Response(200, text="Ok.")
                case "/api/v2/app/version":
                    return httpx.Response(200, text="v5.2.3")
                case "/api/v2/app/preferences":
                    return httpx.Response(200, json={"queueing_enabled": True})
                case "/api/v2/torrents/categories":
                    return httpx.Response(200, json={})
        if host == "anime" and sonarr_down:
            raise httpx.ConnectError("refused", request=request)
        match path:
            case "/api/v3/system/status":
                return httpx.Response(200, json={"version": "4.0.0"})
            case "/api/v3/indexer":
                return httpx.Response(200, json=[])
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def test_collects_every_configured_instance():
    facts = collect_stack(load_config(ENV), transport=_transport())
    assert [q.name for q in facts.qbits] == ["main"]
    assert sorted(a.name for a in facts.arrs) == ["anime", "main"]
    assert facts.errors == ()


def test_unreachable_instance_is_recorded_not_raised():
    facts = collect_stack(load_config(ENV), transport=_transport(sonarr_down=True))
    assert [a.name for a in facts.arrs] == ["main"]
    assert facts.errors == (("sonarr[anime]", "unreachable"),)
```

```python
# tests/test_readonly_guarantee.py
"""lintarr holds every credential in the stack. It must not be able to mutate it."""

import httpx

from lintarr.collect.stack import collect_stack
from lintarr.config import load_config

ENV = {
    "QBIT_URL": "http://qbt:8080",
    "QBIT_USER": "admin",
    "QBIT_PASS": "pw",
    "SONARR_URL": "http://sonarr:8989",
    "SONARR_API_KEY": "k",
}


def test_only_verb_other_than_get_is_the_qbittorrent_login():
    seen: list[tuple[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        match request.url.path:
            case "/api/v2/auth/login":
                return httpx.Response(200, text="Ok.")
            case "/api/v2/app/version":
                return httpx.Response(200, text="v5.2.3")
            case "/api/v2/app/preferences" | "/api/v3/system/status":
                return httpx.Response(200, json={"version": "4.0.0"})
            case "/api/v2/torrents/categories" | "/api/v3/indexer":
                return httpx.Response(200, json=[])
        return httpx.Response(404)

    collect_stack(load_config(ENV), transport=httpx.MockTransport(handle))

    non_get = [(m, p) for m, p in seen if m != "GET"]
    assert non_get == [("POST", "/api/v2/auth/login")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/collect/test_stack.py tests/test_readonly_guarantee.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr.collect.stack'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/collect/stack.py
"""Assemble a StackFacts snapshot from every configured instance.

One failing service must not abort the run, and must not vanish silently
either — failures are recorded so the outcome layer can report ERROR.
"""

import httpx

from lintarr.collect.arr import collect_arr
from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.collect.qbittorrent import AUTH_PATH, collect_qbt
from lintarr.config import LintarrConfig
from lintarr.models import ArrInstance, QbtInstance, StackFacts


def collect_stack(
    cfg: LintarrConfig, *, transport: httpx.BaseTransport | None = None
) -> StackFacts:
    qbits: list[QbtInstance] = []
    arrs: list[ArrInstance] = []
    errors: list[tuple[str, str]] = []

    for qbt in cfg.qbits:
        label = f"qbittorrent[{qbt.name}]"
        try:
            with ReadOnlyClient(qbt.url, transport=transport, auth_path=AUTH_PATH) as client:
                qbits.append(collect_qbt(client, qbt))
        except ServiceError as exc:
            errors.append((label, exc.kind))

    for arr in cfg.arrs:
        label = f"{arr.kind}[{arr.name}]"
        try:
            with ReadOnlyClient(arr.url, transport=transport) as client:
                client._client.headers["X-Api-Key"] = arr.api_key  # noqa: SLF001
                arrs.append(collect_arr(client, arr))
        except ServiceError as exc:
            errors.append((label, exc.kind))

    return StackFacts(qbits=tuple(qbits), arrs=tuple(arrs), errors=tuple(errors))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/collect/test_stack.py tests/test_readonly_guarantee.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/collect/stack.py tests/collect/test_stack.py tests/test_readonly_guarantee.py
git commit -m "feat(collect): assemble multi-instance StackFacts; prove the stack is read-only"
```

---

### Task 9: `dump-facts` CLI

**Files:**
- Create: `src/lintarr/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `load_config` (Task 3), `collect_stack` (Task 8)
- Produces: `cli` — click group with `dump-facts` subcommand; `--json` for machine output, human table by default

This is P0a's definition of done: a complete source-annotated snapshot in which
every unread field is visibly `Unknown` with a reason.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import json

import httpx
from click.testing import CliRunner

from lintarr.cli import cli

ENV = {
    "QBIT_URL": "http://qbt:8080",
    "QBIT_USER": "admin",
    "QBIT_PASS": "hunter2",
}


def _transport():
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/api/v2/auth/login":
                return httpx.Response(200, text="Ok.")
            case "/api/v2/app/version":
                return httpx.Response(200, text="v5.2.3")
            case "/api/v2/app/preferences":
                return httpx.Response(
                    200, json={"queueing_enabled": True, "max_active_torrents": 5}
                )
            case "/api/v2/torrents/categories":
                return httpx.Response(200, json={})
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def _run(args):
    return CliRunner().invoke(cli, args, env=ENV, obj={"transport": _transport()})


def test_dump_facts_json_marks_unread_fields_unknown():
    result = _run(["dump-facts", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    qbt = payload["qbits"][0]
    assert qbt["max_active_torrents"]["known"] is True
    assert qbt["max_active_torrents"]["value"] == 5
    assert qbt["max_ratio_enabled"]["known"] is False
    assert qbt["max_ratio_enabled"]["reason"] == "field-absent"


def test_dump_facts_records_the_source_of_every_known_fact():
    payload = json.loads(_run(["dump-facts", "--json"]).output)
    assert payload["qbits"][0]["max_active_torrents"]["source"] == "GET /api/v2/app/preferences"


def test_dump_facts_never_prints_credentials():
    assert "hunter2" not in _run(["dump-facts"]).output
    assert "hunter2" not in _run(["dump-facts", "--json"]).output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr.cli'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/cli.py
"""Command line entry point."""

import dataclasses
import json as jsonlib
import os
from typing import Any

import click

from lintarr.collect.stack import collect_stack
from lintarr.config import load_config
from lintarr.facts import Known, Unknown, is_known
from lintarr.models import StackFacts


def _fact_to_dict(f: Known[Any] | Unknown) -> dict[str, Any]:
    if is_known(f):
        return {
            "known": True,
            "value": f.value,
            "source": f.source,
            "read_at": f.read_at.isoformat(),
        }
    return {"known": False, "reason": f.reason, "detail": f.detail}


def _instance_to_dict(instance: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in dataclasses.fields(instance):
        value = getattr(instance, field.name)
        match value:
            case Known() | Unknown():
                out[field.name] = _fact_to_dict(value)
            case tuple() as items if items and dataclasses.is_dataclass(items[0]):
                out[field.name] = [_instance_to_dict(i) for i in items]
            case _:
                out[field.name] = value
    return out


def _to_dict(facts: StackFacts) -> dict[str, Any]:
    return {
        "qbits": [_instance_to_dict(q) for q in facts.qbits],
        "arrs": [_instance_to_dict(a) for a in facts.arrs],
        "errors": [{"instance": i, "kind": k} for i, k in facts.errors],
    }


def _render_human(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for group in ("qbits", "arrs"):
        for instance in payload[group]:
            lines.append(
                f"{instance.get('kind', 'qbittorrent')}[{instance['name']}] v{instance['version']}"
            )
            for key, value in sorted(instance.items()):
                if not isinstance(value, dict) or "known" not in value:
                    continue
                if value["known"]:
                    lines.append(f"    {key:<28} = {value['value']!r:<12} {value['source']}")
                else:
                    lines.append(f"    {key:<28} ? UNKNOWN ({value['reason']})")
            lines.append("")
    for err in payload["errors"]:
        lines.append(f"ERROR  {err['instance']}: {err['kind']}")
    return "\n".join(lines)


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """lintarr — static consistency checker for the *arr stack."""
    ctx.ensure_object(dict)


@cli.command("dump-facts")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def dump_facts(ctx: click.Context, as_json: bool) -> None:
    """Print a source-annotated snapshot of everything lintarr can read."""
    facts = collect_stack(load_config(os.environ), transport=ctx.obj.get("transport"))
    payload = _to_dict(facts)
    click.echo(jsonlib.dumps(payload, indent=2) if as_json else _render_human(payload))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (3 tests)

Then the full suite and lint:

```bash
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/cli.py tests/test_cli.py
git commit -m "feat(cli): dump-facts prints a source-annotated snapshot"
```

---

## Definition of done for P0a

Run against Tom's real stack from the Mac, with an SSH tunnel or directly on the
homelab box:

```bash
QBIT_URL=http://192.168.68.67:8080 QBIT_USER=admin QBIT_PASS=... \
SONARR_URL=http://192.168.68.67:8989 SONARR_API_KEY=... \
RADARR_URL=http://192.168.68.67:7878 RADARR_API_KEY=... \
  uv run lintarr dump-facts
```

Verify by eye:

1. Every qBittorrent preference in `_PREF_KEYS` is `Known` with a source.
2. All four Sonarr indexers appear with `seed_ratio` as `Known(None)` —
   configured-but-unset, which is the state that wedged the real stack.
3. No credential appears anywhere in the output.
4. Stopping qBittorrent and re-running records
   `ERROR qbittorrent[main]: unreachable` while Sonarr and Radarr still report.

## Self-review notes

Checked against the spec's P0a scope:

- Adapters, auth with ban avoidance, version detection, multi-instance
  `StackFacts`, `Known`/`Unknown` discipline, read-only test, `dump-facts` — all
  have tasks.
- **Deliberately deferred, per the revised delivery plan:** path normalisation
  (moved to P3 with `hardlink-futility`, its only consumer), suppression (moved
  to P1 with its store), the premise combinator and `queue-liveness` (P0b),
  filesystem facts (P3).
- `FilesystemFacts` is referenced in the spec's `StackFacts` but is **not** in
  this plan's `StackFacts`, because its only consumer is P3. It is added when
  `hardlink-futility` lands.
- Per-category share limits are collected as the raw `categories` payload in
  Task 6 rather than parsed, because the endpoint's share-limit fields vary by
  version. P0b parses them defensively into `Unknown("field-absent")` where
  missing.
