# Pluggable download-client backends — design

**Date:** 2026-11-18 (UTC). **Status:** draft for Tom's approval.
**Builds on:** `2026-08-26-lintarr-design.md` (architecture, fact discipline,
v1 invariant set).

## Why

lintarr reads one download client, qBittorrent, and the name is wired through
every layer: the environment (`QBIT_URL`, `QBIT_USER`, `QBIT_PASS`), the config
(`QbtConfig`), the collector (`collect/qbittorrent.py`), the data model
(`QbtInstance`, `StackFacts.qbits`), the runner (`run.py` calls
`queue_liveness.check` once per qBittorrent) and the "declared services" logic
(`{"qbittorrent"} if facts.qbits`).

Tom wants other people to be able to add support for other clients, and he
wants the config to stop naming a client (Tom, 2026-11-17). The three planned
invariants (`hardlink-futility`, `orphan-inevitability`, `seed-goal-conflict`)
mostly do not care which client is in use. If they are written against
`QbtInstance` they each become a retrofit later. So the seam goes in first.

## Decisions (Tom, 2026-11-17/18)

| Decision | Choice |
|---|---|
| Where backends live | **In this repo**, added by pull request. Each one is proven against the real client in a container, like qBittorrent. No out-of-tree plugin packages, no entry points. |
| Config names | Client-neutral (table below). No aliases for the old names. |
| Seam shape | Approach A (below): shared client facts, plus each backend's own checks. |
| Proving the seam | A fake backend that exists only in the tests, plus a written guide. A real second client is a later slice. |

## Goals

- G1. A backend is one package under `src/lintarr/clients/<kind>/`, and adding
  one needs no edit outside that package except one line in the registry.
- G2. The config names no client or arr app.
- G3. qBittorrent behaves exactly as before: same findings, same exit codes,
  same edge-trigger state keys, same conformance nightly.
- G4. Shared checks (none yet) read only client-neutral facts and run on every
  backend.
- G5. `docs/adding-a-backend.md` states what a backend must provide and the
  proof bar it must meet.

## Non-goals

- A second real backend (Deluge, Transmission, SABnzbd). Later slice.
- The three new invariants. Each is its own later slice, and each adds the
  neutral facts it needs (see "Client facts grow with their consumers").
- The seeder-absorption fix to `queue-liveness` (F1/F3). Deferred until after
  the 2026-11-21 check-in.
- README / first-run readiness for other users. Tabled by Tom 2026-11-18.
- Out-of-tree plugins.

## Anti-goals (a failure even if it works)

- **A silent green after the rename.** Any run where the operator configured
  something and lintarr looked at nothing must not exit 0. Concretely: an
  environment still holding `QBIT_*`, `SONARR_*` or `RADARR_*` names must be a
  usage error (exit 2) that names the replacement variables, not an empty
  config that passes.
- **An unknown client kind treated as "nothing configured".**
  `DOWNLOAD_CLIENT_KIND=delugee` is a usage error listing the known kinds.
- **A shared abstraction of the queue.** Queue rules differ by client (this is
  what P2 measured); the queue check stays backend-specific.
- **Behaviour drift on qBittorrent hidden inside a refactor.** The move must be
  provably behaviour-preserving (G3 checks).

## Approaches considered

- **A. Shared client facts plus per-backend checks (chosen, Tom deferred to my
  call).** Each backend supplies its credentials contract, a collector that
  returns neutral facts plus its own specific facts, and its own checks. Shared
  checks read only neutral facts. Costs a backend author their queue check,
  which is honest: queues really do differ.
- **B. One shared queue model every backend maps into.** One queue check for
  all clients. Rejected: Transmission has separate download and seed queues
  and no total limit; forcing it into one model needs flags until the model is
  wrong somewhere, which is the class of error P2 spent two slices finding.
- **C. Rename the variables and add a registry, keep `QbtInstance`.**
  Cheapest now. Rejected: the three new checks would be written against
  qBittorrent fields, which defeats the point.

## Design

### Configuration

Every instance keeps today's `__NAME` suffix convention (`_instances()` in
`config.py`); the unsuffixed variable is instance `main`.

| Variable | Meaning |
|---|---|
| `DOWNLOAD_CLIENT_KIND[__NAME]` | Required. A registered backend kind, e.g. `qbittorrent`. |
| `DOWNLOAD_CLIENT_URL[__NAME]` | Required. |
| `DOWNLOAD_CLIENT_USER[__NAME]`, `DOWNLOAD_CLIENT_PASS[__NAME]` | Credentials, if the backend's contract needs them. |
| `DOWNLOAD_CLIENT_API_KEY[__NAME]` | Credential, if the backend's contract needs it. |
| `ARR_URL[__NAME]`, `ARR_API_KEY[__NAME]` | An arr instance. Its kind is read from the app. |
| `LINTARR_SERVICES` | Unchanged: comma-separated **kinds** (`qbittorrent`, `sonarr`, `radarr`). |

Rules, all of them errors at load time (exit 2, usage error, no payload):

1. **Legacy names.** Any variable matching `QBIT_(URL|USER|PASS)`,
   `SONARR_(URL|API_KEY)` or `RADARR_(URL|API_KEY)`, with or without an
   `__NAME` suffix, is rejected with a message naming its replacement.
2. **Unknown kind.** `DOWNLOAD_CLIENT_KIND` not in the registry lists the
   known kinds.
3. **Missing kind or URL.** A `DOWNLOAD_CLIENT_URL` without a matching `_KIND`,
   or the reverse, is an error. This extends today's
   `_reject_orphaned_credentials` rule to the kind.
4. **Credentials contract.** Each backend declares which credential variables
   it requires. A missing required one is an error (today's
   `QBIT_URL set but QBIT_USER/QBIT_PASS missing`). A credential the backend
   does not use is an error too, because it is most likely a mistake about
   which client is configured.
5. **Orphaned credentials.** Unchanged in spirit: credentials for an instance
   whose URL is absent are an error.

**Arr kind detection.** `collect/arr.py` already reads
`/api/v3/system/status` for the version. It also reads `appName` from the same
payload and maps it (case-insensitively) to `sonarr` or `radarr`. Any other
or missing `appName` is a collection ERROR for that instance
(`bad-response`, naming the value), never a guess. Detection happens at
collection, so `LintarrConfig.arrs` no longer carries a kind, and the
"declared" set derived from config can no longer say `sonarr` vs `radarr`
before collection. See "Declared services" below.

### The backend contract

`src/lintarr/clients/__init__.py` holds the registry, an explicit dict:

```python
BACKENDS: Mapping[str, Backend] = {"qbittorrent": qbittorrent.BACKEND}
```

A `Backend` (frozen dataclass or Protocol; builder's call) provides:

- `kind: str`, the value of `DOWNLOAD_CLIENT_KIND`.
- `credentials: frozenset[Literal["user", "pass", "api_key"]]`, the contract
  for rule 4.
- `collect(cfg: ClientConfig, *, transport) -> ClientInstance`, raising
  `ServiceError` exactly as `collect_qbt` does today. Auth, ban avoidance and
  the read-only guarantee stay inside the backend and inside `ReadOnlyClient`.
- `checks: tuple[Check, ...]`, the backend's own invariants.
- `therefore: Mapping[tuple[str, str], str]`, the operator-facing "Therefore"
  lines for its own `(invariant, conflict)` pairs (added after the plan
  review: today's table lives in `cli.py`, which would make every new backend
  edit `cli.py`, against G1).

`ClientConfig` replaces `QbtConfig`: `name`, `kind`, `url`, and the three
optional credentials, with the same secret-masking `__repr__`.

### Client facts

`ClientInstance` replaces `QbtInstance` in `StackFacts`
(`StackFacts.clients` replaces `.qbits`):

- `name`, `kind`, `version`.
- `common: ClientFacts`, the neutral facts. **Empty in this slice** (my call,
  after the builder check). The only candidate, `categories`, is qBittorrent's
  raw `/api/v2/torrents/categories` payload, with qBittorrent's own sentinels
  (-2 inherit, -1 unlimited). Moving it as-is would make "neutral" only a name,
  and nothing neutral reads it yet. Under the rule below, the first shared
  check that needs categories (`hardlink-futility`, for save paths) defines a
  genuinely neutral shape and populates it from every backend.
- `specific`: the backend's own facts object. For qBittorrent this is all of
  today's `QbtInstance` fields, including `categories`, unchanged in type and
  meaning (`QbtFacts`).

**Client facts grow with their consumers.** A neutral field is added in the
same slice as the first shared check that reads it, and that slice must
populate it in every registered backend. This keeps unread facts out of the
model and makes "add a shared check" carry its cost across all backends.

### Checks

A check is `(instance: ClientInstance, arrs: tuple[ArrInstance, ...]) ->
Finding` (or a tuple of findings; builder's call, consistent across both
kinds of check).

- **Backend checks** live in the backend package and receive their own
  backend's instances only.
- **Shared checks** live in `src/lintarr/checks/` and run on every instance.
  There are none in this slice; the runner supports the list.

`queue_liveness` moves into `clients/qbittorrent/` and reads
`instance.specific`. Its logic, premise labels (`qbt.*`), invariant name,
conflict names and messages do not change. The test model it is swept
against (`tests/model/queue.py`, `simulate()`/`trace()`) stays where it is;
only imports change.

`run.py`'s per-arr SKIP relabel (`_resolve_missing_arrs`) and absent-service
logic keep working with any kind.

### Declared services

Today `declared` comes from `LINTARR_SERVICES` or, by default, from what the
config names (`qbittorrent`, `sonarr`, `radarr`). After the rename the config
names client kinds directly, but not arr kinds, which are only known after
collection.

Rule: the **default** declared set is the configured client kinds plus the
**detected** arr kinds, plus the placeholder kind `arr` when any configured arr
instance failed collection (it already produces an ERROR finding, so it is
never silent).

**Arr error labels are `arr[<name>]`, always** (my call, after the builder
check). An arr that fails collection is labelled by its instance name whether
the status call or a later call failed. Otherwise the label would flip between
`arr[tv]` and `sonarr[tv]` with the failure point, and each flip is a new alert
key that re-pages. Consequences the builder implements:

- `_attempted()` treats an `arr` attempt as covering every arr kind in the
  declared set, so `LINTARR_SERVICES=sonarr` plus an unreachable arr gives one
  ERROR, not an ERROR and a SKIP.
- `_resolve_missing_arrs` relabels an arr-shaped SKIP to N/A only when no arr
  instance is **configured** at all (from the config, not from the declared
  set), so a placeholder never produces "no sonarr or radarr is configured". `LINTARR_SERVICES`, when set, still names kinds explicitly
and still wins. The builder must show, by test, that each of these still
yields a non-zero exit: an arr URL that answers with an unknown `appName`; a
declared `sonarr` with no arr configured; a configured arr that is
unreachable.

### Finding labels and edge-trigger continuity

Alert keys are `(invariant, instance, conflict, outcome)` (`edge.py:42`).
For the qBittorrent backend, finding and collection-error labels must be
byte-identical before and after (`qbittorrent[<name>]`), so persisting
qBittorrent findings carry over. Arr error labels change once, from
`sonarr[main]`/`radarr[main]` to `arr[<name>]`. Homelab is currently PASS, so
nothing persisting re-pages. A test pins the new labels.

### The fake backend

`tests/fakes/backend.py` defines `kind="fake"`, `credentials={"api_key"}`, a
collector that returns canned facts without HTTP, and one trivial check. A
fixture registers it in `BACKENDS` for the test's duration. Tests through the
real `load_config` and `run_checks` (and the CLI via `CliRunner`) show:

- `DOWNLOAD_CLIENT_KIND=fake` loads, with the fake's credential contract
  enforced.
- Its check runs; qBittorrent's do not run on it.
- A shared check (a test-only one) runs on both a fake and a qBittorrent
  instance in the same run.
- A fake that raises `ServiceError` produces an ERROR labelled `fake[<name>]`.
- Two client kinds side by side produce per-instance findings.

### The guide

`docs/adding-a-backend.md`: the contract; the registry line; the credential
rules; that client facts grow with their consumers; and the **proof bar**. A
backend PR includes conformance tests that run the real client from a pinned
image (as `tests/conformance/` does for qBittorrent), a measurements doc for
any queue behaviour its checks assume, and passes the fake-backend suite.

### Rollout (homelab)

lintarr on homelab reads `~/.config/lintarr/env` (or wherever the template
renders), rendered from `roles/native-apps/templates/lintarr.env.j2`. The new
lintarr rejects the old names, so the two must change together:

1. Merge the lintarr PR. The box does not pull it until a deploy.
2. Merge a homelab PR that renders the new names
   (`DOWNLOAD_CLIENT_KIND=qbittorrent`, `DOWNLOAD_CLIENT_URL/USER/PASS`,
   `ARR_URL__TV`/`ARR_API_KEY__TV`, `ARR_URL__MOVIES`/`ARR_API_KEY__MOVIES`).
   It also renders `LINTARR_SERVICES=qbittorrent,sonarr,radarr` (my call,
   after the builder check). Old lintarr code reading the new env then sees
   declared-but-absent services and exits non-zero instead of an empty config
   passing. That makes a later revert of lintarr loud, not silently green. The
   PR also updates `tests/test_lintarr_deploy.py`, which pins the env names.
3. One `itguy deploy lintarr`: it renders the env and pulls lintarr in one run.
4. Verify: `itguy logs lintarr --level info` shows a PASS run with the
   qBittorrent labels unchanged, and no page fired.

**Timing (my call, after the builder check).** Any native-apps run pulls
lintarr `main`, and the hourly timer fires at `:17`, so new code can briefly
meet the old env. Within one deploy, the pull task also runs before the env
task. New code with the old env exits 2 and pages. So steps 1-3 happen in one
sitting, started just after a `:17` tick, with no other native-apps deploy in
between. At worst one page fires, and it says plainly which variables to
rename.

Instance names change the arr labels (`sonarr[main]` -> `sonarr[tv]`) if
suffixes are used, which would re-page any persisting finding once. Keeping
the unsuffixed `ARR_URL` is not possible for two arrs, so the builder states
the label change in the homelab PR; with the stack currently PASS there is
nothing persisting to re-page.

## Requirements and checks

| # | Requirement | Check |
|---|---|---|
| B1 | Config names no client or arr app. | `git grep -nE "QBIT_\|SONARR_\|RADARR_" src/` hits only the legacy-name rejection table. |
| B2 | Legacy names fail loudly. | Test: each legacy name (bare and suffixed) -> exit 2 with the replacement named. |
| B3 | Unknown kind, kind without URL, URL without kind, missing or unused credential fail loudly. | One test per rule, exit 2. |
| B4 | Arr kind comes from the app. | Tests: `appName` Sonarr/Radarr detected; other or missing -> ERROR for that instance, non-zero exit. |
| B5 | qBittorrent behaviour is unchanged. | Full suite green with fixtures only renamed; `_WEDGED` and healthy pins unchanged; finding/alert-key labels pinned; one green manual conformance workflow run on the branch. |
| B6 | A backend is self-contained. | The fake backend is registered by a fixture alone, with no other edits, and passes the suite above. |
| B7 | Shared checks run on every backend. | Test with a test-only shared check over a fake and a qBittorrent instance. |
| B8 | No silent green across the cut-over. | Tests for each case in "Declared services"; homelab rollout step 4. |
| B9 | The guide exists and states the proof bar. | `docs/adding-a-backend.md` present; reviewed. |

## Left to the builder

Module names inside `clients/qbittorrent/`; dataclass vs Protocol for
`Backend`; whether checks return one finding or a tuple; the typing of
`specific` (generic parameter vs per-backend type); `dump-facts` output shape
(it follows the new model); homelab instance names (propose `tv`/`movies`).
