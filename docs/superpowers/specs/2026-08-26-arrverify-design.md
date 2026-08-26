# arrverify — design

**Status:** draft, revised after independent review
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

That conflict is determined by configuration alone, and is therefore detectable
before it ever manifests.

## What this is

A sidecar container that reads the live configuration of an arr stack, decides
whether the settings can coexist, and reports conflicts in terms of the specific
settings responsible.

## Where it sits in the ecosystem

The novelty claim must be stated narrowly to survive scrutiny.

- **Recyclarr** writes configuration; it does not check consistency.
- **Cleanuparr, Decluttarr, Maintainerr** react to observed runtime state, so by
  construction they engage only after damage.
- **Sonarr and Radarr ship health checks** that already cover several
  cross-service faults: download client unreachable, root folder missing, remote
  path mapping wrong, completed-download-handling disabled. v1 must not
  duplicate these, and the README should list them.

None of the built-in health checks would have caught the motivating incident,
because nothing was downloading and therefore no import failed. The gap is
**static cross-service consistency**, and it is a narrow gap — realistically
eight to twelve configuration-determined impossibilities exist in this
ecosystem. That is a useful linter. It is not a platform.

## Decisions

| Decision | Choice |
|---|---|
| Promise | Zero-*invariant*-config: you configure connections, never rules |
| Delivery | Python sidecar container, API-first, own web UI |
| Reasoning scope | Static, from config; live state is corroborating evidence only |
| Engine | Plain predicates with tracked premises. **No solver.** |
| Temporal properties | Closed form, validated by exhaustive simulation in tests |
| Credentials | API keys required for every service; no auto-discovery |

### Why no solver

An earlier draft used Z3. Review killed it, correctly:

- **Z3 is not a model checker.** It has no temporal operators. "Eventually a
  download starts" cannot be stated in it; you would hand-build a transition
  relation and unroll it, which is writing a model checker badly.
- **Every v1 invariant reduces to a sub-20-line predicate**, three of them to
  single comparisons. There is no search, no combinatorial space, no arithmetic
  that resists evaluation.
- **The one thing a solver offered — unsat cores as explanation — it does worse
  than hand-rolled premise tracking.** Z3's cores are not minimal by default,
  not stable across versions or seeds, unordered, and include the goal
  assertion. A premise combinator is minimal by construction and deterministic.
- **z3py creates variables on reference.** A typo'd `Int('max_activ_torrents')`
  is not an error, it is a fresh unconstrained variable — which makes the system
  more satisfiable, which under the natural polarity means PASS. A silent typo
  becomes a silent green.

**Re-admission gate.** Reintroduce a formal tool only for an invariant whose
state space will not submit to exhaustive simulation at realistic parameters.
At that point the correct tool is **TLA+/TLC**, not an SMT solver.

### How temporal properties are handled

`queue-liveness` asserts something about traces: "some queued download
eventually starts." But its reasoning is a **monotone quantity with an absorbing
state** — seeding torrents never release slots, completed count only rises, and
past a threshold no slot is ever free again. That collapses to a closed form.

The risk is that the derivation becomes folklore. So it does not live in a
comment:

```
tests/model/queue.py        tiny state machine, run to fixpoint
tests/test_queue_model.py   exhaustive sweep: max_active 1..6 x torrents 1..10
                            x every share-limit / seed-goal combination
                            assert predicate(cfg) == simulate(cfg)
                            plus Hypothesis over the ragged edges
```

The temporal reasoning happens once, offline, in tests. The runtime check is the
validated closed form. This is a stronger guarantee than an SMT proof, because
it validates against an executable model rather than against an axiom set that
might be wrong — and it mirrors the conformance discipline one level up:
simulator checked against real qBittorrent, predicate checked against simulator.

## Architecture

```
collect/     per-service adapters -> normalized StackFacts snapshot
theory/      axioms: claims about how each service behaves
invariants/  predicates over facts, with tracked premises
report/      findings, premises, explanations, history
```

Data flows one way. `collect` never imports `theory`; `invariants` never touch
the network. The invariant layer — the one that will grow — is testable against
fixtures with nothing running.

### `collect` — facts, and the absence of facts

**A fact is either read or unknown, never defaulted.** Defaulting an unread
setting to a plausible value is how a verifier produces a confident wrong
answer.

```python
@dataclass(frozen=True)
class Fact[T]:
    value: T | None
    source: str            # "GET /api/v2/app/preferences"
    known: bool
    read_at: datetime
    service_version: str | None
```

`service_version` is load-bearing: the version-ranged axiom story depends on it.

**Adapters are read-only.** Every adapter issues GETs only. This is enforced by
a test asserting no adapter emits a non-GET, not by convention — the sidecar
holds every credential in the stack and must not be able to mutate it.

**`StackFacts` is multi-instance from day one.** Separate 4K and anime Sonarr
instances, and multiple download clients per arr, are the common case.
Retrofitting multiplicity after the invariants are written is expensive.

```python
@dataclass(frozen=True)
class StackFacts:
    arrs: tuple[ArrInstance, ...]        # each with its own download clients
    download_clients: tuple[QbtInstance, ...]
    filesystem: FilesystemFacts
```

Per-category share limits and per-download-client seed goals are modelled
explicitly, because that is exactly where the conflicts live (see
`queue-liveness` below).

### Credentials

API keys are **required** for every service, supplied as environment variables.
There is no Docker socket inspection, no reading `config.xml` off shared mounts,
no auto-discovery. This deletes a class of deployment fragility, and it is what
makes the flagship invariant sound.

**qBittorrent auth needs specific care.** `/api/v2/auth/login` sets an SID
cookie, and qBittorrent enforces `WebUI\MaxAuthenticationFailCount` with an IP
ban (default 3600s). An hourly checker with a wrong password will ban itself —
and behind `network_mode: service:gluetun` it may degrade the stack it is
verifying. Required behaviour:

- reuse the session across a run; do not log in per request
- exponential backoff on auth failure, never a tight retry
- report "we appear to be banned" as a **distinct** `ERROR`, not "bad credentials"
- document the whitelisted-subnet path, where no credentials are needed at all

Credentials never appear in logs, findings, or API responses.

### `theory` — axioms and the fidelity problem

An axiom is a claim about how a service behaves. Every axiom carries the
constraint, a **citation**, a **version range**, and a **conformance status**.

**What is actually conformance-testable — honestly:**

| Axiom family | Testable | Cost |
|---|---|---|
| Hardlink cross-device behaviour | Yes — loop devices, `os.link()`, no services | seconds |
| `qbt.seeding-occupies-slot` | Yes — `mktorrent` a local file, add with `skip_checking` so it goes straight to seeding, set `max_active_torrents=2`, assert the third is `queuedDL` | ~1–3 min |
| qBittorrent share-limit action | Yes, fiddly — must force a ratio via a second client | minutes |
| Sonarr/Radarr import lifecycle | Problematic — needs real metadata lookup (TVDB/TMDb), API keys, indexer stub | tens of minutes, network-dependent |
| Interleaving/race timing | **No** — cannot assert a race deterministically | n/a |

So one family is cheap, one moderate, one fiddly, one drags an external metadata
API into CI, and one is untestable. **Most axiom lines in a report will read
`assumed`, not `conformance-tested`.** That is acceptable and honest, but the
README must say it plainly rather than implying otherwise.

**When a nightly conformance test fails:** the axiom is auto-demoted to
`assumed`, every finding depending on it downgrades in confidence, and the
release is blocked if the axiom is used by a `FAIL`-grade invariant.

### `invariants` — predicates with tracked premises

Nothing is asserted anonymously; enforced by lint. Premise labels must be
**unique**, also enforced.

```python
@invariant(id="queue-liveness", needs=[...], uses=[...])
def queue_liveness(f: StackFacts) -> Finding:
    return conflict_if(
        premise("qbt.queueing_enabled",      f.qbt.queueing),
        premise("qbt.max_active_finite",     f.qbt.max_active.finite),
        premise("qbt.no_global_ratio",       not f.qbt.max_ratio_enabled),
        premise("qbt.no_global_seed_time",   not f.qbt.max_seed_time_enabled),
        premise("qbt.no_category_limits",    not f.qbt.any_category_share_limit),
        premise("arr.no_per_torrent_goals",  not f.any_arr_seed_goal),
    )
```

The premise set that fired **is** the explanation: minimal by construction,
deterministic, ordered as written, trivially unit-testable.

## Outcomes

Five, not three. The extra two exist because collapsing them produces silent
green at exactly the wrong moment.

| Outcome | Meaning | Exit |
|---|---|---|
| `PASS` | Checked, no conflict, every required fact known | 0 |
| `FAIL` | Conflict, premises attached | non-zero |
| `SKIP` | A service is **not configured**; names which | non-zero under `--strict` (default) |
| `ERROR` | A service **is** configured but unreachable, unauthorised, or banned | non-zero |
| `N/A` | Running version falls outside every declared axiom range | non-zero under `--strict` |

Configured-but-unreachable is `ERROR`, never `SKIP`. When the VPN drops and
qBittorrent 502s, that is precisely when the tool should be shouting.

A run in which every check skipped must not exit 0. The summary always prints
counts by outcome.

### Vacuity detection: the sensitivity sweep

A check that passes because its inputs were vacuous is worse than a false
positive, because nobody investigates a pass.

For each fact in an invariant's declared `needs`, re-evaluate with that fact
dropped. **If the verdict does not change, the fact was not load-bearing and the
`needs` list is lying.** Cheap — n extra evaluations on a tiny predicate — and it
is the runtime analogue of the mutation discipline in testing.

### Confidence is derived

Every finding names the axioms it leans on; every axiom has a conformance
status. A finding's confidence is its weakest axiom. Untested assumptions
automatically yield hedged findings. No separate severity system.

## `explain`

Exists to make "it says my config is broken but it isn't" cheap to diagnose.
Such a report is nearly always **our axiom being wrong, not the user's config**,
so the output separates the two:

```
FAIL  queue-liveness                              confidence: high

  Your settings — read from your stack, check these yourself:
    qbittorrent.max_active_torrents   = 5      GET /api/v2/app/preferences
    qbittorrent.max_ratio_enabled     = false  GET /api/v2/app/preferences
    sonarr[main].seed_ratio           = unset  GET /api/v3/downloadclient
    radarr[main].seed_ratio           = unset  GET /api/v3/downloadclient

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

Cut from the previous draft: **`import-race`**. Whether removal beats import
depends on ratio timing versus import scan cadence — runtime, all of it. From
config you can establish only that a hazard *window* exists, which is true of a
large fraction of healthy stacks. High false-positive rate, not conformance-
testable, low actionability.

**1. `queue-liveness`** *(FAIL-grade)* — completed torrents hold active slots
forever, so no queued download can start.

Soundness depends on reading **per-torrent seed goals set by Sonarr/Radarr**
via `/api/v3/downloadclient`, and **per-category share limits** (qBt 4.6+).
Global `max_ratio_enabled=false` alone does not mean seeding is unlimited; a
stack with per-app seed goals is healthy and must not be flagged.

**2. `hardlink-futility`** *(FAIL-grade)* — hardlinks are enabled but library
and download roots are on different filesystems, so every "hardlink" is silently
a copy and disk usage doubles.

This one is a **probe, not a proof**, and needs care:

- What matters is same-device-ness inside the *arr's* mount namespace, which the
  sidecar cannot stat. Requires a documented **mount contract**: the sidecar
  must receive identical volume mounts at identical paths. A violated contract
  silently inverts the verdict, so the contract is checked and reported.
- Paths must be resolved through `/api/v3/remotePathMapping` and qBittorrent's
  per-category `savePath` before comparison.
- `st_dev` alone is wrong. btrfs subvolumes and ZFS datasets report differing
  `st_dev` on one pool where hardlinks genuinely fail — so `st_dev` is right
  there. But **mergerfs presents a single `st_dev` while hardlinks across
  branches misbehave**, a false negative, and mergerfs is common in this
  community. The check must attempt a real `os.link()` and clean up.

**3. `orphan-inevitability`** *(FAIL-grade)* — no *configured* reclamation path
exists: the arr does not remove completed items and qBittorrent's share-limit
action removes the torrent without its content.

Wording matters — Cleanuparr, a cron job, or a human are all uncovered
reclamation paths, so the claim is about configuration, not the world. Any size
reported alongside it **must be link-count aware**: where hardlink imports are
in use, download data shares inodes with the library, and naive summing
overstates waste, sometimes grossly.

**4. `seed-goal-conflict`** *(informational, not FAIL)* — the arr's seed
criteria and qBittorrent's share limits disagree; the stricter silently wins.
Nothing breaks, and a conservative global limit alongside per-app goals is a
normal deliberate setup. Shipping this as `FAIL` would train users to ignore
findings.

**Admission criterion for any future invariant:** determined by configuration
alone, and falsifiable by a conformance test. Apply without exception — every
request from users will be for runtime state, and the static boundary is the
product.

## Suppression

A user who deliberately runs unlimited seeding gets a permanent `FAIL`, a
permanently red exit code, and will delete the tool. Suppression is a v1
requirement, not a nicety: per-invariant, with a **required reason** and an
**expiry date**. Suppressed findings still appear, marked and excluded from the
exit code, and reappear when the suppression lapses.

## Runtime

**Scheduler.** One asyncio timer, default hourly, plus check-on-start. Not
cron-flexible in v1.

**Alerting.** One outbound webhook (generic POST; ntfy-shaped by default),
**edge-triggered with dedup** — fire on transition into `FAIL` or `ERROR`, not
hourly forever. Without this the tool reproduces the exact failure it exists to
catch: a dashboard nobody opens.

**HTTP API.** Authenticated by token; **binds localhost by default**.

```
GET  /api/findings          all findings, with premises and evidence
GET  /api/findings/{id}     one finding, full explanation, first-seen
GET  /api/axioms            axioms with conformance status
GET  /api/health            liveness of the checker itself
POST /api/check             run now
```

Responses carry a **schema version** from the first release.

**History.** Findings are persisted (SQLite) so "when did this start" is
answerable — the first question anyone asks about a silent failure. Axiom and
invariant IDs are stable identifiers under a documented policy; renaming one is
a breaking change.

**UI.** Server-rendered HTML, no build step: findings list, explain view, axiom
table.

**CLI.** `arrverify check`, `explain <id>`, `axioms`, `suppress`. Non-zero exit
per the outcome table, so it composes with cron and CI regardless of the UI.

## Testing

**Unit** — every invariant against synthetic `StackFacts` fixtures, both
directions: `FAIL` on known-bad, `PASS` on known-good.

**Mutation discipline** — an invariant test only ever watched to pass proves
nothing. For each invariant, mutate the guarded setting and confirm the test
goes red. A test asserting `result == FAIL` will happily pass against a
predicate that returns `FAIL` for everything.

**Model validation** — the exhaustive simulator sweep plus Hypothesis, as above.
This is where the temporal arguments are actually discharged.

**Property tests** — over generated `StackFacts`: never crashes; never `PASS`
with an unknown required fact; every `FAIL` carries a non-empty premise set; and
**every finding's premises are a subset of its declared `needs ∪ uses`**, which
catches an invariant quietly leaning on an undeclared axiom.

**Read-only test** — no adapter emits a non-GET.

**Conformance** (Docker; nightly and on release tags) — real containers, each
axiom asserted empirically, cheapest first. Kept off the per-push path because
it is slow and flaky.

## Packaging

PyPI (`uvx arrverify check`) and a GHCR image. Python 3.13, hatchling, src
layout, click, ruff, pytest, Hypothesis. Dependencies: `httpx`, `click`, a
minimal ASGI stack, SQLite via stdlib. **No `z3-solver`** — which also means no
30–60 MB non-musl wheel, so an Alpine base stays available.

## Delivery plan

Four sequenced sub-projects. The riskiest — conformance — is retired third, not
last, because it is the credibility claim.

**P0 — Facts, adapters, one invariant.** qBittorrent, Sonarr, Radarr adapters;
version detection; path normalisation through remote path mappings and category
save paths; multi-instance `StackFacts`; the full outcome lattice and exit
codes; read-only guarantee and its test; qBittorrent auth with ban avoidance;
suppression. Ship `queue-liveness` with its simulator validation.
*Done when:* it reproduces the motivating incident from a fixture and from a
live stack, **and stays quiet on a stack where the arr apps set per-torrent seed
goals.*

**P1 — Explanation and delivery.** Premise combinator, sensitivity sweep,
`explain`, CLI, authenticated HTTP API, static UI, one webhook, persisted
history, versioned findings schema.

**P2 — Conformance harness.** Hardlink behaviour first (loop devices, no
services), then `qbt.seeding-occupies-slot`. Then decide **on evidence** whether
arr-lifecycle conformance is affordable given the TVDB/TMDb dependency — and if
not, say so in the README rather than letting the differentiator quietly not
exist.

**P3 — Remaining invariants.** `hardlink-futility` (needs P0 path work, the
mount contract, and a real `os.link()` probe) and `orphan-inevitability` (needs
link-count-aware sizing). `seed-goal-conflict` as informational.

**Future — formal tooling,** only if the re-admission gate is met, and then
TLA+/TLC rather than an SMT solver.

## Risks

**Fidelity is still the whole ballgame.** Conformance tests, citations and
derived confidence mitigate it; nothing eliminates it. Most axioms will ship as
`assumed`.

**The mount contract for `hardlink-futility`** is an unenforced deployment
requirement whose violation inverts a verdict. Checked and reported, but a real
sharp edge.

**Version drift.** Axiom version ranges and a conformance matrix help; the
`N/A` outcome keeps unknown versions from becoming false green.

**Scope creep toward monitoring.** Every user will ask for runtime checks
overlapping Cleanuparr. The admission criterion is the defence.

**Small honest surface.** After cuts, v1 is three FAIL-grade invariants and one
informational. That is a useful, well-tested linter with citations — worth
building because the failure it catches is silent and expensive, not because it
will be widely adopted.

## Open questions

1. Name.
2. Whether arr-lifecycle conformance is affordable, decided in P2 on evidence.
3. Whether `explain` should ever propose repairs. Deferred, not rejected.
