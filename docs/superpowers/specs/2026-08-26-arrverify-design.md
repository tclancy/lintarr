# arrverify — design

**Status:** draft for review
**Date:** 2026-08-26
**Name:** `arrverify` is provisional.

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

That conflict is provable from configuration alone, before it ever manifests.
Nothing in the ecosystem does this today: Cleanuparr, Maintainerr and Decluttarr
all react to observed state, so by definition they engage only after the damage.

## What this is

A sidecar container that reads the live configuration of an arr stack, proves
whether the settings can coexist, and reports conflicts in terms of the specific
settings responsible.

Z3 is an implementation detail. Users need no formal-methods knowledge.

## Decisions already settled

| Decision | Choice |
|---|---|
| Core promise | Zero-config doctor with a curated invariant library |
| Delivery | Python sidecar container, API-first, own web UI |
| Reasoning scope | Static proof from config; live state attached as corroborating evidence only |
| Core architecture | Shared stack theory, invariants as queries against it |
| Explanation | Tracked assertions + unsat cores; `explain` is first-class |

Non-goals for v1: repairing config, replacing Cleanuparr's runtime monitoring, a
user-facing invariant DSL, and any notification backend beyond the HTTP API.

## Architecture

Four layers, each independently testable.

```
collect/     per-service adapters -> normalized StackFacts snapshot
theory/      Z3 axioms: how each service actually behaves
invariants/  short queries over theory + facts
report/      findings, unsat cores, explanations
```

Then a thin runtime around them: a scheduler, an HTTP API, and a static UI.

Data flows one way. `collect` never imports `theory`; `invariants` never talk to
the network. This keeps the whole invariant layer testable against fixtures with
no services running, which matters because that is the layer that will grow.

### `collect` — facts, and the absence of facts

Each adapter turns one service's API into part of a `StackFacts` snapshot.

The load-bearing rule: **a fact is either read or unknown, never defaulted.**

```python
@dataclass(frozen=True)
class Fact:
    value: object | None
    source: str          # "GET /api/v2/app/preferences"
    known: bool
```

Defaulting an unread setting to a plausible value is how a verifier produces a
confident wrong answer. An unknown input makes any invariant depending on it
`SKIP`, never `PASS` (see Skip semantics).

Adapters for v1: qBittorrent, Sonarr, Radarr. Cleanuparr is deferred — its
configuration surface is less documented and it is not needed for the v1
invariants.

Filesystem facts (device ids behind the library and download roots) are
collected too. They are facts about deployment, not transient state, so they are
legitimate proof inputs rather than mere evidence.

### `theory` — the axioms, and the fidelity problem

This is where the credibility of the whole tool lives, so it gets the strictest
discipline in the codebase.

An axiom is a claim about how a service behaves — for example, that seeding
torrents occupy an active slot. Every axiom carries:

- the constraint it contributes
- a **citation**: documentation link or recorded observation
- a **version range** it is believed to hold for
- a **conformance status**, set by the suite below

```python
Axiom(
    id="qbt.seeding-occupies-slot",
    applies_to=VersionRange("qbittorrent", ">=4.1"),
    cite="https://github.com/qbittorrent/qBittorrent/wiki/...",
    constraint=lambda f: active_count == downloading + seeding,
)
```

**Conformance tests are the point.** A suite runs against real containers in CI,
configures them, and asserts each axiom empirically. `qbt.seeding-occupies-slot`
is directly testable: set `max_active_torrents=2`, seed two torrents, queue a
third, observe that it never starts.

Axioms that cannot be conformance-tested are marked `assumed` and say so in every
report they touch. A wrong verdict then traces to a stated belief rather than
hiding inside a solver call.

This is the main thing peers do not do. Encoding assumptions once and never
rechecking them is how a linter becomes confidently wrong two releases later.

### `invariants` — the queries

Each invariant is a small module: a plain-English statement, the facts it needs,
the axioms it leans on, and a query.

```python
Invariant(
    id="queue-liveness",
    says="Some queued download can eventually start.",
    needs=["qbt.max_active_torrents", "qbt.max_ratio_enabled", ...],
    uses=["qbt.seeding-occupies-slot", "qbt.no-share-limit-means-permanent-seed"],
)
```

A conflict is reported when the solver proves the invariant's goal
**unsatisfiable** given the configuration — the config makes progress
impossible, regardless of queue contents.

### `report` — tracked assertions and cores

Every assertion is tracked with a label naming its origin. Nothing is asserted
anonymously; this is enforced by a lint rule, not convention.

```python
s.assert_and_track(max_active == 5,   "qbittorrent.max_active_torrents")
s.assert_and_track(Not(share_limits), "qbittorrent.max_ratio_enabled")
s.assert_and_track(seeding_holds_slot, "axiom.qbt.seeding-occupies-slot")
```

The unsat core then *is* the explanation — the minimal set of inputs that cannot
coexist. No natural-language generation from proof terms, and a finding cannot
drown its own evidence in forty irrelevant settings.

## `explain` — the anti-false-positive mechanism

`explain` exists to make "it says my config is broken but it isn't" cheap to
diagnose. Such a report is nearly always **our axiom being wrong, not the user's
config**, so the output separates the two:

```
FAIL  queue-liveness

  Your settings — read from your stack, check these yourself:
    qbittorrent.max_active_torrents  = 5      GET /api/v2/app/preferences
    qbittorrent.max_ratio_enabled    = false  GET /api/v2/app/preferences

  What we believe about qBittorrent — we could be wrong here:
    seeding torrents occupy an active slot
      conformance-tested against 5.2.3   PASS   2026-08-20
      docs: github.com/qbittorrent/qBittorrent/wiki/...

  Therefore: once 5 torrents finish, no slot is ever released.

  Observed now (corroborating, not part of the proof):
    52 torrents, 29 seeding, 0 active downloads, 0 kB/s
```

A disagreeing user files "your seeding-occupies-slot axiom is wrong on 5.4" —
one line of one file — rather than "tool broken."

`explain` also runs on **passing** invariants, which is how vacuous passes get
caught: an empty premise set is visible immediately.

### Confidence is derived, not configured

Every finding names the axioms in its core, and every axiom has a conformance
status. A finding's confidence is the weakest axiom it depends on. An untested
assumption automatically yields a hedged finding. There is no separate severity
system to maintain.

### Skip semantics

Three outcomes, not two:

- `PASS` — proved, with every required fact known
- `FAIL` — conflict proved, core attached
- `SKIP` — a required fact was unknown; names which one and why

A silent green on missing input is worse than a false positive, because nobody
investigates a pass.

## v1 invariant set

Five, each drawn from a real observed failure and each provable from config.

1. **queue-liveness** — completed torrents hold active slots forever, so no
   queued download can start. *(The motivating incident.)*

2. **import-race** — content removal can fire while an import is still pending,
   destroying the only copy. Depends on whether hardlink imports are in use.

3. **hardlink-futility** — hardlinks are enabled but the library and download
   roots are on different filesystems, so every "hardlink" is silently a copy
   and disk usage doubles. Common, invisible, and provable from device ids.

4. **seed-goal-conflict** — the arr app's seed criteria and qBittorrent's share
   limits disagree; the stricter silently wins, so the user's stated intent is
   unachievable.

5. **orphan-inevitability** — nothing in the configuration reclaims download
   data: the arr app does not remove completed items and qBittorrent's share
   limit action removes the torrent without its content. Data accumulates with
   no path back. *(Observed: 546 GB.)*

Backlog: category/path mismatches against remote path mappings, indexer
retention versus seed-goal conflicts, and Cleanuparr rules once its config
surface is modelled.

## Runtime

**Scheduler.** A single asyncio timer, default hourly, plus check-on-start.
Deliberately not cron-flexible in v1.

**HTTP API.** The engine's public surface, so a Jellyfin plugin or any other
client can be a thin consumer later:

```
GET /api/findings          all findings with cores and evidence
GET /api/findings/{id}     one finding, full explanation
GET /api/axioms            axioms with conformance status
GET /api/health            liveness of the checker itself
POST /api/check            run now
```

**UI.** Server-rendered HTML, no build step. Findings list, per-finding explain
view, axiom table. Deliberately small.

**CLI.** `arrverify check`, `arrverify explain <id>`, `arrverify axioms`. Exits
non-zero on any `FAIL`, so it composes with cron and CI regardless of the UI.

## Configuration

Environment variables, matching arr-stack convention:

```yaml
arrverify:
  image: ghcr.io/tclancy/arrverify
  ports: ["9494:9494"]
  environment:
    QBIT_URL: http://gluetun:8080
    QBIT_USER: admin
    QBIT_PASS: ${QBIT_PASS}
    SONARR_URL: http://sonarr:8989
    SONARR_API_KEY: ${SONARR_API_KEY}
```

Any service left unconfigured is absent, and invariants needing it `SKIP`.
Credentials are never logged and never appear in findings or the API.

## Testing

**Unit** — every invariant against synthetic `StackFacts` fixtures, in both
directions: it must `FAIL` on a known-bad config and `PASS` on a known-good one.

**Mutation discipline** — an invariant test that has only been watched to pass
proves nothing. For each invariant, mutate the guarded setting and confirm the
test goes red. Applies with force here: a test asserting `result == FAIL` will
happily pass against a solver that returns `FAIL` for everything.

**Property tests** (Hypothesis) — over randomly generated `StackFacts`: the
checker never crashes, never emits `PASS` when a required fact is unknown, and
every `FAIL` carries a non-empty core.

**Conformance** (Docker, nightly and on release tags) — real containers, each
axiom asserted empirically. Slower and flakier than unit tests, so kept on its
own schedule; unit and property tests run on every push.

## Packaging

PyPI (`uvx arrverify check`) and a GHCR container image. Python 3.13, hatchling,
src layout, click, ruff, pytest — matching existing conventions. Dependencies:
`z3-solver`, `httpx`, `click`, and a minimal ASGI stack.

## Risks

**Model fidelity is the whole ballgame.** A confidently wrong verdict is worse
than no tool. Mitigated by conformance tests, visible citations, and derived
confidence — but never eliminated.

**Version drift.** qBittorrent and the arr apps change behaviour between
releases. Mitigated by version ranges on axioms and by conformance running
against a matrix; not solved.

**Scope creep toward monitoring.** Every user will ask for runtime checks that
overlap Cleanuparr. The static-proof boundary is the product's distinguishing
claim and should be defended.

**Small audience.** This is a narrow tool for a niche stack. Worth building
because the failure it catches is silent and expensive, not because it will be
widely adopted.

## Open questions

1. Name.
2. Should `explain` eventually propose repairs? Z3 can compute minimal repairs,
   but that is auto-repair wearing a hat, and is the feature most likely to
   confidently suggest the wrong thing. Deferred, not rejected.
3. How far to push the conformance matrix across versions before the CI cost
   outweighs the confidence gained.
