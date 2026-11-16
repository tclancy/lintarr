# Uploading-Seeder Measurement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether actively uploading seeders hold qBittorrent 5.2.3's
`max_active_torrents` slots against a new download, record the result, and pin
it in the nightly.

**Architecture:** A second container from the same pinned image acts as the
leecher. It sits on a private docker network with the seeder container and is
connected with `addPeers`. Each torrent's download is capped so the seeder keeps
uploading. The decision logic (bring-up readiness, window stability, outcome)
consists of pure functions over recorded polls, unit-tested without Docker. A
probe tool runs each scenario three times to produce the measurement. A
`container` test then pins the recorded outcome.

**Tech Stack:** Python 3.13, uv, pytest, httpx, Docker, `lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.14-ls476`.

**Spec:** `docs/superpowers/specs/2026-11-15-uploading-seeders-measurement-design.md`

## Global Constraints

- `src/`, `tests/model/queue.py` (`simulate()` and `trace()`), the `_WEDGED`
  fixture and its exit-1 pin are not changed.
- The existing session `qbt` fixture and slice 1's scenarios keep their
  peerless world. The pair is a separate fixture.
- Image: `QBT_IMAGE` from `tests/conformance/image.py`. No new image or
  dependency.
- WebUI port rule: publish the same port on both sides and set `WEBUI_PORT` to
  it. Log in exactly once per container (5-failure IP ban,
  `tests/conformance/conftest.py:1-10`).
- Seeding torrents are 32 MiB with 256 KiB pieces. The leecher caps each one at
  65536 bytes/s. The slow threshold is the image default, 2 KiB/s = 2048 bytes/s
  `upspeed`.
- `slow_torrent_inactive_timer=1`. Bring-up allows at most 60 s. The window is
  120 s at a 1 s poll, and the verdict is read over its final 30 s, which must be
  stable.
- Scenarios, as `(dl, ul, tot, exemption)`:
  - **U0-off:** -1/-1/3, off. 3 seeders with no leecher, then 1 incomplete torrent.
  - **U1-off:** -1/-1/3, off. 3 seeders uploading, then 1 incomplete torrent.
  - **U1-on:** -1/-1/3, on. The same as U1-off.
  - **W-off:** 3/3/5, off. 5 seeders with a leecher, then 3 incomplete torrents.
- Three measurement runs per scenario must agree before a result is pinned. If
  they disagree, the scenario is recorded as unresolved and not pinned.
- Failure kinds feed the existing result file. `HarnessError` means a broken
  bring-up, and `AssertionError` means a changed outcome.
- Nightly: the pytest step's 35-minute timeout stays. The total container run
  must stay under 30 minutes, measured.
- Commits use the bot identity: `eval "$(dispatch mint-token)" && dispatch bot-identity --quiet && git commit ...`.
  Never use `--no-verify`.

## Review Focus

1. **Private torrents never connect over `addPeers`.** The bring-up must fail
   as a named `HarnessError` ("no seeder reached uploading"), not time out
   silently. Task 3's smoke test is the gate. The fallback is `private=False`.
2. **The leecher finishes a torrent mid-window.** Its seeder drops to
   `stalledUP`, which would be read as a verdict. Pinned by
   `test_window_seeder_not_uploading_is_harness_error`.
3. **The final 30 s are not stable.** The outcome must be a failure carrying
   the raw polls, never "the last poll wins". Pinned by
   `test_unstable_window_is_a_disagreement`.
4. **Leaked networks.** A crashed session must not leave docker networks
   behind. Pinned by `test_sweep_removes_expired_networks`.
5. **W-off's bring-up.** The upload limit queues 2 of 5 seeders, so "every
   seeder uploading" can never hold there. Readiness must accept `queuedUP`
   seeders and require that every active one is uploading. Pinned by
   `test_ready_accepts_queued_seeders`.

---

### Task 1: Harness API additions

**Files:**
- Modify: `tests/conformance/qbt.py`, `tests/conformance/torrents.py`
- Test: `tests/conformance/test_qbt_client.py` (new, not `container`)

**Interfaces:**
- Produces:
  - `QbtHarness.add_peers(hashes: list[str], peers: list[str]) -> None`:
    POST `/api/v2/torrents/addPeers` with `hashes` `|`-joined and `peers`
    `|`-joined (`ip:port`).
  - `QbtHarness.set_download_limit(hashes: list[str], limit: int) -> None`:
    POST `/api/v2/torrents/setDownloadLimit`, `limit` in bytes/s.
  - `QbtHarness.rows() -> list[Row]`: from `/api/v2/torrents/info`.
  - `Row` is a frozen dataclass with `hash: str`, `name: str`, `state: str`,
    `upspeed: int` and `num_leechs: int`.
  - `QbtHarness.states()` is reimplemented as `{r.hash: r.state for r in self.rows()}`.
  - `make_torrent(name, size=16384, piece_length=16384, *, private: bool = True)`.
    It omits the `private` key when `private` is False.
  - `QbtHarness.__init__` gains an optional keyword
    `transport: httpx.BaseTransport | None = None`, so tests can inject
    `httpx.MockTransport`.

- [ ] **Step 1: Write failing tests** with `httpx.MockTransport` recording each request:
  - `test_add_peers_posts_pipe_joined_hashes_and_peers`: the form has
    `hashes == "a|b"` and `peers == "172.18.0.2:6881"`.
  - `test_set_download_limit_posts_bytes_per_second`: the form has
    `limit == "65536"`.
  - `test_rows_reads_upspeed_and_leechers`: the JSON row
    `{"hash":"h","name":"n","state":"uploading","upspeed":70000,"num_leechs":1}`
    becomes `Row("h","n","uploading",70000,1)`.
  - `test_non_200_raises_harness_error`: a 400 from `addPeers` raises
    `HarnessError`.
  - `test_make_torrent_public_has_no_private_key`:
    `b"7:privatei1e" not in make_torrent("x", private=False).metainfo`.
    The default still contains it.
- [ ] **Step 2: Run** `uv run pytest tests/conformance/test_qbt_client.py -q`. Expected: FAIL (missing attributes).
- [ ] **Step 3: Implement** the additions above.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS, with the existing suite unchanged.
- [ ] **Step 5: Commit** `feat(conformance): addPeers, download limit and rich rows in the harness client`.

### Task 2: Lift container lifecycle; add the network and pair

**Files:**
- Create: `tests/conformance/containers.py`
- Modify: `tests/conformance/conftest.py`
- Test: `tests/conformance/test_containers.py` (new, not `container`)

**Interfaces:**
- Consumes: Task 1's `QbtHarness`.
- Produces:
  - **Moved from `conftest.py` to `containers.py`, unchanged:** `_run`,
    `_docker`, `_expired`, `_remove`, `_wait_ready`, `_temp_password`,
    `_free_port`, `EXPIRY_LABEL` and `LIFETIME_SECONDS`.
  - `sweep_expired() -> None`: containers first, then networks, using
    `docker network ls --filter label=... --format '{{.ID}} {{.Label "<EXPIRY_LABEL>"}}'`
    and `docker network rm`, which never raises, the same as `_remove`.
  - `start_qbt(downloads: Path, *, network: str | None = None) -> tuple[str, QbtHarness]`:
    the body of today's fixture. It returns the container name and a
    logged-in harness, and adds `--network <network>` when it is given.
  - `stop_qbt(name: str, harness: QbtHarness | None) -> None`.
  - `container_ip(name: str, network: str) -> str`: uses `docker inspect` with
    `{{(index .NetworkSettings.Networks "<network>").IPAddress}}`. It raises
    `HarnessError` when the result is empty.
  - `qbt_pair(tmp: Path) -> Iterator[Pair]`, a context manager. It creates the
    network `lintarr-conformance-net-<port>` with the expiry label, starts the
    seeder and the leecher on it, and tears both down along with the network.
  - `Pair` has `seeder: QbtHarness`, `leecher: QbtHarness` and
    `seeder_ip: str`.
  - `conftest.py`'s `qbt` fixture becomes a thin wrapper over
    `start_qbt`/`stop_qbt`. A new module-scoped fixture `qbt_pair` wraps
    `qbt_pair()`.

- [ ] **Step 1: Write failing tests** by monkeypatching `containers._run` to
  record its argument lists and return canned `CompletedProcess` values:
  - `test_sweep_removes_expired_networks`: one expired network and one live
    one. Only the expired ID gets `network rm`, and container removal is
    issued first.
  - `test_container_ip_empty_is_harness_error`.
  - `test_start_qbt_joins_the_network_when_given`: the `run` args contain
    `--network`, `lintarr-conformance-net-1`.
  - Move the existing tests of `_expired` (if any) to import from `containers`.
- [ ] **Step 2: Run** the tests. Expected: FAIL (no module).
- [ ] **Step 3: Implement.** Move the helpers, keep the docstrings and the
  port-rule comment, and leave `conftest.py` importing them.
- [ ] **Step 4: Run** `uv run pytest -q` and then `uv run pytest -m container -q tests/conformance/test_harness_smoke.py`.
  Expected: both PASS. The second shows the refactored `qbt` fixture still
  works against Docker.
- [ ] **Step 5: Commit** `refactor(conformance): lift container lifecycle; add network and seeder/leecher pair`.

### Task 3: Peer smoke test (feasibility gate)

**Files:**
- Create: `tests/conformance/uploading.py` (starts holding `PEER_PREFS` and `bring_up`)
- Test: `tests/conformance/test_peer_smoke.py` (`container`)

**Interfaces:**
- Consumes: Task 2's `Pair` and Task 1's API.
- Produces:
  - `PEER_PREFS: dict[str, object]`, applied to both sides:
    `listen_port` 6881, `random_port` False, `upnp` False, `dht` False,
    `pex` False, `lsd` False, `limit_lan_peers` True.
  - `seed(pair, names: list[str]) -> list[HarnessTorrent]`: adds each torrent
    complete on the seeder and incomplete on the leecher. It caps each leecher
    torrent with `set_download_limit(..., 65536)` and calls
    `add_peers(..., [f"{pair.seeder_ip}:6881"])`.

- [ ] **Step 1: Write the test** `test_leecher_makes_a_seeder_upload_above_threshold`:
  - Apply `PEER_PREFS` plus unlimited queue limits on both sides, using
    `set_prefs_verified` from `runner.py`.
  - `seed(pair, ["smoke-0"])`.
  - Within 60 s, the seeder's row is `uploading` with `upspeed > 2048` on 2
    consecutive polls.
  - Within the same 60 s, the leecher's `upspeed` for it stays ≤ 65536 × 1.5,
    which shows the cap is enforced on the LAN.
- [ ] **Step 2: Run** `uv run pytest -m container -q tests/conformance/test_peer_smoke.py`.
  - If it fails because no peer connects, switch `seed()` to
    `make_torrent(..., private=False)` and record why in a code comment and in
    the ledger. Then re-run.
  - If it still fails, **stop**. Report BLOCKED with the rows, because the
    spec's approach does not work.
- [ ] **Step 3: Commit** `test(conformance): seeder uploads to a capped leecher over addPeers`.

### Task 4: Pure decision logic

**Files:**
- Modify: `tests/conformance/uploading.py`
- Test: `tests/conformance/test_uploading_logic.py` (new, not `container`)

**Interfaces:**
- Produces:
  - `Poll` is a frozen dataclass with `t: float` (seconds since the event) and
    `rows: tuple[Row, ...]`.
  - `ready(rows, seeders: set[str], *, expect_active: int) -> bool`. It is true
    when each seeder hash is `uploading` with `upspeed > 2048` or `queuedUP`,
    and exactly `expect_active` are `uploading`. U0 has no leecher, so U0
    readiness is the separate `idle_ready(rows, seeders)`: all seeders are
    `stalledUP`.
  - `outcome(before: Counts, polls: list[Poll], seeders: set[str], downloads: set[str], *, uploading: bool, stable_s: float = 30.0) -> Outcome`.
    It takes the polls with `t >= last.t - stable_s`.
    - Their `tally` (from `states.py`, over `{r.hash: r.state}`) across
      seeders ∪ downloads must be identical. If not, it raises
      `UnstableWindow(AssertionError)` with every poll in the slice.
    - When `uploading` is True, every seeder in `Bucket.SEEDING` in that slice
      must be `uploading` with `upspeed > 2048`. If not, it raises
      `HarnessError("seeder stopped uploading")`. U0 passes `uploading=False`.
    - It returns an `Outcome`.
  - `Outcome` is a frozen dataclass with `before: Counts`, `after: Counts` and
    `label: str`. The label is `"starts"` when `after.downloading >= 1` and
    `after.seeding_queued > before.seeding_queued`. It is `"queued"` when
    `after.queued == len(downloads)` and
    `after.seeding_queued == before.seeding_queued`. Otherwise it is
    `"other"`. The pin compares `(before, after)` counts. The label is for
    the doc.

- [ ] **Step 1: Write failing tests** on hand-built `Row`s:
  - `test_ready_accepts_queued_seeders`: 3 `uploading` above threshold plus 2
    `queuedUP`, with `expect_active=3`, is True. With `expect_active=5` it is
    False.
  - `test_ready_rejects_slow_uploader`: `upspeed` 2048 is not above the
    threshold.
  - `test_outcome_starts`: before `(0,0,3,0,0)`. Final 30 s:
    `(1,0,2,1,0)` on every poll. The label is `"starts"`.
  - `test_outcome_queued`: the final polls hold `(0,1,3,0,0)`. The label is
    `"queued"`.
  - `test_unstable_window_is_a_disagreement`: one poll in the final 30 s
    differs. It raises `UnstableWindow`, and the message contains every poll's
    `t`.
  - `test_window_seeder_not_uploading_is_harness_error`: an active seeder is
    `stalledUP` in the final 30 s while `uploading=True`.
  - `test_unstable_window_is_classified_disagreement`:
    `classify_report` on a report carrying `UnstableWindow` returns
    `"disagreement"`. Build the report the way `test_known_registry.py` does.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(conformance): readiness, window stability and outcome for uploading seeders`.

### Task 5: Scenario driver and probe tool; take the measurement

**Files:**
- Modify: `tests/conformance/uploading.py`
- Create: `tools/probe_qbt_uploading.py`
- Create: `docs/measurements/2026-11-16-qbt-uploading-seeders.md`

**Interfaces:**
- Produces:
  - `UploadScenario` is a frozen dataclass with `id: str`, `cfg: QueueConfig`,
    `n_seeders: int`, `n_downloads: int`, `leecher: bool` and
    `expect_active: int`.
  - `UPLOAD_SCENARIOS` holds the four scenarios from Global Constraints. U0,
    U1-off and U1-on use `expect_active=3`. W-off uses `expect_active=3`, the
    F2 prediction. If the run shows otherwise, the measured value replaces it
    and the doc says why.
  - `run_scenario(pair, sc, *, rep: int) -> tuple[Outcome, list[Poll]]`. It
    calls `reset()` on both sides, applies `prefs_for(sc.cfg, slow_timer_s=1) | PEER_PREFS`
    verified on both, and seeds. Torrent names are `f"{sc.id}-r{rep}-c{i}"`.
    It brings up with a 60 s limit, raising `HarnessError` that names the last
    rows. It records `before`, then adds `n_downloads` incomplete torrents to
    the **seeder only**, with no peers. It polls `rows()` every 1 s for 120 s
    and returns `outcome(...)` along with the polls.
  - The probe tool runs each scenario `--reps 3` (the default) inside
    `qbt_pair()` and writes one JSON line per poll to stdout, plus a summary
    table to stderr.

- [ ] **Step 1: Implement** `UPLOAD_SCENARIOS`, `run_scenario` and the probe tool.
- [ ] **Step 2: Run** `uv run python tools/probe_qbt_uploading.py --reps 3 > scratch/uploading.jsonl`,
  where `scratch/` is the session scratchpad, not the repo.
- [ ] **Step 3: Write the measurement doc** in the style of
  `2026-11-13-qbt-conformance-assumptions.md`:
  - TL;DR, method, settings read back, and per scenario the three runs'
    `before`/`after`/label.
  - Raw rows at bring-up and at the decisive final poll for one run each.
  - What the result means for the `_WEDGED` pin and F3, per the spec's "Why".
    State this as findings. The decisions are Tom's.
  - If runs disagree, the scenario is marked **unresolved**.
- [ ] **Step 4: Commit** `docs(measurements): uploading seeders against max_active_torrents (P2 slice 2)`.

### Task 6: Pin in the nightly

**Files:**
- Create: `tests/conformance/test_uploading_seeders.py` (`container`)
- Modify: `.github/workflows/conformance.yml` (the timeout comment only)

**Interfaces:**
- Consumes: `UPLOAD_SCENARIOS`, `run_scenario` and the `qbt_pair` fixture.
- Produces: `RECORDED: dict[str, tuple[Counts, Counts]]`, holding
  `(before, after)` from the measurement doc for every scenario not marked
  unresolved.

- [ ] **Step 1: Write** `test_uploading_scenario_matches_measurement`, parametrized over `RECORDED`.
  - It wraps `run_scenario(..., rep=<unique per run>)` in `with_one_retry`.
  - It asserts `(o.before, o.after) == RECORDED[id]`. The message holds the
    label and the final 30 s of polls.
  - `test_recorded_cites_the_doc` (not `container`): every `RECORDED` key
    appears in the measurement doc, and every unresolved scenario is absent.
- [ ] **Step 2: Mutation check.** Change one `RECORDED` count, run, and confirm
  it goes red with kind `disagreement` in `conformance-result.json`. Restore it.
  Note the result in the ledger.
- [ ] **Step 3: Run the full container suite once** with
  `time uv run pytest -m container -q`. Record the wall time. If it exceeds
  30 min, stop and report. Otherwise, update the workflow's timeout comment
  with the measured figure.
- [ ] **Step 4: Commit** `test(conformance): pin the uploading-seeder outcomes in the nightly`.

### Task 7: Record and hand over

**Files:**
- Modify: `CONTINUATION.md`

- [ ] **Step 1:** Under Open items, add the slice 2 result:
  - each scenario's label
  - what it says about the `_WEDGED` pin and F3
  - "`DECISION NEEDED:` (Tom): the `_WEDGED` pin and the F1/F3 predicate/simulate()/trace() change"
  - a note that the nightly now runs about N min
- [ ] **Step 2: Commit** `docs(continuation): uploading-seeder result and the decisions it unblocks`.
