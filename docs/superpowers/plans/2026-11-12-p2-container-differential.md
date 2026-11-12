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

Rules. Each goes in the code as a comment citing its source line:

1. Every seeding torrent occupies a `max_active_torrents` slot. `src/lintarr/invariants/queue_liveness.py:21-23`, `:497-501`.
2. `max_active_uploads` is never consulted, so `seeding_queued` is always 0. `queue_liveness.py:483-484`, `:500-501`.
3. With `dont_count_slow_torrents`, a slow torrent counts against no limit, whether it's downloading or seeding. Every harness torrent is slow (no peers), so in practice nothing counts. Source: the predicate makes `qbt.slow_exempt_off` a premise of the seeder-absorption wedge (`queue_liveness.py:1028,1042`), so it believes slow *seeders* can't absorb slots. `tests/model/queue.py:76-81` says the queue then drains. A first start must still be possible (`queue.py:70-74`): `max_active_downloads=0` or `max_active_torrents=0` still blocks.
4. With `share_limit_enabled`, a torrent that becomes complete leaves at once (`left += 1`, freeing its slot). `queue.py` (the `share_limit_enabled` branch).
5. `queueing_enabled=False`: nothing queues, and every incomplete torrent is downloading.
6. Queued torrents start in FIFO order whenever a slot is free (`queue.py` `slots_free`).
7. `AddComplete` when `max_active_torrents` binds and its *counted* torrents already fill it (with no share limit or slow exemption): the predicate states no belief, so raise `ModelUndefined`. Scenarios must not reach it.

- [ ] **Step 1: Write failing tests** in `tests/model/test_queue_trace.py`:
  - `test_incident_wedges_step_by_step`: `WEDGED` from `tests/model/test_queue_model.py` (import it), events `[AddComplete(5), AddIncomplete(3)]` → last `Counts(downloading=0, queued=3, seeding=5, seeding_queued=0, left=0)`.
  - `test_share_limit_drains`: `WEDGED` with `share_limit_enabled=True`, `[AddComplete(5), AddIncomplete(3)]` → last `Counts(3, 0, 0, 0, 5)`.
  - `test_slow_exemption_releases_the_wedge`: `WEDGED` with `dont_count_slow_torrents=True`, `[AddComplete(5), AddIncomplete(3)]` → last `Counts(3, 0, 5, 0, 0)`.
  - `test_slow_exemption_does_not_rescue_zero_downloads`: `max_active_downloads=0`, `dont_count_slow_torrents=True`, `[AddIncomplete(2)]` → `Counts(0, 2, 0, 0, 0)`.
  - `test_remove_seeder_frees_exactly_one`: `WEDGED`, `[AddComplete(5), AddIncomplete(3), RemoveSeeder()]` → last `Counts(1, 2, 4, 0, 0)`.
  - `test_zero_downloads_blocks_first_start`: `max_active_downloads=0`, `[AddIncomplete(2)]` → `Counts(0, 2, 0, 0, 0)`.
  - `test_overfull_complete_is_undefined`: `WEDGED`, `[AddComplete(6)]` raises `ModelUndefined`.
  - `test_trace_agrees_with_simulate_over_the_sweep_box` (**R3**). This uses the full sweep box from `tests/invariants/test_queue_liveness_sweep.py`: dl, ul, tot each in `{0, 1..6, -1}`, slow and share each `{on, off}`, queueing on, n in 1..10. For each case, `events = [AddIncomplete(n)] + [FinishDownloads()] * (n + 1)` and `trace(cfg, events)[-1].queued > 0 == simulate(cfg, n)`. The docstring states this mapping: "simulate()'s world is n incomplete torrents that finish; FinishDownloads replays it". This supersedes the narrower R3 row in the spec. Update that row in this task.
- [ ] **Step 2:** `uv run pytest tests/model/test_queue_trace.py -q` → FAIL (ImportError on `trace`).
- [ ] **Step 3:** Implement the types and `trace()` in `tests/model/queue.py` per rules 1–7. Keep `trace()` under ~30 lines by splitting per-event handlers (`_apply_add_complete`, etc.) over a small mutable `_State`.
- [ ] **Step 4:** `uv run pytest -q` → all pass, including the existing `test_queue_model.py` and the sweep.
- [ ] **Step 5:** Mutation check: make rule 3 exempt downloads only (seeders still count). `test_slow_exemption_releases_the_wedge` must go red. Restore. Note the result for the PR body.
- [ ] **Step 6:** Commit: `test(model): step-wise trace() encoding the predicate's beliefs (P2)`. This commit includes the spec R3 row edit.

### Task 2: Torrent maker and state mapper (pure, no Docker)

**Files:**
- Create: `tests/conformance/__init__.py`, `tests/conformance/torrents.py`, `tests/conformance/states.py`
- Test: `tests/conformance/test_torrents.py`, `tests/conformance/test_states.py` (no `container` marker; they run in normal CI)

**Interfaces:**
- Produces:
  - `def bencode(value: int | bytes | str | list | dict) -> bytes` (dict keys sorted as bytes)
  - `@dataclass(frozen=True) class TestTorrent: name: str; data: bytes; metainfo: bytes; info_hash: str` (lowercase hex SHA-1 of the bencoded `info`)
  - `def make_torrent(name: str, size: int = 16384, piece_length: int = 16384) -> TestTorrent`: single file, deterministic content derived from `name`, no announce URL, `private=1` so no DHT or PEX looks for peers.
  - `class Bucket(StrEnum): DOWNLOADING, QUEUED, SEEDING, SEEDING_QUEUED, LEFT`
  - `class UnmappedState(Exception)` carrying the state string.
  - `def bucket_of(state: str) -> Bucket` per the spec's table (`downloading`, `stalledDL`, `metaDL`, `forcedDL` → DOWNLOADING; `queuedDL` → QUEUED; `uploading`, `stalledUP`, `forcedUP` → SEEDING; `queuedUP` → SEEDING_QUEUED; `pausedUP`, `stoppedUP` → LEFT; anything else raises `UnmappedState`).
  - `def tally(states: Iterable[str], expected_hashes: set[str], seen_hashes: set[str]) -> Counts`: a hash in `expected_hashes` but not in `seen_hashes` counts as LEFT (removed by a share action). Imports `Counts` from `tests.model.queue`.

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
    - `add(t: TestTorrent, *, complete: bool) -> None` (`POST /api/v2/torrents/add`, multipart `torrents`, `savepath=/downloads`, `skip_checking` = `complete`, and not stopped). `complete=True` first writes `t.data` to the host directory mounted at `/downloads`.
    - `states() -> dict[str, str]` (hash → state, from `GET /api/v2/torrents/info`)
    - `delete(hashes: list[str], *, delete_files: bool) -> None`
    - `reset() -> None`: delete every torrent, then poll until `states() == {}` or raise `HarnessError` (Review Focus 3)
  - Fixture `qbt` (session scope) in `conftest.py`. It runs `docker run -d --rm -p 127.0.0.1:<free port>:8080 -e PUID=<uid> -e PGID=<gid> -e WEBUI_PORT=8080 -v <tmp>:/downloads QBT_IMAGE`, waits for the WebUI (bounded by 90 s, else `HarnessError`; Review Focus 1), reads the temporary password from `docker logs` (the line contains "temporary password"), logs in, and yields a `QbtHarness`. It always runs `docker rm -f` in teardown. If `docker` isn't on PATH, the fixture raises `HarnessError("docker not available")`. That is a fail, never a skip.

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
  3. The minimum accepted `slow_torrent_inactive_timer`, and the time until an idle torrent counts as slow.
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
  - `@dataclass(frozen=True) class Scenario: id: str; cfg: QueueConfig; prefs: dict[str, object]; events: tuple[Event, ...]`. `prefs` are the full qBittorrent preferences to set; `cfg` is the model's view of them. A helper `prefs_for(cfg, **overrides)` builds `prefs` from `cfg`, so the two can't drift silently.
  - `SCENARIOS: tuple[Scenario, ...]`: the spec's scenarios 1, 1b, 2, 3–8, each expanded over `dont_count_slow_torrents ∈ {False, True}`. Scenario 6 uses whatever Task 4 proved fires. If nothing does, drop it and add a line to CONTINUATION.md "Open items".
  - `def observe(h: QbtHarness, s: Scenario, *, poll_s: float, settle_s: float, timeout_s: float) -> list[Counts]`: one stable `Counts` per event (stable = equal on two consecutive polls, both taken at least `settle_s` after the event; `settle_s` ≥ the measured slow timer; on timeout raise `HarnessError`; Review Focus 5).
  - `@dataclass(frozen=True) class Known: observed: tuple[Counts, ...]; why: str; tracked_in: str` and `KNOWN_DISAGREEMENTS: dict[str, Known]` (empty at first unless Task 4 found one).
  - `def judge(scenario_id: str, observed: list[Counts], predicted: list[Counts], known: Mapping[str, Known]) -> None`: raises `AssertionError` (a disagreement, with the full detail per the spec) or `StaleKnownEntry(AssertionError)`.
  - The result file. A session-finish hook in `tests/conformance/conftest.py` writes `conformance-result.json` (`{"kind": "pass" | "disagreement" | "unmapped-state" | "harness-error", "failed": [ids]}`) to the working directory. Precedence when there are several: `harness-error` > `unmapped-state` > `disagreement`.
  - Retry: each differential test runs `observe` up to twice (after a `reset()`), and fails only if both attempts produce a failure. No plugin.

- [ ] **Step 1: Failing unit tests** (no marker) in `test_known_registry.py` (**R11**), against `judge` with synthetic `Counts`:
  - `test_known_entry_matching_observation_passes` (observed == recorded ≠ predicted → no raise)
  - `test_known_entry_with_changed_observation_fails`
  - `test_known_entry_now_matching_model_is_stale`
  - `test_every_registry_entry_has_why_and_tracked_in` (iterates the real `KNOWN_DISAGREEMENTS`)
  - `test_unknown_disagreement_fails_with_full_detail` (the message contains the scenario id, step index, both triples)
  - `test_result_kind_precedence`
  - `test_scenario_prefs_agree_with_cfg` (for each `SCENARIOS` entry, the qBittorrent keys in `prefs` equal the `cfg` fields)
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `scenarios.py`, `known.py`, `runner.py` (`observe`, `judge`, the result writer).
- [ ] **Step 4:** Write `test_differential.py`: one `@pytest.mark.container` test parametrised over `SCENARIOS` by `id`. Per test: `reset()`, `set_prefs`, `observe` → `judge(id, observed, trace(s.cfg, s.events), KNOWN_DISAGREEMENTS)`.
- [ ] **Step 5:** `uv run pytest -m container --collect-only -q | grep -c test_differential` → ≥ 12 (**R1**). Then `uv run pytest -m container -q` locally.
- [ ] **Step 6:** For each disagreement: apply the stop rule. Report to Tom with the raw states. Only after Tom agrees it's understood, add a `Known` entry plus a CONTINUATION.md "Open items" line (its `tracked_in`). Rerun → green.
- [ ] **Step 7: Mutation round (R4).** Temporarily make `trace()` rule 1 false (seeders don't count). Scenario 1b must go red against the container. Restore. Record it in the PR body.
- [ ] **Step 8:** Commit: `test(conformance): scenario differential with known-disagreement registry`.

### Task 6: Nightly workflow and ntfy alert

**Files:**
- Create: `.github/workflows/conformance.yml`, `tools/conformance_alert.sh`
- Test: `tests/test_conformance_workflow.py` (no marker; follow `tests/test_ci_workflow.py`'s YAML-reading pattern), `tests/test_conformance_alert.py`

**Interfaces:**
- `conformance.yml`:
  - `on: schedule: - cron: "41 6 * * *"`, plus `workflow_dispatch` with a boolean input `force_failure` (default false).
  - `permissions: contents: read`, one job, `timeout-minutes: 30`.
  - Steps: checkout, setup-uv and setup-python (same SHAs as `tests.yml`), `uv sync --frozen --all-groups`, then `uv run --frozen pytest -m container -q` with `CONFORMANCE_FORCE_FAILURE=${{ inputs.force_failure }}`.
  - Final step `if: failure()`: `tools/conformance_alert.sh`, with env `NTFY_TOKEN: ${{ secrets.NTFY_TOKEN }}` and `RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}`.
- `CONFORMANCE_FORCE_FAILURE=true` makes the differential's first scenario compare against a deliberately wrong prediction. This is the R8 drill. Implement it in `test_differential.py`.
- `tools/conformance_alert.sh` (bash, `set -euo pipefail`):
  - It reads `kind` from `conformance-result.json`. If the file is missing or unparseable, the kind is `harness-error` and the message says so.
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

- [ ] Tom: `gh secret set NTFY_TOKEN --repo tclancy/lintarr`.
- [ ] **R9:** if a dispatch runs before the secret exists, the alert step fails with the exact message. (Optional: run it once before Tom sets the secret.)
- [ ] **R8:** `gh workflow run conformance.yml -f force_failure=true` → the run is red and a push arrives on `homelab-alerts` (Tom confirms on the phone). Then `gh workflow run conformance.yml` → green.
- [ ] **R7:** `git diff origin/main~N -- src/` is empty for the merged range.
- [ ] Update `CONTINUATION.md`: P2 slice 1 done, the known disagreements listed, and next steps (the version-drift warning; the quarantine machinery with P1).
