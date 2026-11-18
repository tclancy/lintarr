# Pluggable download-client backends — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** lintarr reads download clients through registered in-tree backends and is configured with client-neutral variable names, with qBittorrent's behaviour unchanged.

**Architecture:** A `Backend` registry in `src/lintarr/clients/` maps a kind to its credential contract, collector, checks and "Therefore" lines. `StackFacts.clients` holds `ClientInstance`s whose `specific` is the backend's own facts type (qBittorrent keeps today's `QbtInstance` unchanged). Config moves to `DOWNLOAD_CLIENT_*` / `ARR_*`; arr kind is detected from `/api/v3/system/status`'s `appName`.

**Tech Stack:** Python 3.13, click, httpx, pytest, Hypothesis, uv.

**Spec:** `docs/superpowers/specs/2026-11-18-pluggable-backends-design.md`

**Revised** after an Opus plan review (2026-11-18): old Tasks 2 and 3 merged (neither could pass the suite alone); import-cycle-free layout pinned; dump-facts shape pinned; "Therefore" table moved into the backend (G1).

## Global Constraints

- `uv run pytest -q` and pre-commit pass after every task. Never `--no-verify`.
- No new runtime dependencies (`pyproject.toml` `dependencies` stays `click`, `httpx`).
- Ruff: line length 100, target py313, rules E/F/I. E402 is on: no imports below code.
- Fact discipline: an unread value is `Unknown(reason, detail)`, never a default (`facts.py`). No fallbacks for a missing `appName`.
- Read-only guarantee: all HTTP goes through `ReadOnlyClient`; `tests/test_readonly_guarantee.py` stays green.
- Config errors are `ValueError` from `load_config`, surfaced by `cli._config_from_env` as a usage error, exit 2.
- qBittorrent labels are byte-identical: findings and errors `qbittorrent[<name>]`; invariant id `queue-liveness`; premise labels `qbt.*`; conflict names unchanged.
- Arr error labels become `arr[<name>]`, always.
- Registry: `BACKENDS: dict[str, Backend]`, a plain mutable dict (tests `monkeypatch.setitem` it); no entry points.
- Layout (no import cycles): `clients/base.py` holds `Backend` and `Check` and imports nothing from `clients/*` or `config`; `clients/__init__.py` only imports backends and builds `BACKENDS`; `config.py` imports `BACKENDS` **inside** `load_config`, never at module level.
- New variable names: `DOWNLOAD_CLIENT_KIND`, `DOWNLOAD_CLIENT_URL`, `DOWNLOAD_CLIENT_USER`, `DOWNLOAD_CLIENT_PASS`, `DOWNLOAD_CLIENT_API_KEY`, `ARR_URL`, `ARR_API_KEY`, each with optional `__NAME`; `LINTARR_SERVICES` unchanged.
- Legacy names rejected: `QBIT_URL`, `QBIT_USER`, `QBIT_PASS`, `SONARR_URL`, `SONARR_API_KEY`, `RADARR_URL`, `RADARR_API_KEY`, bare or `__NAME`-suffixed.
- Out of scope: any change to `queue_liveness` logic, `tests/model/`, `tests/fixtures/homelab.py`, the `_WEDGED` pin, conformance scenarios.

## Review Focus

1. A legacy name **alongside** the new names (half-migrated env) must still be rejected. Task 2.
2. `DOWNLOAD_CLIENT_KIND` with whitespace or capitals (`" QBittorrent "`) normalises (strip, lower). Task 2.
3. `appName` case varies and may be absent or non-string: case-insensitive detection; absent/other is ERROR, never a guess. Task 2.
4. `LINTARR_SERVICES=sonarr` with the only arr unreachable gives exactly one ERROR `arr[<name>]` and no `collect` SKIP. Task 2.
5. The same instance name in both families (`DOWNLOAD_CLIENT_URL__TV`, `ARR_URL__TV`) is legal and does not cross-wire credentials. Task 2.

---

### Task 1: Backend registry and client model, behaviour-preserving

Moves qBittorrent behind a `Backend`. Env still reads `QBIT_*` at the end of this task.

**Files:**
- Create: `src/lintarr/clients/base.py`, `src/lintarr/clients/__init__.py`, `src/lintarr/clients/qbittorrent/__init__.py`, `src/lintarr/checks/__init__.py`
- Move (`git mv`): `src/lintarr/collect/qbittorrent.py` -> `src/lintarr/clients/qbittorrent/collect.py`; `src/lintarr/invariants/queue_liveness.py` -> `src/lintarr/clients/qbittorrent/queue_liveness.py`
- Modify: `src/lintarr/models.py`, `src/lintarr/config.py`, `src/lintarr/collect/stack.py`, `src/lintarr/run.py`, `src/lintarr/cli.py`
- Modify (imports/fixtures): files from `git grep -lE "lintarr\.invariants\.queue_liveness|lintarr\.invariants import queue_liveness|lintarr\.collect\.qbittorrent|lintarr\.collect import qbittorrent|StackFacts\(|\.qbits|QbtConfig|_THEREFORE" tests tools src`, including `tests/strategies.py`, `tests/test_render_human.py:82`, `tests/test_cli.py:61,70,156`, `tests/test_properties.py:447`.
- Create: `tests/fixtures/clients.py` (`as_client`), `tests/clients/__init__.py`, `tests/clients/test_registry.py`

**Interfaces:**
- `clients/base.py`:
  - `Check = Callable[[ClientInstance, tuple[ArrInstance, ...]], Finding]`
  - `@dataclass(frozen=True) class Backend: kind: str; credentials: frozenset[Literal["user", "pass", "api_key"]]; collect: Callable[..., ClientInstance]` called as `collect(cfg, *, transport)`; `checks: tuple[Check, ...]`; `therefore: Mapping[tuple[str, str], str]` (keyed `(invariant, conflict)`, may be empty).
- `config.py`: `ClientConfig(name: str, kind: str, url: str, username: str | None = None, password: str | None = None, api_key: str | None = None)`, secrets masked in `__repr__` as today's `QbtConfig`; `QbtConfig` deleted. `LintarrConfig.qbits` renamed `.clients`.
- `models.py`: `ClientFacts()` (empty frozen dataclass); `ClientInstance(name: str, kind: str, version: str, common: ClientFacts, specific: object)`; `StackFacts(clients, arrs, errors=())`, `.qbits` removed. `QbtInstance` unchanged.
- `clients/qbittorrent/__init__.py`: `BACKEND = Backend(kind="qbittorrent", credentials=frozenset({"user", "pass"}), collect=..., checks=(lambda inst, arrs: queue_liveness.check(inst.specific, arrs),), therefore=THEREFORE)`. `THEREFORE` is `cli._THEREFORE` moved verbatim into `clients/qbittorrent/queue_liveness.py` (or a sibling module). Its `collect` opens `ReadOnlyClient(cfg.url, transport=transport, auth_path=AUTH_PATH)` and returns `ClientInstance(cfg.name, "qbittorrent", qbt.version, ClientFacts(), qbt)`.
- `checks/__init__.py`: `SHARED_CHECKS: tuple[Check, ...] = ()`. `run.py` does `from lintarr import checks` and reads `checks.SHARED_CHECKS` at call time (so tests can patch the module attribute).
- `run.run_checks`: per client instance, `BACKENDS[inst.kind].checks` then `checks.SHARED_CHECKS`, each result through `_resolve_missing_arrs`. `_collected` uses kinds in `facts.clients`.
- `cli`: "Therefore" lookup is `BACKENDS[<kind of f's instance>].therefore.get((f.invariant, f.conflict))` — or the union of all backends' tables, builder's call, but no table in `cli.py`. **dump-facts shape (pinned):** top-level key `"clients"`, each entry flat: `{"name", "kind", "version", **<fact fields of specific>}`; `common` omitted while `ClientFacts` has no fields. `_render_human` iterates `"clients"` and takes the label kind from the entry's `kind`.
- `tests/fixtures/clients.py`: `as_client(qbt: QbtInstance) -> ClientInstance`. Do not edit `tests/fixtures/homelab.py`.
- `tests/strategies.py`: the stack-facts strategy builds clients of kind `"qbittorrent"` via `as_client`.

- [ ] **Step 1: Write failing tests** in `tests/clients/test_registry.py`:

```python
from lintarr.clients import BACKENDS
from lintarr.clients.qbittorrent import BACKEND

def test_qbittorrent_is_registered_under_its_kind():
    assert BACKENDS["qbittorrent"] is BACKEND
    assert BACKEND.kind == "qbittorrent"

def test_qbittorrent_needs_user_and_pass():
    assert BACKEND.credentials == frozenset({"user", "pass"})

def test_queue_liveness_is_qbittorrents_only_check():
    assert len(BACKEND.checks) == 1

def test_registry_is_patchable():
    assert type(BACKENDS) is dict
```

plus label pins: running `run_checks` over the wedged homelab fixture (via `as_client`) yields a finding with `invariant == "queue-liveness"` and `instance == "qbittorrent[main]"`; a qBittorrent `ServiceError` through `collect_stack` yields `ErrorRow` label `qbittorrent[main]`.
- [ ] **Step 2: Run** `uv run pytest tests/clients -q` — expect ImportError.
- [ ] **Step 3: Implement** per Interfaces. `load_config` still reads `QBIT_*` and builds `ClientConfig(kind="qbittorrent", ...)`.
- [ ] **Step 4: Update imports/fixtures** from the grep above. Allowed assertion edits: `payload["qbits"]` -> `payload["clients"]`, `_THEREFORE` import path. Nothing else.
- [ ] **Step 5: Run** `uv run pytest -q`; `git grep -nE "\.qbits|QbtConfig|lintarr\.invariants" src tests tools` is empty; `src/lintarr/invariants/` holds only `combinator.py` and `__init__.py`.
- [ ] **Step 6: Commit** `refactor: download clients behind a backend registry (qBittorrent unchanged)`.

### Task 2: Client-neutral configuration, arr kind detection, labels, declared set

One task because removing the config's arr kind, detecting it, relabelling arr errors and recomputing the declared set cannot pass the suite separately.

**Files:**
- Modify: `src/lintarr/config.py`, `src/lintarr/collect/arr.py`, `src/lintarr/collect/stack.py`, `src/lintarr/run.py`, `src/lintarr/cli.py`, `src/lintarr/clients/qbittorrent/collect.py` (`_LOGIN_401_DETAIL` names `DOWNLOAD_CLIENT_URL`), docstrings naming legacy vars in `run.py:7`, `config.py:81`, `cli.py:38`
- Modify tests (named by review; also `git grep -lE "QBIT_|SONARR_|RADARR_|\.declared|\.kind" tests tools`):
  - status mocks gain `"appName": "Sonarr"` (or `"Radarr"`): `tests/test_cli.py:43`, `tests/test_check_cli.py:65`, `tests/test_readonly_guarantee.py:27`, `tests/test_error_detail_route.py:91`, `tests/collect/test_stack_error_labels.py:57`, `tests/collect/test_stack.py:52`, `tests/collect/test_arr.py:66`
  - arr labels `sonarr[main]` -> `arr[<name>]`: `tests/test_error_detail_route.py:276-284`, `tests/collect/test_stack_error_labels.py:82-118,130` (rewrite its "nothing ever declares `arr`" docstring), `tests/test_run.py:56`, `tests/test_properties.py:482`
  - `.kind`/`.declared` readers: `tests/test_config.py:18,41,53`, `tests/collect/test_stack_error_labels.py:109,130`
  - env-clearing `_PREFIXES` gain `DOWNLOAD_CLIENT_` and `ARR_` (keep legacy prefixes): `tests/test_cli.py:26`, `tests/test_check_cli.py:9`, `tests/test_cli_config_errors.py:18`, `tests/test_error_detail_route.py:36`; `tests/test_check_cli.py:260,276` filter `ARR_`
  - `tests/collect/test_qbittorrent_host_header.py:106,277,287` retarget to `DOWNLOAD_CLIENT_URL`
  - `tests/strategies.py:186-202`: error labels may be `arr[...]`; `DECLARED` includes `arr`
- Create: `tests/test_config_neutral.py`, `tests/test_declared_after_detection.py`

**Interfaces:**
- `ArrConfig(name: str, url: str, api_key: str)` — no `kind`.
- `LintarrConfig(clients, arrs, declared_explicit: frozenset[str] | None)` — `declared` removed; `declared_explicit` is `LINTARR_SERVICES` parsed as today, or `None` if unset.
- `run.declared_services(cfg: LintarrConfig, facts: StackFacts) -> frozenset[str]`: `cfg.declared_explicit` if not `None`; else `{c.kind for c in cfg.clients} | {a.kind for a in facts.arrs} | ({"arr"} if any error label starts with "arr[" else set())`. `cli.check_command` calls `run_checks(facts, declared=declared_services(cfg, facts))`.
- `collect_arr` reads `appName` from the same status payload as the version; `.strip().lower()` in `{"sonarr", "radarr"}` -> `ArrInstance.kind`; absent, non-string or other -> `ServiceError("bad-response", f"{_STATUS}: appName {value!r} is not a supported arr (radarr, sonarr)")`.
- `stack.collect_stack` labels arr errors `f"arr[{arr.name}]"`.
- `run._attempted`: an `arr` label adds `arr` **and** every member of `ARR_KINDS`.
- `run._resolve_missing_arrs`: relabel to N/A only when `not (declared & ARR_KINDS)` **and** `not facts.arrs` **and** no error label starts with `arr[` (today's two clauses plus the arr-error clause).
- `load_config(env)` rules, each a `ValueError` naming the variable(s):

| Input | Message must contain |
|---|---|
| any legacy name(s), bare or suffixed | **every** legacy name present, each with its replacement (`QBIT_URL__4K` -> `DOWNLOAD_CLIENT_URL__4K`); if any `QBIT_*` is present, also `DOWNLOAD_CLIENT_KIND=qbittorrent` |
| `DOWNLOAD_CLIENT_URL[__X]` without `_KIND[__X]` | `DOWNLOAD_CLIENT_KIND[__X]` |
| `DOWNLOAD_CLIENT_KIND[__X]` without `_URL[__X]` | `DOWNLOAD_CLIENT_URL[__X]` |
| kind not in `BACKENDS` (after strip/lower) | the kind as given and the known kinds |
| missing required credential | the missing variable |
| credential the backend does not use | the variable and the kind |
| orphaned credential (`DOWNLOAD_CLIENT_USER__X`, no URL) | `DOWNLOAD_CLIENT_URL__X` |
| `ARR_URL[__X]` without `ARR_API_KEY[__X]`, or the reverse | the missing variable |

- [ ] **Step 1: Write failing tests.**
  - `tests/test_config_neutral.py`: a full neutral env loads (clients `[("main","qbittorrent","http://q:8080")]` with trailing slash stripped; arrs `movies`, `tv`); one test per table row; legacy names parametrised over all 7 bases × {bare, `__4K`} asserting the exact replacement; one bare and one suffixed legacy case through `CliRunner` exit 2; Review Focus 1, 2, 5.
  - `tests/collect/test_arr.py`: `appName` `"Sonarr"`, `"sonarr"`, `"RADARR"` detected; `"Lidarr"`, absent, `123` -> `bad-response` naming the value.
  - `tests/test_declared_after_detection.py` (through `collect_stack` + `run_checks` with a mock transport, pattern `tests/collect/test_stack.py`):
    - `LINTARR_SERVICES=sonarr`, arr unreachable -> exactly one ERROR `arr[tv]`, zero findings with `invariant == "collect"` and outcome SKIP (Review Focus 4).
    - no `LINTARR_SERVICES`, arr unreachable -> exactly one ERROR `arr[tv]`, zero `collect` SKIPs, and the queue finding's arr-shaped SKIP is **not** relabelled N/A.
    - `appName` `"Lidarr"` -> ERROR `arr[tv]`, exit non-zero.
    - `LINTARR_SERVICES=qbittorrent,sonarr`, no arr configured -> SKIP for `sonarr`, exit non-zero.
    - no arr configured, no `LINTARR_SERVICES` -> arr-shaped SKIP becomes N/A with today's message.
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Migrate the named tests**; no fallback for missing `appName`.
- [ ] **Step 5: Run** `uv run pytest -q`; `git grep -nE "QBIT_|SONARR_|RADARR_" src` hits only the legacy table and its docstring/messages in `config.py`.
- [ ] **Step 6: Commit** `feat(config): client-neutral names; arr kind from appName; arr[<name>] labels`.

### Task 3: Fake backend proves the seam; backend guide

**Files:**
- Create: `tests/fakes/__init__.py`, `tests/fakes/backend.py`, `tests/clients/conftest.py`, `tests/clients/test_fake_backend.py`, `docs/adding-a-backend.md`
- Modify: `README.md` (env names only), `CONTINUATION.md` (state line)

**Interfaces:**
- `tests/fakes/backend.py`: `FakeFacts(broken: bool)`; `FAKE = Backend(kind="fake", credentials=frozenset({"api_key"}), collect=..., checks=(fake_check,), therefore={})`. `collect(cfg, *, transport)` does no HTTP; raises `ServiceError("unreachable", "fake")` when `cfg.url` ends with `/down`; `broken` is `cfg.url.endswith("/broken")`. `fake_check` returns a `Finding(invariant="fake-check", instance=f"fake[{inst.name}]", outcome=FAIL if broken else PASS)`.
- `tests/clients/conftest.py`: fixture `fake_backend` -> `monkeypatch.setitem(BACKENDS, "fake", FAKE)`; fixture `shared_probe` -> `monkeypatch.setattr(lintarr.checks, "SHARED_CHECKS", (probe,))`, `probe` returns PASS `Finding(invariant="shared-probe", instance=f"{inst.kind}[{inst.name}]")`.

- [ ] **Step 1: Write tests** in `tests/clients/test_fake_backend.py`:
  - `DOWNLOAD_CLIENT_KIND=fake` + `DOWNLOAD_CLIENT_API_KEY` loads; without the key -> `ValueError`; with `DOWNLOAD_CLIENT_USER` -> `ValueError` naming it and `fake`.
  - `check --json` via `CliRunner`: a fake instance yields `fake-check` and no `queue-liveness` finding.
  - With `shared_probe`, fake `__A` plus qBittorrent `__B` (mock transport): `shared-probe` on both `fake[a]` and `qbittorrent[b]`; `fake-check` only on `fake[a]`; `queue-liveness` only on `qbittorrent[b]`.
  - Fake at `.../down` -> ERROR `fake[<name>]`, exit 2.
  - Without the fixture, `DOWNLOAD_CLIENT_KIND=fake` is an unknown-kind `ValueError` (B6: registration is the only edit).
- [ ] **Step 2: Implement** the fake and fixtures; tests pass.
- [ ] **Step 3: Write `docs/adding-a-backend.md`:** `Backend` fields (incl. `therefore`); the registry line; credential rules (required and unused both rejected); "client facts grow with their consumers" (spec wording); labels are `<kind>[<name>]`; the proof bar — conformance tests against the real client from a pinned image (as `tests/conformance/`), a measurements doc for any queue behaviour a check assumes, the fake-backend suite green.
- [ ] **Step 4: Update README env names and CONTINUATION state line.**
- [ ] **Step 5: Run** `uv run pytest -q`; commit `test: fake backend proves the seam; docs: adding a backend`.

### Task 4 (controller): conformance run and homelab PR

- [ ] Push the branch as the bot. Ask Tom to dispatch `gh workflow run conformance.yml --repo tclancy/lintarr --ref claude/pluggable-backends` (the bot gets 403); record the green run URL (B5).
- [ ] In `~/Documents/work/homelab`, branch `claude/lintarr-neutral-env`: `ansible/roles/native-apps/templates/lintarr.env.j2` renders `DOWNLOAD_CLIENT_KIND=qbittorrent`, `DOWNLOAD_CLIENT_URL/USER/PASS`, `ARR_URL__TV`/`ARR_API_KEY__TV`, `ARR_URL__MOVIES`/`ARR_API_KEY__MOVIES`, `LINTARR_SERVICES=qbittorrent,sonarr,radarr`; reword its `QBIT_USER`/`QBIT_PASS` comment; update `tests/test_lintarr_deploy.py` `_REQUIRED_TOGETHER`. Run homelab pytest. Open the PR as the bot; do not merge.
- [ ] Rollout (spec "Rollout"/"Timing") only after Tom approves the merges.
