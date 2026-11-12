# P2 slice 1: container differential — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A nightly GitHub Actions job drives a real, pinned qBittorrent
through scripted scenarios. At each step it asserts that the observed queue
counts equal a step-wise model of what lintarr's `queue-liveness` predicate
believes. Failures push to ntfy `homelab-alerts`.

**Architecture:** A new step-wise model, `trace()`, lives next to
`simulate()` in `tests/model/queue.py`, and encodes the predicate's stated
beliefs, each with a citation. A harness under `tests/conformance/` (its own
read-write httpx client, a bencode torrent maker, a Docker-CLI container
fixture) runs scenarios against `lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.14-ls476`.
A registry of known disagreements keeps understood findings from paging. A
new workflow, `conformance.yml`, runs `pytest -m container` nightly and posts
to ntfy on failure.

**Tech Stack:** Python 3.13, uv, pytest, hypothesis, httpx (all present),
Docker CLI, GitHub Actions, ntfy.

**Spec:** `docs/superpowers/specs/2026-11-12-p2-container-differential-design.md`

## Global Constraints

- `src/` is not modified. `git diff main -- src/` must be empty at the end (R7).
- No new runtime dependency. No new dev dependency without a written reason
  in the PR body.
- Container tests carry the `container` marker and are deselected by default:
  plain `uv run pytest -q` must not need Docker (R6).
- Image pin: `lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.14-ls476`, exactly (R2).
- ntfy target: `https://notifications.tomclancy.info/homelab-alerts`, with
  `Authorization: Bearer $NTFY_TOKEN`. Push with `curl --fail-with-body`,
  never a bare `curl -s` (it exits 0 on a 403).
- Failure kinds, exactly: `disagreement`, `unmapped-state`, `harness-error`.
- Action pins in workflows: full commit SHA plus a version comment, copied
  from `.github/workflows/tests.yml` for the same actions.
- Timestamps UTC. Branch names `claude/…`. Pre-commit must pass before every
  commit; never `--no-verify`.
- **Stop rule:** if any observation (Task 4 onwards) contradicts what this
  plan or the spec assumes about qBittorrent, stop and report. Do not adapt
  `trace()` to match. That finding is the point of P2.

## Review Focus

1. **The WebUI isn't ready when the fixture first polls** (the linuxserver
   image takes several seconds to init). Expected: the fixture waits with a
   bounded timeout and raises a `HarnessError`, not a connection error that
   reads as a disagreement. Pinned in Task 3.
2. **A torrent added with `skip_checking` over data the container cannot see**
   (wrong mount or permissions) sits in `missingFiles` or `checkingUP`.
   Expected: `unmapped-state`, not a silent wrong bucket. Pinned in Task 2
   (mapper) and Task 3 (the smoke test asserts one complete torrent reaches a
   seeding state).
3. **Leftover torrents from a previous scenario** (or from a retry) inflate the
   counts. Expected: the per-scenario reset verifies `torrents/info` is empty
   before it proceeds. Pinned in Task 3.
4. **The alert step itself failing silently**: an empty secret, a 403, or a
   bad JSON result file. Expected: the step exits non-zero with the reason in
   the log. Pinned in Task 6.
5. **A count that hasn't settled yet** (the slow timer is still running when
   compared). Expected: stability means unchanged over two polls taken after
   the slow timer elapsed, with a timeout giving `harness-error`. Pinned in
   Task 5.

---

### Task 1: Step-wise model `trace()` (own commit, before any container code)

This task's commit must land before any file under `tests/conformance/`
exists. Git order is the evidence that the rules were not fitted to
observations.

**Files:**
- Modify: `tests/model/queue.py` (append; `simulate()` unchanged)
- Test: `tests/model/test_queue_trace.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True, slots=True) class Counts: downloading: int; queued: int; seeding: int; seeding_queued: int; left: int`
  - Events, frozen dataclasses: `AddComplete(n: int)`, `AddIncomplete(n: int)`, `RemoveSeeder()`, `FinishDownloads()`. `FinishDownloads` is model-only: every downloading torrent finishes. The container can't do it, and only R3 uses it.
  - `Event = AddComplete | AddIncomplete | RemoveSeeder | FinishDownloads`
  - `class ModelUndefined(Exception)`: raised where the predicate states no belief.
  - `def trace(cfg: QueueConfig, events: Sequence[Event]) -> list[Counts]`: one `Counts` per event, the steady state after it.

Rules. Each goes in the code as a comment citing its source. The spec
requires predicate citations. Where a rule comes from the simulator instead,
the comment says so.

1. Every seeding torrent occupies a `max_active_torrents` slot. Predicate: `src/lintarr/invariants/queue_liveness.py:21-23`, `:497-501`.
2. `seeding_queued` is always 0. This is **a consequence of rule 1, not a stated belief**: seeders absorb *every* slot, so none sit queued. The predicate's verdict is also invariant in `max_active_uploads`, which the `ul` axis of `tests/invariants/test_queue_liveness_sweep.py` pins. The predicate flags this as unvalidated at `:80-85`. Do **not** cite `:483-484`: read literally, it says a seeder without an upload slot frees the download, which contradicts the predicate's own FAIL on WEDGED. Report that inconsistency to Tom in the Task 1 PR body as a finding.
3. With `dont_count_slow_torrents`, slow torrents don't count against `max_active_torrents`, seeders included. Predicate: `queue_liveness.py:1028,1042` (the seeder wedge requires the exemption off). Exempting slow *downloads* from `max_active_downloads` as well comes from the **simulator** (`tests/model/queue.py:76-81`), and the comment says so. Every harness torrent is slow (no peers), so under the exemption nothing is counted. A first start must still be possible: a limit that binds at ≤ 0 still blocks. Predicate: `queue_liveness.py:17-20`.
4. With `share_limit_enabled`, a torrent that becomes complete leaves at once (`left += 1`, freeing its slot), **only for a releasing action** (`_RELEASING_ACTIONS = frozenset({0, 1, 3})`, `queue_liveness.py:438`; `_action_conflict`, `:1188-1218`). `QueueConfig` has no action field, so Task 5's `prefs_for` must set a releasing action whenever `share_limit_enabled`.
5. `queueing_enabled=False`: nothing queues, and every incomplete torrent is downloading. Predicate: the queueing premise, `queue_liveness.py:1374`. Applied before rule 7.
6. Queued torrents start in FIFO order whenever a slot is free. Source: the simulator (`queue.py`, `slots_free`); the predicate is order-agnostic.
7. `AddComplete` raises `ModelUndefined` (the predicate states no belief) when queueing is on, the share limit is off, and either `max_active_torrents` binds at ≤ 0, or the slow exemption is off and the counted torrents already fill it. Scenarios must not reach it.
8. `RemoveSeeder` removes one torrent in the seeding bucket. With no seeders it raises `ModelUndefined`.

- [ ] **Step 1: Write failing tests** in `tests/model/test_queue_trace.py`:
  - `test_incident_wedges_step_by_step`: `WEDGED` from `tests/model/test_queue_model.py` (import it), events `[AddComplete(5), AddIncomplete(3)]` → last `Counts(downloading=0, queued=3, seeding=5, seeding_queued=0, left=0)`.
  - `test_share_limit_drains`: `WEDGED` with `share_limit_enabled=True`, `[AddComplete(5), AddIncomplete(3)]` → last `Counts(3, 0, 0, 0, 5)`.
  - `test_slow_exemption_releases_the_wedge`: `WEDGED` with `dont_count_slow_torrents=True`, `[AddComplete(5), AddIncomplete(3)]` → last `Counts(3, 0, 5, 0, 0)`.
  - `test_slow_exemption_does_not_rescue_zero_downloads`: `WEDGED` with `max_active_downloads=0`, `dont_count_slow_torrents=True`, `[AddIncomplete(2)]` → `Counts(0, 2, 0, 0, 0)`.
  - `test_remove_seeder_frees_exactly_one`: `WEDGED`, `[AddComplete(5), AddIncomplete(3), RemoveSeeder()]` → last `Counts(1, 2, 4, 0, 0)`.
  - `test_zero_downloads_blocks_first_start`: `WEDGED` with `max_active_downloads=0`, `[AddIncomplete(2)]` → `Counts(0, 2, 0, 0, 0)`.
  - `test_overfull_complete_is_undefined`: `WEDGED`, `[AddComplete(6)]` raises `ModelUndefined`.
  - `test_remove_seeder_without_seeders_is_undefined`: `WEDGED`, `[AddIncomplete(1), RemoveSeeder()]` raises `ModelUndefined`.
  - `test_add_complete_equals_finishing_downloads` (**M5**, a Hypothesis property over the sweep box): when `[AddIncomplete(c)]` leaves `queued == 0`, `trace(cfg, [AddComplete(c)])[-1] == trace(cfg, [AddIncomplete(c), FinishDownloads()])[-1]`. This cross-checks the `AddComplete` handler, which every container scenario uses, against the path R3 ties to `simulate()`.
  - `test_trace_agrees_with_simulate_over_the_sweep_box` (**R3**). This uses the full sweep box from `tests/invariants/test_queue_liveness_sweep.py`: dl, ul, tot each in `{0, 1..6, -1}`, slow and share each `{on, off}`, queueing on, n in 1..10. For each case, `events = [AddIncomplete(n)] + [FinishDownloads()] * (n + 1)` and `assert (trace(cfg, events)[-1].queued > 0) == simulate(cfg, n)` (the parentheses matter: without them Python chains the comparison). The docstring states this mapping: "simulate()'s world is n incomplete torrents that finish; FinishDownloads replays it". This supersedes the narrower R3 row in the spec. Update that row in this task.
- [ ] **Step 2:** `uv run pytest tests/model/test_queue_trace.py -q` → FAIL (ImportError on `trace`).
- [ ] **Step 3:** Implement the types and `trace()` in `tests/model/queue.py` per rules 1–7. Keep `trace()` under ~30 lines by splitting per-event handlers (`_apply_add_complete`, etc.) over a small mutable `_State`.
- [ ] **Step 4:** `uv run pytest -q` → all pass, including the existing `test_queue_model.py` and the sweep.
- [ ] **Step 5:** Mutation check: make rule 3 exempt downloads only (seeders still count). `test_slow_exemption_releases_the_wedge` must go red. Restore. Note the result for the PR body.
- [ ] **Step 6:** Commit: `test(model): step-wise trace() encoding the predicate's beliefs (P2)`. This commit also edits the spec so it matches the plan:
  - the R3 row
  - the Pieces table: `trace(cfg, population, events)` becomes `trace(cfg, events)`
  - R5: "expects a harness error" becomes "raises `UnmappedState`, kind `unmapped-state`"
  - R4: "homelab#393 scenario" becomes "scenario 1b, or whichever scenario Task 5 Step 7 selects"

### Task 2: Torrent maker and state mapper (pure, no Docker)

**Files:**
- Create: `tests/conformance/__init__.py`, `tests/conformance/torrents.py`, `tests/conformance/states.py`
- Test: `tests/conformance/test_torrents.py`, `tests/conformance/test_states.py` (no `container` marker; they run in normal CI)

**Interfaces:**
- Produces:
  - `def bencode(value: int | bytes | str | list | dict) -> bytes` (dict keys sorted as bytes)
  - `@dataclass(frozen=True) class HarnessTorrent:` (not `Test…`, which pytest would try to collect) name: str; data: bytes; metainfo: bytes; info_hash: str` (lowercase hex SHA-1 of the bencoded `info`)
  - `def make_torrent(name: str, size: int = 16384, piece_length: int = 16384) -> HarnessTorrent`: single file, deterministic content derived from `name`, no announce URL, `private=1` so no DHT or PEX looks for peers.
  - `class Bucket(StrEnum): DOWNLOADING, QUEUED, SEEDING, SEEDING_QUEUED, LEFT`
  - `class UnmappedState(Exception)` carrying the state string.
  - `def bucket_of(state: str) -> Bucket` per the spec's table (`downloading`, `stalledDL`, `metaDL`, `forcedDL` → DOWNLOADING; `queuedDL` → QUEUED; `uploading`, `stalledUP`, `forcedUP` → SEEDING; `queuedUP` → SEEDING_QUEUED; `pausedUP`, `stoppedUP` → LEFT; anything else raises `UnmappedState`).
  - `def tally(states: Mapping[str, str], expected_hashes: set[str]) -> Counts`: `states` is hash → state from `QbtHarness.states()`. A hash in `expected_hashes` but absent from `states` counts as LEFT (removed by a share action). Imports `Counts` from `tests.model.queue`.

- [ ] **Step 1: Failing tests:**
  - `test_bencode_known_vectors`: `bencode({"a": 1, "b": [b"x", "y"]}) == b"d1:ai1e1:bl1:x1:yee"`; dict keys sorted.
  - `test_make_torrent_hash_is_stable`: two calls with the same name give the same `info_hash`, and different names give different ones.
  - `test_metainfo_has_private_flag_and_no_announce`.
  - `test_every_mapped_state` (parametrised over the spec table).
  - `test_unknown_state_raises` (**R5**): `missingFiles`, `checkingUP`, `error`, `""` each raise `UnmappedState`.
  - `test_vanished_torrent_counts_as_left`.
- [ ] **Step 2:** Run → FAIL (ImportError).
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `uv run pytest tests/conformance -q` → PASS.
- [ ] **Step 5:** Commit: `test(conformance): torrent maker and qBittorrent state mapper`.

### Task 3: Harness client, container fixture, marker, image pin

**Files:**
- Create: `tests/conformance/image.py`, `tests/conformance/qbt.py`, `tests/conformance/conftest.py`, `tests/conformance/test_harness_smoke.py`, `tests/conformance/test_image_pin.py`
- Modify: `pyproject.toml` (`[tool.pytest.ini_options]`: add `markers = ["container: drives a real qBittorrent container; needs Docker"]` and `addopts = "-m 'not container'"`)

**Interfaces:**
- Produces:
  - `QBT_IMAGE = "lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.14-ls476"` in `image.py`, with a comment: "Source of truth is homelab's running container; bump together."
  - `class HarnessError(Exception)` in `qbt.py`.
  - `class QbtHarness` in `qbt.py`. It is constructed by the fixture, and the methods are thin wrappers over the Web API v2:
    - `login() -> None`
    - `set_prefs(prefs: dict[str, object]) -> None` (`POST /api/v2/app/setPreferences`, form field `json`)
    - `add(t: HarnessTorrent, *, complete: bool) -> None` (`POST /api/v2/torrents/add`, multipart `torrents`, `savepath=/downloads`, `skip_checking` = `complete`, `stopped=false`; 5.x renamed `paused`). `complete=True` first writes `t.data` to the host directory mounted at `/downloads`.
    - `states() -> dict[str, str]` (hash → state, from `GET /api/v2/torrents/info`)
    - `delete(hashes: list[str], *, delete_files: bool) -> None`
    - `reset() -> None`: delete every torrent, then poll until `states() == {}` or raise `HarnessError` (Review Focus 3)
  - Fixture `qbt` (session scope) in `conftest.py`. It picks a free port P and runs `docker run -d --rm -p 127.0.0.1:P:P -e WEBUI_PORT=P -e PUID=<uid> -e PGID=<gid> -v <tmp>:/downloads QBT_IMAGE`. **The same port on both sides is mandatory.** A remapped publish fails host-header validation, so every request answers 401, and failed logins feed the 5-failure ban. See `docs/measurements/2026-10-02-qbittorrent-5.2.4-webui-ban.md` (row A6, and the "Publish on the port" note) and `tools/probe_qbt_ban.py:17-21`, and cite both in the fixture. The fixture then:
    - waits for readiness with **unauthenticated** `GET /api/v2/app/version` (any HTTP answer, 403 included, means it's up), never by polling login. The bound is 90 s, else `HarnessError` (Review Focus 1).
    - reads the temporary password from `docker logs` (the line contains "temporary password"; parse it the way `tools/probe_qbt_ban.py:125` does).
    - logs in **once**, accepting 200 `Ok.` or 204. Any other answer raises `HarnessError` with no retry. and yields a `QbtHarness`. It always runs `docker rm -f` in teardown. If `docker` isn't on PATH, the fixture raises `HarnessError("docker not available")`. That is a fail, never a skip.

The harness's POSTs live only under `tests/conformance/`. `tests/test_readonly_guarantee.py` scans `src/`. Confirm that by reading it, and leave it unchanged (R7).

- [ ] **Step 1: Failing tests:**
  - `test_image_pin.py::test_pin_matches_homelab` (**R2**): `QBT_IMAGE.endswith(":5.2.3_v2.0.14-ls476")`.
  - `test_harness_smoke.py`, all `@pytest.mark.container`:
    - `test_container_reports_expected_version`: `GET /api/v2/app/version` starts with `v5.2.3`.
    - `test_complete_torrent_reaches_seeding`: add one complete torrent; within 30 s `bucket_of(state) == SEEDING` (Review Focus 2).
    - `test_incomplete_torrent_downloads`: add one incomplete torrent → `DOWNLOADING` (expected `stalledDL`).
    - `test_reset_empties_client`.
- [ ] **Step 2:** `uv run pytest -q` → `test_pin_matches_homelab` fails with ImportError, and the smoke tests are deselected. Then `uv run pytest -m container -q` → fails.
- [ ] **Step 3:** Implement `image.py`, `qbt.py`, `conftest.py`, and the `pyproject.toml` marker config.
- [ ] **Step 4:** `uv run pytest -m container -q` (with Docker running) → 4 passed. `uv run pytest -q` → passes, with the smoke tests reported as deselected (**R6**: check `-rA` output shows `deselected`, not `skipped`).
- [ ] **Step 5:** Commit: `test(conformance): qBittorrent harness, container fixture, image pin`.

### Task 4: Measure the assumptions before writing scenarios (R10)

Exploratory. The deliverable is a measurement document, not code.

**Files:**
- Create: `docs/measurements/2026-11-<dd>-qbt-conformance-assumptions.md`
- Create: `tests/conformance/test_assumptions.py` (`container` marker; pins what was measured)

- [ ] **Step 1:** Using the fixture, measure and record (exact commands, raw `torrents/info` excerpts, UTC timestamps):
  1. `max_ratio_enabled=true, max_ratio=0, max_ratio_act=0`: does a just-added complete torrent with zero upload move to `stoppedUP` (or `pausedUP`), and how fast?
  2. The same with `max_ratio_act=3`: does the torrent vanish from `torrents/info`?
  3. The minimum accepted `slow_torrent_inactive_timer`, and, under `dont_count_slow_torrents` with `max_active_torrents` full, the time from adding a queued torrent until it **actually starts**. That end-to-end delay includes libtorrent's auto-manage pass (about 30 s by default), so it, not the timer alone, sets `settle_s`.
  4. What `torrents/info` reports for 5 complete + 3 incomplete under the `_WEDGED` prefs (`tests/fixtures/homelab.py`). This is the first real look at the headline scenario. Record the per-state counts.
- [ ] **Step 2:** Apply the **stop rule**. If item 1 fails, record which replacement works (`max_seeding_time=0`, or none). If item 4 differs from `trace()`'s prediction, that is expected to become a known disagreement in Task 5. Report it to Tom in the PR body, with the raw states. Don't change `trace()`.
- [ ] **Step 3:** Write `test_assumptions.py` pinning items 1–3 as measured, so a qBittorrent change trips them.
- [ ] **Step 4:** `uv run pytest -m container tests/conformance/test_assumptions.py -q` → PASS.
- [ ] **Step 5:** Commit: `docs(measurements): qBittorrent 5.2.3 conformance assumptions`.

### Task 5: Scenarios, the differential, known disagreements, result file

**Files:**
- Create: `tests/conformance/scenarios.py`, `tests/conformance/known.py`, `tests/conformance/runner.py`, `tests/conformance/test_differential.py`, `tests/conformance/test_known_registry.py`

**Interfaces:**
- Consumes: `trace`, `Counts` and the events (Task 1); `make_torrent`, `tally`, `UnmappedState` (Task 2); `QbtHarness`, `HarnessError` (Task 3).
- Produces:
  - `@dataclass(frozen=True) class Scenario: id: str; cfg: QueueConfig; prefs: dict[str, object]; events: tuple[Event, ...]`. `prefs` are the full qBittorrent preferences to set; `cfg` is the model's view of them. A helper `prefs_for(cfg, *, slow_timer_s: int, **overrides)` builds `prefs` from `cfg`, so the two can't drift silently. It **always sets the same full key set**, so no scenario inherits the previous one's settings (`reset()` only deletes torrents):
    - `queueing_enabled`, the three `max_active_*` limits, `dont_count_slow_torrents`
    - `max_ratio_enabled`, `max_ratio`, `max_ratio_act` (a releasing action when `share_limit_enabled`, per rule 4)
    - `max_seeding_time_enabled`, `max_inactive_seeding_time_enabled`, `slow_torrent_inactive_timer`

  The key set is a module constant, `PREF_KEYS`.
  - `SCENARIOS: tuple[Scenario, ...]`: the spec's scenarios 1, 1b, 2, 3–8, each expanded over `dont_count_slow_torrents ∈ {False, True}`. Scenario 6 uses whatever Task 4 proved fires. If nothing does, drop it and add a line to CONTINUATION.md "Open items".
  - `def observe(h: QbtHarness, s: Scenario, *, poll_s: float, settle_s: float, timeout_s: float) -> list[Counts]`: one stable `Counts` per event.
    - Stability is judged on the **raw** hash → state maps: equal on two consecutive polls, both taken at least `settle_s` after the event. Only then is the map passed through `bucket_of` and `tally`, so transient states like `checkingResumeData` never raise (**M2**). An unmapped state still present at stability raises `UnmappedState`. A timeout raises `HarnessError` (Review Focus 5).
    - `settle_s` comes from Task 4's measured *time for a slow torrent to free a slot* (which includes libtorrent's auto-manage pass), not just the slow timer (**M3**).
    - `RemoveSeeder` deletes one hash currently in SEEDING (`delete_files=True`) and **removes it from the expected set**, so it isn't counted as LEFT (**H4**).
  - `@dataclass(frozen=True) class Known: observed: tuple[Counts, ...]; why: str; tracked_in: str` and `KNOWN_DISAGREEMENTS: dict[str, Known]` (empty at first unless Task 4 found one).
  - `def judge(scenario_id: str, observed: list[Counts], predicted: list[Counts], known: Mapping[str, Known]) -> None`: raises `AssertionError` (a disagreement, with the full detail per the spec) or `StaleKnownEntry(AssertionError)`.
  - The result file, in `runner.py`, with hooks in `tests/conformance/conftest.py`:
    - `def classify_report(report: pytest.TestReport) -> str | None`. Classify **setup, call and teardown** reports via `pytest_runtest_makereport`, so a session-fixture `HarnessError` (a setup error) is caught. `StaleKnownEntry` maps to `disagreement`.
    - `def result_kind(kinds: Iterable[str], exitstatus: int) -> str`. Precedence: `harness-error` > `unmapped-state` > `disagreement` > `pass`. A non-zero `exitstatus` with nothing classified (e.g. a collection error) gives `harness-error` (**H5**).
    - `pytest_sessionfinish` writes `conformance-result.json` (`{"kind": ..., "failed": [ids]}`) **only when at least one `container` item was selected** (**M1**). Add `conformance-result.json` to `.gitignore` in this task.
  - Retry: each differential test runs `observe` up to twice (after a `reset()`), and fails only if both attempts produce a failure. No plugin.

- [ ] **Step 1: Failing unit tests** (no marker) in `test_known_registry.py` (**R11**), against `judge` with synthetic `Counts`:
  - `test_known_entry_matching_observation_passes` (observed == recorded ≠ predicted → no raise)
  - `test_known_entry_with_changed_observation_fails`
  - `test_known_entry_now_matching_model_is_stale`
  - `test_every_registry_entry_has_why_and_tracked_in` (iterates the real `KNOWN_DISAGREEMENTS`)
  - `test_unknown_disagreement_fails_with_full_detail` (the message contains the scenario id, step index, both triples)
  - `test_result_kind_precedence`
  - `test_scenario_prefs_agree_with_cfg`: for each `SCENARIOS` entry, `set(prefs) == PREF_KEYS`, every directly mapped key equals its `cfg` field, `share_limit_enabled` ⇔ (`max_ratio_enabled` or `max_seeding_time_enabled`), and when it's on, `max_ratio_act ∈ {0, 1, 3}`.
  - `test_remove_seeder_drops_hash_from_expected` (**H4**): after a `RemoveSeeder`, the deleted hash isn't counted as LEFT.
  - `test_transient_states_do_not_raise_before_stability` (**M2**): a fake harness whose raw states pass through `checkingResumeData` → `stalledUP` settles as SEEDING. One that stays `missingFiles` raises `UnmappedState` at stability.
  - `test_failure_kind_mapping` (**H5**): `classify_report` maps a setup-phase `HarnessError` → `harness-error`, `UnmappedState` → `unmapped-state`, `AssertionError` and `StaleKnownEntry` → `disagreement`. `result_kind` with zero classified reports and `exitstatus != 0` → `harness-error`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `scenarios.py`, `known.py`, `runner.py` (`observe`, `judge`, the result writer).
- [ ] **Step 4:** Write `test_differential.py`: one `@pytest.mark.container` test parametrised over `SCENARIOS` by `id`. Per test: `reset()`, `set_prefs`, `observe` → `judge(id, observed, trace(s.cfg, s.events), KNOWN_DISAGREEMENTS)`. **Before `judge`, assert the scenario reached its target state** (spec anti-goal):
    - slow-on variants: at least `settle_s` elapsed after the last event
    - scenario 6: at least one torrent is LEFT
    - scenario 1/1b: the incomplete torrents were added after the complete ones were seeding

  A failed reach assertion raises `HarnessError` (the scenario didn't run as designed), not a disagreement.
- [ ] **Step 5:** `uv run pytest -m container --collect-only -q | grep -c test_differential` → ≥ 12 (**R1**). Then `uv run pytest -m container -q` locally.
- [ ] **Step 6:** For each disagreement: apply the stop rule. Report to Tom with the raw states. Only after Tom agrees it's understood, add a `Known` entry plus a CONTINUATION.md "Open items" line (its `tracked_in`). Rerun → green.
- [ ] **Step 7: Mutation round (R4).** Choose a (rule, scenario) pair where the scenario is **not** in `KNOWN_DISAGREEMENTS`, and the mutated rule changes that scenario's predicted trace. Assert both conditions in a throwaway script before mutating. A Known scenario masks any mutation, because `judge` only checks observed == recorded. The first candidate is rule 1 ("seeders don't count") against scenario 1b, if 1b agrees with the model. Mutate, watch it go red against the container, then restore. If no unregistered scenario is sensitive to any rule, record that **R4 cannot be met yet** and tell Tom. That also means spec Goal 4 (a live conformance test of rule 1) is unmet. Record either outcome in the PR body.
- [ ] **Step 8:** Commit: `test(conformance): scenario differential with known-disagreement registry`.

### Task 6: Nightly workflow and ntfy alert

**Files:**
- Create: `.github/workflows/conformance.yml`, `tools/conformance_alert.sh`
- Test: `tests/test_conformance_workflow.py` (no marker; follow `tests/test_ci_workflow.py`'s YAML-reading pattern), `tests/test_conformance_alert.py`

**Interfaces:**
- `conformance.yml`:
  - `on: schedule: - cron: "41 6 * * *"`, plus `workflow_dispatch` with a boolean input `force_failure` (default false).
  - `permissions: contents: read`, one job. Derive `timeout-minutes` from Task 4's measured `settle_s`: scenarios × events × (settle + 2 polls) × 2 (retry), plus 3 min for container start, rounded up. Write the arithmetic in a comment. If it exceeds 45, shorten scenarios or split the slow-on and slow-off variants into two matrix jobs.
  - Steps: checkout, setup-uv and setup-python (same SHAs as `tests.yml`), `uv sync --frozen --all-groups`, then `uv run --frozen pytest -m container -q` with `CONFORMANCE_FORCE_FAILURE=${{ inputs.force_failure }}`.
  - Final step `if: failure()`: `tools/conformance_alert.sh`, with env `NTFY_TOKEN: ${{ secrets.NTFY_TOKEN }}` and `RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}`.
- `CONFORMANCE_FORCE_FAILURE=true` makes the differential's first scenario compare against a deliberately wrong prediction. This is the R8 drill. Implement it in `test_differential.py`.
- `tools/conformance_alert.sh` (bash, `set -euo pipefail`):
  - It reads `kind` from `conformance-result.json` with `python3 -c` (not `jq`, which isn't guaranteed on Tom's Mac). If the file is missing or unparseable, the kind is `harness-error` and the message says so.
  - An empty `NTFY_TOKEN` makes it exit 1 with `NTFY_TOKEN secret is not set; no alert was sent` (**R9**).
  - Otherwise it runs `curl --fail-with-body -sS -H "Authorization: Bearer $NTFY_TOKEN" -H "Title: lintarr conformance: $kind" -H "Priority: high" -H "Click: $RUN_URL" -d "<kind>: <failed ids, comma-joined, ≤ 300 chars> — $RUN_URL" https://notifications.tomclancy.info/homelab-alerts`. A non-2xx exits non-zero (Review Focus 4).
  - It never prints the token.

- [ ] **Step 1: Failing tests:**
  - `test_conformance_workflow.py`:
    - the cron is set and `workflow_dispatch` has `force_failure`
    - the pytest step runs `-m container`
    - the alert step is `if: failure()` and passes `secrets.NTFY_TOKEN`
    - every `uses:` is pinned to a 40-hex SHA
    - the action SHAs equal `tests.yml`'s
  - `test_conformance_alert.py` (runs the script via `subprocess`, with a fake `curl` on `PATH` that records its argv and exits with a chosen code):
    - empty token → exit 1 and the exact message
    - missing JSON → the posted body starts with `harness-error`
    - a disagreement JSON → the title contains `disagreement` and the URL ends `/homelab-alerts`
    - fake curl exits 22 → the script exits non-zero
    - the token never appears in stdout or stderr
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement the workflow and the script.
- [ ] **Step 4:** `uv run pytest -q` → PASS. Run pre-commit (`check yaml` covers the workflow).
- [ ] **Step 5:** Commit: `ci(conformance): nightly qBittorrent differential with ntfy alert`.

### After the branch merges (Tom + verification)

- [ ] **R9 (before Tom sets the secret):** `gh workflow run conformance.yml -f force_failure=true`. The alert step runs (because the job failed) and fails with `NTFY_TOKEN secret is not set; no alert was sent`. A clean run can't show this, since the alert step only runs on failure.
- [ ] Then Tom: `gh secret set NTFY_TOKEN --repo tclancy/lintarr`.
- [ ] **R8:** `gh workflow run conformance.yml -f force_failure=true` → the run is red and a push arrives on `homelab-alerts` (Tom confirms on the phone). Then `gh workflow run conformance.yml` → green.
- [ ] **R7:** `git diff origin/main~N -- src/` is empty for the merged range.
- [ ] Update `CONTINUATION.md`: note that GitHub **auto-disables scheduled workflows on a public repo after 60 days without repo activity**. A silent nightly is the failure this slice exists to prevent, so the line belongs under Open items. Then record: P2 slice 1 done, the known disagreements listed, and next steps (the version-drift warning; the quarantine machinery with P1).
