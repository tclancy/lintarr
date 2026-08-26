# lintarr — design

**Status:** draft, revised after two review rounds
**Date:** 2026-08-26
**Licence:** MIT

## Problem

A media-automation stack is four or five services, each individually correct,
whose retention and queueing rules interact in ways no single service can see.
The failure mode is silence: the stack does not error, it simply stops doing
work, and nobody notices for weeks.

The motivating incident. qBittorrent had `max_active_torrents = 5` with share
limits disabled. Seeding torrents count against that limit, so once five
torrents completed they held every slot permanently. Fifty-two torrents, zero
active downloads, zero kB/s, no error anywhere. Every individual setting was
legal. The *combination* was not.

That conflict is determined by configuration alone, and is therefore detectable
before it ever manifests.

## What this is

A sidecar container that reads the live configuration of an arr stack, decides
whether the settings can coexist, and reports conflicts in terms of the specific
settings responsible.

## Where it sits in the ecosystem

The novelty claim must be stated narrowly to survive scrutiny.

- **Recyclarr** writes configuration; it does not check consistency.
- **Cleanuparr, Decluttarr, Maintainerr, Sanitarr** react to observed runtime
  state, so by construction they engage only after damage.
- **Sonarr and Radarr ship health checks** already covering several cross-service
  faults: download client unreachable, root folder missing, remote path mapping
  wrong, completed-download-handling disabled. v1 must not duplicate these, and
  the README should list them.

None of the built-in health checks would have caught the motivating incident,
because nothing was downloading and therefore no import failed. The gap is
**static cross-service consistency** — realistically eight to twelve
configuration-determined impossibilities exist in this ecosystem. That is a
useful linter. It is not a platform.

## Decisions

| Decision | Choice |
|---|---|
| Promise | Zero-*invariant*-config: you configure connections, never rules |
| Delivery | Python sidecar container, API-first, own web UI |
| Reasoning scope | Static, from config; live state is corroborating evidence only |
| Engine | Plain predicates with tracked premises. **No solver.** |
| Temporal properties | Closed form, validated by simulation *and* by container differential |
| Credentials | API keys required for every service; no auto-discovery |

### Why no solver

An earlier draft used Z3. Review killed it, correctly:

- **Z3 is not a model checker.** It has no temporal operators, so it never
  supplied the eventuality reasoning that justified it.
- **Every v1 invariant reduces to a short predicate**, three to comparisons.
- **Unsat cores as explanation are worse than hand-rolled premise tracking** —
  not minimal by default, unstable across versions and seeds, unordered, and
  they include the goal assertion.
- **z3py creates variables on reference.** A typo'd `Int('max_activ_torrents')`
  is a fresh unconstrained variable, making the system more satisfiable — a
  silent typo becomes a silent green.

**Re-admission gate**, stated precisely so it does not over-reach in either
direction:

- A **temporal** property whose state space resists exhaustive simulation →
  **TLA+/TLC**, which is the tool for that class.
- A **combinatorial allocation** question (is there any assignment satisfying
  every profile's goals across clients and categories) → **enumeration**. At
  realistic sizes — five clients, twenty categories — brute force answers this
  in milliseconds. Not a formal tool.
- Neither case exists in v1 or in the current backlog.

### How temporal properties are handled, and what that does *not* prove

`queue-liveness` asserts something about traces. Its reasoning is a **monotone
quantity with an absorbing state**, which collapses to a closed form.

Validation is deliberately two-level, because the first level alone is
circular:

**Level 1 — derivation check (fast, every push).** A state-machine model in
`tests/model/queue.py`, swept exhaustively, asserting
`predicate(cfg) == simulate(cfg)`.

This catches errors in *deriving* the closed form from the model, which is
exactly where hand-derivation goes wrong. **It cannot catch a wrong model.** A
simulator is an axiom set written in Python; a belief held by the author enters
both the predicate and the simulator, and the sweep reports perfect agreement
while both are wrong together. Most sharply: **a sweep over the predicate's own
parameters cannot discover a parameter the predicate is missing.**

**Level 2 — fidelity check (P2, nightly).** A **differential test**: generate a
short scenario from the simulator, drive a real qBittorrent container through
the same scenario using `skip_checking` to reach seeding without peers, and
assert observed `(active, queued, seeding)` counts match the simulator's at each
step. Six to eight scenarios, a few minutes.

Without Level 2 the temporal argument rests on one person's reading of the wiki.
It is the only mechanism in the plan that can catch a *missing* parameter.

Sweep box, and why:

```
max_active_downloads   0, 1..6, -1 (unlimited sentinel)
max_active_uploads     0, 1..6, -1
max_active_torrents    0, 1..6, -1
torrent count          1..10
dont_count_slow_torrents  x {on, off}
share limits x per-category limits x per-arr seed goals
```

`0` and `-1` are legal qBittorrent values and both are edge-critical —
`max_active_downloads = 0` is legal and immediately catastrophic. The magnitude
range exists to confirm the predicate is **insensitive** to magnitude, since it
tests only finiteness. **Rule: if any premise ever becomes magnitude-sensitive,
this box must widen.**

## Architecture

```
collect/     per-service adapters -> normalized StackFacts snapshot
theory/      axioms: claims about how each service behaves
invariants/  predicates over facts, with tracked premises
report/      findings, premises, explanations, history
```

Data flows one way. `collect` never imports `theory`; `invariants` never touch
the network.

### `collect` — facts, and the absence of facts

**A fact is either known or unknown, never defaulted.** Represented as a union,
not a nullable value — qBittorrent legitimately returns nulls, so a genuinely
read `None` must be distinguishable from unread:

```python
@dataclass(frozen=True)
class Known[T]:
    value: T
    source: str  # "GET /api/v2/app/preferences"
    read_at: datetime
    service_version: str


@dataclass(frozen=True)
class Unknown:
    reason: Literal["service-absent", "field-absent", "insufficient-permission"]
    detail: str


type Fact[T] = Known[T] | Unknown
```

**Adapters are read-only**, enforced by a test asserting no adapter emits a
non-GET. The sidecar holds every credential in the stack and must not be able to
mutate it.

**`StackFacts` is multi-instance from day one.** Separate 4K and anime Sonarr
instances, and multiple download clients per arr, are the common case.

```python
@dataclass(frozen=True)
class StackFacts:
    arrs: tuple[ArrInstance, ...]  # each with its own download clients
    download_clients: tuple[QbtInstance, ...]
    filesystem: FilesystemFacts
```

Per-category share limits and per-download-client seed goals are modelled
explicitly — that is exactly where the conflicts live.

### Credentials

API keys are **required**, supplied as environment variables. No Docker socket
inspection, no reading `config.xml`, no auto-discovery.

**qBittorrent auth needs specific care.** `/api/v2/auth/login` sets an SID
cookie, and qBittorrent enforces `WebUI\MaxAuthenticationFailCount` with an IP
ban (default 3600s). An hourly checker with a wrong password bans itself — and
behind `network_mode: service:gluetun` it may degrade the stack it is verifying.

- reuse the session across a run; never log in per request
- exponential backoff on auth failure, never a tight retry
- report "appears banned" as a **distinct** `ERROR`, not "bad credentials"
- document the whitelisted-subnet path, where no credentials are needed

Credentials never appear in logs, findings, or API responses.

### `theory` — axioms and the fidelity problem

Every axiom carries its constraint, a **citation**, a **version range**, and a
**conformance status**.

| Axiom family | Testable | Cost |
|---|---|---|
| Hardlink cross-device behaviour | Yes — loop devices, `os.link()`, no services | seconds |
| `qbt.seeding-occupies-slot` | Yes — `mktorrent` a local file, add with `skip_checking`, set limits, assert `queuedDL` | ~1–3 min |
| qBittorrent share-limit action | Yes, fiddly — must force a ratio via a second client | minutes |
| Sonarr/Radarr import lifecycle | Problematic — needs real metadata lookup (TVDB/TMDb), keys, indexer stub | tens of min, network-dependent |
| Interleaving/race timing | **No** — cannot assert a race deterministically | n/a |

**Most axiom lines in a report will read `assumed`, not `conformance-tested`.**
The README must say so plainly.

**When a nightly conformance test fails**, with flake tolerance so one bad night
cannot block every release:

1. First failure → axiom enters **`quarantined`**; findings depending on it
   downgrade in confidence; nothing is blocked.
2. **Three consecutive** failures → axiom demoted to `assumed`, and release is
   blocked if it backs a `FAIL`-grade invariant.

### `invariants` — predicates with tracked premises

Nothing is asserted anonymously; premise labels must be unique. Both enforced by
lint.

```python
@invariant(id="queue-liveness", needs=[...], uses=[...])
def queue_liveness(f: StackFacts, qbt: QbtInstance) -> Finding:
    return conflict_if(
        premise("qbt.queueing_enabled", qbt.queueing),
        premise("qbt.a_limit_is_binding", qbt.any_active_limit_finite),
        premise("qbt.slow_exempt_off", not qbt.dont_count_slow_torrents),
        premise("qbt.no_global_ratio", not qbt.max_ratio_enabled),
        premise("qbt.no_global_seed_time", not qbt.max_seed_time_enabled),
        premise("qbt.no_category_limits", not qbt.any_category_share_limit),
        premise("arr.indexer_without_goal", f.any_torrent_indexer_lacking_seed_criteria()),
    )
```

**Seed criteria are per *indexer*, not per download client** — verified against
a live Sonarr, where `/api/v3/downloadclient` carries no seed fields at all and
`/api/v3/indexer` carries `seedCriteria.seedRatio`, `seedCriteria.seedTime` and
`seedCriteria.seasonPackSeedTime`.

That granularity changes the predicate. A stack wedges if **any one** enabled
torrent indexer lacks seed criteria, because torrents grabbed from it seed
forever and accumulate in the slots. Requiring *every* indexer to lack them
would miss the mixed case, which is the common one.

`any_active_limit_finite` covers all three real queue limits —
`max_active_downloads`, `max_active_uploads`, `max_active_torrents` — since a
stack can wedge on any one of them, and `0` is a legal binding value.

The premise set that fired **is** the explanation: minimal by construction,
deterministic, ordered as written, trivially unit-testable.

## Outcomes

| Outcome | Meaning |
|---|---|
| `PASS` | Checked, no conflict, every required fact known |
| `FAIL` | Conflict; premises attached |
| `SKIP` | **A required fact is unknown**; carries the `Unknown.reason` — service-absent, field-absent, or insufficient-permission |
| `ERROR` | A configured service is unreachable, unauthorised, banned, or returned an unparseable version |
| `N/A` | Running version falls outside every declared axiom range |

`SKIP` covers the reachable-but-field-absent case: a version that does not expose
a setting, a reduced-scope key, a payload shape change. Transport and auth
failures are `ERROR`. An unparseable version is `ERROR`, not `N/A` — we do not
know that it is out of range, only that we cannot tell.

**Suppression is a flag on a finding, not a sixth outcome.** A suppressed `FAIL`
is still a `FAIL` in the API and history, marked and excluded from the exit code,
retaining its original first-seen date.

**Multi-instance precedence.** Findings are emitted **per instance**, so a
per-instance verdict is never hidden. For the run's exit status they combine as:

```
ERROR > N/A > SKIP > FAIL > PASS
```

**Exit codes**, so CI can distinguish "found a conflict" from "couldn't look":

| Code | Meaning |
|---|---|
| 0 | All checks completed, no unsuppressed `FAIL` |
| 1 | At least one unsuppressed `FAIL` |
| 2 | At least one `ERROR` |
| 3 | Incomplete — `SKIP` or `N/A` present, under `--strict` |

**Declaring a service intentionally absent.** A qBittorrent-only user must not
sit at permanent exit 3. `LINTARR_SERVICES` (or config equivalent) enumerates the
services expected to exist; anything not listed produces no `SKIP` at all.
Suppression is scoped to invariants and does not solve this.

### Vacuity detection lives in CI, not at runtime

An earlier draft specified a runtime sensitivity sweep. It does not work:

- "Dropping a fact" is undefined. If dropped means `known=False`, the verdict
  always becomes `SKIP`, so every fact looks load-bearing and nothing is
  detected.
- **Conjunction masking.** `queue-liveness` is a seven-premise AND. On a healthy
  config where two premises are already false, dropping any single one leaves
  `PASS` — so it flags *every* premise as non-load-bearing. Loudest exactly
  where you want it quiet.
- Vacuity is a property of the invariant, not of one user's config.

**Replacement, deterministic and in CI:** every fact in an invariant's `needs`
must be shown load-bearing by at least one **fixture pair** — two fixtures
differing only in that fact, producing different verdicts. This is the
mechanised form of the mutation discipline, and it catches a lying `needs` list
properly.

### Confidence is derived

A finding's confidence is its weakest axiom's conformance status. Untested
assumptions automatically yield hedged findings. No separate severity system.

## `explain`

Exists to make "it says my config is broken but it isn't" cheap to diagnose.
Such a report is nearly always **our axiom being wrong**, so the output separates
what we read from what we believe:

```
FAIL  queue-liveness  [qbittorrent/main]              confidence: high

  Your settings — read from your stack, check these yourself:
    qbittorrent.max_active_torrents   = 5      GET /api/v2/app/preferences
    qbittorrent.max_ratio_enabled     = false  GET /api/v2/app/preferences
    sonarr[main] 1337x seedRatio      = unset  GET /api/v3/indexer
    sonarr[main] EZTV  seedRatio      = unset  GET /api/v3/indexer

  What we believe about qBittorrent — we could be wrong here:
    seeding torrents occupy an active slot
      conformance-tested against 5.2.3   PASS   2026-08-20
      docs: github.com/qbittorrent/qBittorrent/wiki/...

  Therefore: once 5 torrents finish, no slot is ever released.

  Observed now (corroborating, not part of the finding):
    52 torrents, 29 seeding, 0 active downloads, 0 kB/s

  First seen: 2026-08-04 09:00Z  (22 days ago)
```

A disagreeing user files "your seeding-occupies-slot axiom is wrong on 5.4" —
one line of one file — instead of "tool broken."

## v1 invariant set

Cut in review: **`import-race`** — whether removal beats import depends on ratio
timing versus import scan cadence, all runtime. From config you can establish
only that a hazard *window* exists, true of many healthy stacks.

**1. `queue-liveness`** *(FAIL-grade)* — completed torrents hold active slots
forever, so no queued download can start.

Soundness depends on reading **per-indexer seed criteria** via `/api/v3/indexer`
(see above — *not* the download client), **per-category share limits**, and all
**three** active limits plus `dont_count_slow_torrents`. Global
`max_ratio_enabled=false` alone does not mean seeding is unlimited.

Per-category share limits are read defensively: where the running qBittorrent
does not expose them, the fact is `Unknown("field-absent")` and the invariant
reports `SKIP` rather than guessing. This is the fact discipline earning its
keep on a field whose availability varies by version.

**2. `hardlink-futility`** *(FAIL-grade, P3)* — hardlinks are enabled but library
and download roots are on different filesystems, so every "hardlink" is silently
a copy and disk usage doubles.

A **probe, not a proof**:

- What matters is same-device-ness inside the *arr's* mount namespace, which the
  sidecar cannot stat. The **mount contract** — identical volumes at identical
  paths — is verified, not assumed, by cross-checking free-space bytes:
  `/api/v3/rootfolder`'s `freeSpace` and qBittorrent's `free_space_on_disk`
  against the sidecar's own `statvfs` on the mapped path. Matching byte counts
  are strong evidence of the same filesystem; a mismatch fails the contract
  loudly and the invariant reports `SKIP`, never a verdict.
- Paths resolve through `/api/v3/remotePathMapping` and per-category `savePath`
  before comparison.
- `st_dev` alone is wrong. btrfs subvolumes and ZFS datasets differ on one pool
  where hardlinks genuinely fail, so `st_dev` is right there. But **mergerfs
  presents one `st_dev` while hardlinks across branches misbehave** — a false
  negative, and mergerfs is common here. The check attempts a real `os.link()`
  and cleans up.

**3. `orphan-inevitability`** *(FAIL-grade, P3)* — no *configured* reclamation
path exists: the arr does not remove completed items and qBittorrent's
share-limit action removes the torrent without its content.

Cleanuparr, a cron job or a human are all uncovered paths, so the claim is about
configuration, not the world. Any size reported **must be link-count aware** —
where hardlink imports are in use, download data shares inodes with the library
and naive summing grossly overstates waste.

**4. `seed-goal-conflict`** *(informational)* — the arr's seed criteria and
qBittorrent's share limits disagree; the stricter silently wins. Nothing breaks,
and a conservative global limit alongside per-app goals is a normal deliberate
setup. As `FAIL` it would train users to ignore findings.

**Admission criterion for any future invariant:** determined by configuration
alone, and falsifiable by a conformance test. Apply without exception — every
user request will be for runtime state, and the static boundary is the product.

## Suppression

Per-invariant, with a **required reason** and an **expiry date**. Suppressed
findings still appear, marked, excluded from the exit code, and reappear when the
suppression lapses. Without this, a user who deliberately runs unlimited seeding
gets permanent red and deletes the tool.

## Runtime

**Scheduler.** One asyncio timer, default hourly, plus check-on-start.

**Alerting.** One outbound webhook (generic POST, ntfy-shaped by default),
**edge-triggered with dedup** — fire on transition into `FAIL` or `ERROR`, not
hourly forever. Without it the tool reproduces the failure it exists to catch.

**HTTP API.** Token-authenticated; **binds localhost by default**. Responses
carry a **schema version** from the first release.

```
GET  /api/findings          all findings, with premises and evidence
GET  /api/findings/{id}     one finding, full explanation, first-seen
GET  /api/axioms            axioms with conformance status
GET  /api/health            liveness of the checker itself
POST /api/check             run now
```

**History.** Findings persisted (SQLite) so "when did this start" is answerable.
Axiom and invariant IDs are stable identifiers under a documented policy;
renaming one is a breaking change.

**UI.** Server-rendered HTML, no build step.

**CLI.** `lintarr check`, `explain <id>`, `axioms`, `dump-facts`, `suppress`.

## Testing

**Unit** — every invariant against synthetic fixtures, both directions.

**Mutation discipline** — for each invariant, mutate the guarded setting and
confirm the test goes red. A test asserting `result == FAIL` passes happily
against a predicate that always returns `FAIL`.

**Load-bearing coverage** — the CI fixture-pair assertion replacing the runtime
sweep, above.

**Model validation** — exhaustive sweep plus Hypothesis (Level 1).

**Differential** — simulator versus real container (Level 2, P2).

**Property tests** — never crashes; never `PASS` with an unknown required fact;
every `FAIL` carries a non-empty premise set; every finding's premises are a
subset of its declared `needs ∪ uses`.

**Read-only test** — no adapter emits a non-GET.

## Packaging

PyPI (`uvx lintarr check`) and a GHCR image. MIT. Python 3.13, hatchling, src
layout, click, ruff, pytest, Hypothesis. Dependencies: `httpx`, `click`, a
minimal ASGI stack, stdlib SQLite. **No `z3-solver`** — so no 30–60 MB non-musl
wheel, and an Alpine base stays available.

## Delivery plan

**P0a — collect.** qBittorrent and one arr adapter; auth with ban avoidance;
version detection; multi-instance `StackFacts`; the `Known`/`Unknown` discipline;
read-only test.
*Done when:* `lintarr dump-facts` prints a complete source-annotated snapshot of
a real stack, and every unread field is `Unknown` with a reason.

**P0b — one invariant, end to end.** Premise combinator; `queue-liveness` with
the full three-limit queue model; state machine and exhaustive sweep; outcome
lattice; exit codes; `check`.
*Done when:* two named, scripted scenarios — `scenario/wedged-queue` and
`scenario/healthy-per-app-goals` — give `FAIL` and `PASS` respectively against
both fixtures and a scripted live stack built with the `skip_checking` trick.

**P1 — delivery.** `explain`, authenticated API, UI, persistence, history,
webhook, suppression (which belongs where its store lives).

**P2 — conformance.** Hardlink behaviour first (loop devices, no services), then
`qbt.seeding-occupies-slot`, then the **simulator-versus-container differential**.
Then decide **on evidence** whether arr-lifecycle conformance is affordable given
the TVDB/TMDb dependency — and if not, say so in the README rather than letting
the differentiator quietly not exist.

**P3 — remaining invariants.** Path normalisation (its only consumer is here),
`hardlink-futility` with the free-space mount-contract check, and
`orphan-inevitability` with link-count-aware sizing. `seed-goal-conflict` as
informational.

## Risks

**Fidelity is still the whole ballgame.** Conformance, citations and derived
confidence mitigate; nothing eliminates. Most axioms ship `assumed`.

**Level 1 validation is circular by construction** and cannot find a missing
parameter. Level 2 is the only guard, and it lands in P2 — so between P0b and P2
the flagship invariant rests on one reading of the docs. Accepted knowingly.

**Version drift.** Version ranges, a conformance matrix, and `N/A` keep unknown
versions from becoming false green.

**Scope creep toward monitoring.** The admission criterion is the defence.

**Small honest surface.** Three FAIL-grade invariants and one informational.
Worth building because the failure is silent and expensive, not because it will
be widely adopted.

## Open questions

1. Whether arr-lifecycle conformance is affordable — decided in P2 on evidence.
2. Whether `explain` should ever propose repairs. Deferred, not rejected.
