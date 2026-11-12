# P2 slice 1: simulator vs. real qBittorrent (the Level 2 differential)

**Status:** draft for Tom's approval (builder-checked), 2026-11-12 (UTC)
**Parent spec:** `2026-08-26-lintarr-design.md` (sections "How temporal
properties are handled", "theory", "Delivery plan / P2").

## Why

`queue-liveness` is the only check lintarr runs, and it is only as right as one
reading of the qBittorrent docs. The Level 1 sweep (`tests/model/queue.py`
against the predicate) proves the closed form was *derived* correctly from the
model. It cannot prove the *model* is right: a belief the author holds enters
both, and they agree while both are wrong. Most sharply, a sweep over the
predicate's own parameters cannot discover a parameter the predicate is
missing.

This slice is the first mechanism that can catch that: drive a **real
qBittorrent** through scripted scenarios and compare what it does, step by
step, with what the simulator says it should do.

Tom chose P2 ahead of P1 on 2026-11-12 because every later check would build
on the same untested model.

## Goals

1. A nightly run drives the **same qBittorrent build homelab runs**
   (`lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.14-ls476`) through 6–8
   scenarios. At every step it asserts that the observed
   `(downloading, queued, seeding)` counts equal the simulator's.
2. When the run fails, an ntfy push goes to topic **`homelab-alerts`** with a
   link to the run. The message says whether this was a **disagreement** (the
   model is wrong) or a **harness error** (the container or the network broke).
3. The same command runs on Tom's Mac with Docker, so a disagreement can be
   reproduced locally.
4. The `qbt.seeding-occupies-slot` axiom gets a direct conformance test, since
   it falls out of the same harness.

## Non-goals (this slice)

- **No axiom registry or quarantine machinery** (option C from the
  conversation). lintarr's live output does not learn the nightly result. That
  belongs with P1/P3, which need a persistent store anyway.
- **No GitHub issue** on failure. Tom's call: the ntfy push links the run, and
  the run log holds the trace.
- **No hardlink conformance.** Nothing consumes it until P3's
  `hardlink-futility`.
- **No Sonarr/Radarr lifecycle conformance.** The parent spec's open question 1
  stays open.
- **No version-drift warning** in `lintarr check` (live qBittorrent version ≠
  conformance-tested version). It is a natural follow-up, not part of this.
- **No change to the predicate**, unless the differential finds a
  disagreement. A disagreement is a new issue, not something this slice fixes.

## Anti-goals (would make this a failure even if it "works")

- **Loosening the read-only guarantee.** `tests/test_readonly_guarantee.py`
  pins that no `src/` adapter emits a non-GET. The harness must write
  (set preferences, add torrents), so it lives entirely under `tests/` with its
  own client. `src/` gains no write path.
- **The simulator importing the harness's observations, or the predicate.**
  Then it agrees by construction, which is the circularity this slice exists to
  break.
- **A green that proves nothing**, e.g. a scenario that never reaches the state
  it claims to test. Every scenario asserts that it reached its target state
  before comparing, and the suite carries a mutation round (see Checks).
- **Flaky red nights.** Each scenario gets one automatic retry before it counts
  as a disagreement. A persistent disagreement still pages every night, which
  is intended: the model is wrong until fixed.
- **Making the normal `pytest -q` run need Docker.** The container tests are
  opt-in by marker. PR CI and the pre-push battery stay as fast as today.

## Constraints

- Public repo, so GitHub-hosted runner minutes are free. `ubuntu-latest` has
  Docker.
- Actions pins follow `tests.yml`'s convention: full SHAs with a version
  comment.
- Python 3.13, uv, pytest, httpx are already dependencies. **No new runtime
  dependency.** At most one new dev dependency, and none is expected:
  container control goes through the Docker CLI via `subprocess`, and test
  torrents are bencoded in a few lines of Python.
- ntfy runs with deny-all default access, so the workflow needs a repo secret
  **`NTFY_TOKEN`**. Only Tom can set it (the bot App token 403s on secrets).
  The push must check the HTTP status (`curl --fail-with-body`). A bare
  `curl -s` exits 0 on a 403, and then the alert silently reaches nobody.
- Homelab's alert path uses `http://localhost:2586`. From Actions the push goes
  to the public host `https://notifications.tomclancy.info/homelab-alerts`.

## Approaches considered

1. **pytest suite that starts the container itself (chosen).** Container tests
   under `tests/conformance/`, marked `container`, excluded from the default
   run. A fixture starts the pinned image with the Docker CLI and drives the
   Web API with httpx. The same command works locally and nightly. Cost: the
   simulator needs a step-wise trace function.
2. **Standalone script writing a JSON report.** Easier to read in isolation,
   but it can't reuse the fixtures and Hypothesis strategies, and "failed"
   becomes a home-grown exit-code convention.
3. **Run it on homelab next to production qBittorrent.** It tests the exact
   host, but it puts a throwaway torrent client beside production, plus Docker
   and port housekeeping on the box. The pinned image tag already gives the
   version fidelity.

Tom picked 1 on 2026-11-12.

## Design

### Pieces

| Piece | Where | What it does |
|---|---|---|
| Step-wise model | `tests/model/queue.py` | New `trace(cfg, events) -> list[Counts]`. The existing `simulate()` stays and keeps its contract. A Level 1 test asserts `simulate(cfg, n)` equals "the last trace step is wedged" across the existing sweep box, so the two cannot drift. |
| Harness client | `tests/conformance/qbt.py` | Minimal read-write httpx client: login, `setPreferences`, add torrent (multipart, `skip_checking`, `savepath`), `torrents/info`, `delete`. Lives in `tests/` only. |
| Torrent maker | `tests/conformance/torrents.py` | Builds a single-file `.torrent` (bencode, fixed piece size) from bytes. Torrents are either **complete** (data written to the container's save path, added with `skip_checking=true`, so they go straight to seeding) or **incomplete** (no data, so they need peers that never come and sit downloading or queued). |
| Container fixture | `tests/conformance/conftest.py` | Session-scoped. `docker run` the pinned image on a random free port with a temp volume, wait for the WebUI, read the generated admin password from the container log (linuxserver prints it on first start), log in. A clean qBittorrent state per scenario by deleting all torrents and resetting preferences. Tears down even on failure. |
| Scenarios | `tests/conformance/test_differential.py` | 6–8 named scenarios, each a `(QueueConfig, population, events)` triple, parametrised over `dont_count_slow_torrents ∈ {on, off}`. |
| Nightly workflow | `.github/workflows/conformance.yml` | `schedule` (daily, off the hour) plus `workflow_dispatch`. Runs `pytest -m container`. On failure, a final step pushes to ntfy. |

### What `trace()` encodes, and who decides it

*(Claude's call, from the builder check.)* `simulate()` only tells one story:
downloads finish and become seeders. The container tells a different one:
complete torrents arrive already seeding, and incomplete ones never finish. So
`trace()` needs rules `simulate()` never stated, and **whoever writes those
rules is writing the model under test.**

Rule: `trace()` encodes **the beliefs the shipped predicate already holds**,
each cited to the `queue_liveness.py` docstring or comment that states it. It
does not encode the builder's own reading of qBittorrent. Concretely, as of
`main` today:

1. Every seeding torrent occupies a `max_active_torrents` slot.
2. `max_active_uploads` is not consulted (the predicate does not declare it).
3. With the slow-torrent exemption on, slow torrents count against no limit, seeders included. The predicate makes "exemption off" a premise of the seeder wedge (`queue_liveness.py:1042`). *(Corrected while planning: an earlier draft said "downloads only", which was the simulator's comment, not the predicate's belief.)*
4. With a share limit enabled, a seeder that meets it leaves and frees its slot.
5. `queueing_enabled=false` means nothing queues.

These rules land in **their own commit, before the harness first talks to a
container**, so git shows they were not fitted to observations. Where the
builder believes a rule is wrong (the reviewer suspects 2 is), the rule
**stays as the predicate believes it**. The scenario is expected to disagree,
and that disagreement is the finding. See "Known disagreements" below.

### Observed buckets

The model's buckets are `downloading`, `queued`, `seeding` and `left`.
`queuedUP` is **not** folded into `seeding`. It gets its own observed bucket,
`seeding_queued`, so a disagreement about the upload limit shows up as itself
rather than as a wrong seeding count. The model predicts `seeding_queued = 0`
everywhere, because of rule 2.

### How a scenario runs

1. Set preferences from the scenario's `QueueConfig`. Also set
   `slow_torrent_inactive_timer` to its minimum, so slow classification takes
   seconds, not 60 s. Every torrent here has no peers and so is slow by
   qBittorrent's definition. That makes `dont_count_slow_torrents` live in
   every scenario, which is why each one runs with it both on and off.
2. Apply each event in order: add N complete torrents, add N incomplete
   torrents, or remove one.
3. After each event, wait until qBittorrent's counts are **stable** (unchanged
   across two polls after the slow timer has elapsed), with a hard timeout.
   Map qBittorrent states to the model's buckets:
   - `downloading`/`stalledDL`/`metaDL`/`forcedDL` → downloading
   - `queuedDL` → queued
   - `uploading`/`stalledUP`/`forcedUP` → seeding
   - `queuedUP` → seeding_queued
   - `pausedUP`/`stoppedUP`, **or a harness-added torrent that is gone from
     `torrents/info`** → **left**. A share-limit action of 1 or 3 removes the
     torrent, so "left" has to include torrents that vanished.

   Any state outside this table fails the scenario with kind
   **`unmapped-state`**. That is its own kind, neither a disagreement nor a
   harness error, because it is a model finding (the model has no bucket for
   it) and not infrastructure noise. It gets the same retry-then-page path.
4. Compare against `trace()`'s counts for that step. On mismatch, the assertion
   message prints the full config, the step, both count triples and the raw
   per-torrent states, so the run log alone is enough to reproduce.

### Scenario set (initial; builder may adjust within the intent)

1. **The homelab#393 wedge:** the `_WEDGED` values from
   `tests/fixtures/homelab.py`, unchanged, 5 complete + 3 incomplete. The model
   expects the 3 incomplete to stay queued. Note: `max_active_uploads=3` there
   is **not** from #393 (it was never recorded) but inherited from the
   repaired config. If qBittorrent queues seeders beyond the upload limit and
   they free their torrent slots, this scenario disagrees on night one. That
   would also say the real incident must have had uploads ≥5 or unlimited.
   Add a sibling **1b** with `max_active_uploads=-1`, so the wedge itself is
   tested regardless.
2. **The repaired homelab config**, with one substitution *(Claude's call)*:
   its share limits (ratio 1.5, 14 days) cannot fire within a nightly run with
   zero upload. So `max_ratio=0` and `max_seeding_time` disabled replace them,
   keeping the real `max_ratio_act=3` (remove with files). Every other key is
   `_REPAIRED`'s. The model expects no torrent left queued. That the real
   limits *eventually* fire is the predicate's claim and is out of reach for a
   container test; that stays a documented gap.
3. **`max_active_downloads=0`:** wedged immediately, even with zero seeders.
4. **Unlimited sentinels** (`-1` on all three): nothing ever queues.
5. **Downloads limit binds, torrents limit doesn't:** queueing caused by
   downloaders alone, not seeders.
6. **Share limit releases the slot:** `max_ratio_enabled`, `max_ratio=0`,
   action = stop. Expect seeders to move to **left** and the queue to drain.
   *Unverified assumption:* that a ratio limit of `0` fires on a just-finished
   torrent with zero upload. Build step 1 confirms this. If false, scenario 6
   uses `max_seeding_time=0` instead, or is dropped and recorded as a known
   gap.
7. **Remove a seeder:** the queue advances by exactly one.
8. **`queueing_enabled=false`:** limits ignored, nothing queues.

### Failure alerting

The final workflow step runs `if: failure()`. It posts a short text body to
ntfy with a title, a priority (`high`), the run URL, and a one-word kind:
`disagreement` (a count mismatch), `unmapped-state` (a state the model has no
bucket for), or `harness-error` (anything else: image pull, container start,
API failure, timeout). pytest writes a small
`conformance-result.json` that the alert step reads, rather than parsing logs.
If `NTFY_TOKEN` is empty, the step fails loudly with that as the reason, never
a silent skip.

### Known disagreements

*(Tom's call, 2026-11-12.)* Once a disagreement has been seen and understood,
it is recorded as **known**, and the nightly pages only on new changes.

Mechanism *(Claude's call)*. Plain `xfail` is too loose: it stays green on
*any* failure, including a different one. Instead there is one registry,
`KNOWN_DISAGREEMENTS` in `tests/conformance/known.py`, keyed by scenario ID.
Each entry records:

- the **observed** count triple(s) at the disagreeing step,
- a one-line `why` (which model rule is believed wrong),
- `tracked_in`: where the fix is tracked. Default: an item under
  `CONTINUATION.md` "Open items".

For a known scenario, the test asserts **observed == the recorded
observation**, and that the recorded observation still differs from the
model's prediction. So it goes red, and pages, when:

- qBittorrent behaves differently from the recorded observation (version
  drift or a flaky world), or
- the model is fixed (it now matches the observation, so the entry is stale
  and must be removed), or
- an entry has an empty `why` or `tracked_in` (a unit test checks that).

A newly found disagreement is never auto-recorded. A person adds the entry in
a PR, which is the "seen and understood" step.

## Checks (each passes only if its requirement is met)

| # | Requirement | Check |
|---|---|---|
| R1 | The real container is driven through ≥6 scenarios, both slow-torrent settings. | `uv run pytest -m container --collect-only -q` lists ≥12 differential test IDs; a local run passes. |
| R2 | The pinned image equals homelab's. | A unit test asserts the conformance image constant equals the string `5.2.3_v2.0.14-ls476`. The constant carries a comment naming homelab as the source of truth. |
| R3 | Step-wise model agrees with the existing simulator where both describe the same world. | Level 1 test over the full sweep box (dl, ul, tot each in `{0, 1..6, -1}`, slow and share each on/off, queueing on, n in 1..10): `events = [AddIncomplete(n)] + [FinishDownloads()] * (n + 1)`, and `(trace(cfg, events)[-1].queued > 0) == simulate(cfg, n)`. The docstring states the mapping: simulate()'s world is n incomplete torrents that finish; `FinishDownloads` (model-only) replays it. |
| R4 | A wrong model goes red. | Mutation round: flip one model rule (e.g. seeders don't count against `max_active_torrents`). Scenario 1b, or whichever scenario Task 5 Step 7 selects, goes red against the container. Restore. Record in the PR body. |
| R5 | An unmapped qBittorrent state cannot pass. | A unit test feeds the state mapper an unknown state and raises `UnmappedState`, kind `unmapped-state`. |
| R6 | Normal test runs need no Docker. | `uv run pytest -q` on a machine with Docker stopped passes, and the container tests are deselected, not skipped-as-passing. |
| R7 | `src/` stays read-only. | `tests/test_readonly_guarantee.py` passes unchanged; `git diff main -- src/` is empty. |
| R8 | Nightly failure reaches `homelab-alerts`. | `workflow_dispatch` with an env flag that forces one scenario to fail produces a push on Tom's phone. Then a clean dispatch run is green. |
| R9 | A missing ntfy token is loud. | `workflow_dispatch` before `NTFY_TOKEN` is set: the alert step fails with a message naming the missing secret. |
| R10 | Share-limit assumption is verified, not assumed. | Build step 1 records the observed result for `max_ratio=0` in `docs/measurements/` before scenario 6 is written. |
| R11 | A known disagreement stays green only while it fails the recorded way. | Unit tests with a fake observer: (a) observed == recorded and ≠ model → pass; (b) observed changes → fail; (c) model now matches → fail as a stale entry; (d) empty `why`/`tracked_in` → fail. |

## Tom's steps

1. Add repo secret `NTFY_TOKEN` on `tclancy/lintarr`, using your existing ntfy
   token: `gh secret set NTFY_TOKEN --repo tclancy/lintarr`, which prompts
   without echoing.
2. When homelab's qBittorrent image is bumped, bump the pin here too. R2's test
   makes the constant easy to find. Deriving it automatically from homelab is
   a follow-up.

## Left to the builder

- Exact cron time, polling interval, timeouts, and the retry mechanism. A small
  retry wrapper is fine; a plugin dependency needs a reason.
- How the fixture learns the WebUI password. The container log is expected;
  setting it through `qBittorrent.conf` on the mounted volume is fine too.
- Scenario details, within the intent of each named scenario.
- If build step 1 shows that a state or a preference behaves differently than
  this doc assumes, stop and report. Don't adapt the model to match. That
  finding is the point of P2.
