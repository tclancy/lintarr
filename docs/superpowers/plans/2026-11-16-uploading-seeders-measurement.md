# Uploading-Seeder Measurement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether actively uploading seeders hold qBittorrent 5.2.3's
`max_active_torrents` slots against a new download, record the result, and pin
it in the nightly.

**Architecture:** A second container from the same pinned image acts as the
leecher. It sits on a private docker network with the seeder container and is
connected with `addPeers`. Each torrent's download is capped so the seeder keeps
uploading. The decision logic (bring-up readiness, upload continuity, window
stability, verdict) is pure functions over recorded polls, unit-tested without
Docker. A probe tool runs each scenario three times and keeps every poll, even
from a failed run. A `container` test then pins the recorded counts.

**Tech Stack:** Python 3.13, uv, pytest, httpx, Docker, `lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.14-ls476`.

**Spec:** `docs/superpowers/specs/2026-11-15-uploading-seeders-measurement-design.md`

**Plan review:** an independent review on 2026-11-16 found 2 Critical,
9 Important and 5 Minor problems. All are folded in below.

## Global Constraints

- **Out of bounds:** `src/`, `tests/model/queue.py` (`simulate()`, `trace()`),
  the `_WEDGED` fixture and its exit-1 pin. None of them change.
- **Slice 1 is untouched:** the existing session `qbt` fixture and slice 1's
  scenarios stay in their peerless world. The pair is a separate fixture.
- **Image:** `QBT_IMAGE` from `tests/conformance/image.py`. No new image and no
  new dependency.
- **WebUI port rule:** publish the same port on both sides and set `WEBUI_PORT`
  to it. Log in exactly once per container, because of the 5-failure IP ban
  (`tests/conformance/conftest.py:1-10`).
- **Download directories:** the seeder and the leecher each get their **own**
  directory. A shared one would let the leecher find the data already on disk
  and turn into a second seeder.
- **Seeder preferences:** `prefs_for(sc.cfg, slow_timer_s=1) | PEER_PREFS`.
- **Leecher preferences:** `prefs_for(QueueConfig(False, -1, -1, -1, False, False), slow_timer_s=1) | PEER_PREFS`.
  Queueing is off, so the leecher's own queue never limits what it downloads.
- **Preference read-back:** both sides are verified with `set_prefs_verified`
  over `PREF_KEYS | PEER_PREFS.keys()`.
- **Sizes and rates:** seeding torrents are 32 MiB with 256 KiB pieces. The
  leecher caps each torrent at 65536 bytes/s. The slow threshold is the image
  default of 2 KiB/s, i.e. 2048 bytes/s of seeder `upspeed`.
- **Timing:** `slow_torrent_inactive_timer=1`. Bring-up gets at most 60 s. The
  observation window is 120 s, polled every 1 s. The verdict is read over the
  final 30 s of the window, which must be stable.
- **Scenarios** `(dl, ul, tot, exemption)`:
  - U0-off: -1/-1/3, off. 3 seeders, **no** leecher, then 1 incomplete torrent.
  - U1-off: -1/-1/3, off. 3 uploading seeders, then 1 incomplete torrent.
  - U1-on: -1/-1/3, on. Otherwise the same as U1-off.
  - W-off: 3/3/5, off. 5 seeders with the leecher on all 5, then 3 incomplete
    torrents.
- **Agreement rule:** the 3 measurement runs of a scenario must agree on
  `(before, after)` before that result is pinned. If they disagree, the
  scenario is **unresolved** and is not pinned.
- **Failure kinds** feed the existing result file. `HarnessError` (a broken
  bring-up, or a seeder that stopped uploading) is `harness-error`.
  `AssertionError` (`UnstableWindow`, or a changed outcome) is `disagreement`.
- **Nightly budget:** the pytest step's 35-minute timeout stays.
  - If a clean full `-m container` run measures over 30 min, shrink the window
    to 60 s.
  - Only do that if the measurement shows every run's counts were stable by
    `t = 30 s`. Record that justification in the doc.
  - If they were not stable by then, stop and report.
- **Commits:** use the bot identity:
  `eval "$(dispatch mint-token)" && dispatch bot-identity --quiet && git commit ...`.
  Never use `--no-verify`.

## Review Focus

1. **Private torrents never connect over `addPeers`.** Bring-up must fail as a
   named `HarnessError` that carries the last rows. Task 3 is the gate, and
   `private=False` is the fallback.
2. **An active seeder stops uploading mid-window** (the leecher finished, or a
   peer dropped). That must be a `harness-error`, not a verdict. The check runs
   on every poll, and a seeder is excused only once it has gone `queuedUP`.
   Pinned by `test_seeder_stopping_mid_window_is_harness_error`.
3. **The final 30 s are unstable.** That must be a failure that carries the raw
   polls. Pinned by `test_unstable_window_is_a_disagreement`.
4. **Leaked networks.** Pinned by `test_sweep_removes_expired_networks`.
5. **W-off is judged by free slots, not by "a seeder was demoted".** With
   ul=3, 2 total slots are free before any download arrives. Pinned by
   `test_verdict_hold_when_downloads_fit_free_slots`.

---

### Task 1: Harness API additions

**Files:**
- Modify: `tests/conformance/qbt.py`, `tests/conformance/torrents.py`,
  `tests/conformance/runner.py` (`set_prefs_verified` key set only)
- Test: `tests/conformance/test_qbt_client.py` (new, not `container`), and
  `tests/conformance/test_prefs_readback.py`

**Interfaces:**
- Produces:
  - `QbtHarness.__init__(base_url, password, downloads, *, transport: httpx.BaseTransport | None = None)`.
  - `add_peers(hashes: list[str], peers: list[str]) -> None`. POST
    `/api/v2/torrents/addPeers` with `|`-joined `hashes` and `peers`
    (`ip:port`).
  - `set_download_limit(hashes: list[str], limit: int) -> None`. POST
    `/api/v2/torrents/setDownloadLimit`, with `limit` in bytes/s.
  - `rows() -> list[Row]`. `Row` is a frozen dataclass of `hash: str`,
    `name: str`, `state: str`, `upspeed: int`, `dlspeed: int` and
    `num_leechs: int`. `states()` becomes
    `{r.hash: r.state for r in self.rows()}`.
  - `make_torrent(name, size=16384, piece_length=16384, *, private: bool = True)`.
    It omits the `private` key when `private` is False.
  - `set_prefs_verified(h, prefs, *, extra_keys: frozenset[str] = frozenset())`.
    It reads back `PREF_KEYS | extra_keys`. The default leaves today's
    behaviour unchanged.

- [ ] **Step 1: Write the failing tests.** Use an `httpx.MockTransport` that
  records each request.
  - `test_add_peers_posts_pipe_joined_hashes_and_peers`: the form has
    `hashes == "a|b"` and `peers == "172.18.0.2:6881"`.
  - `test_set_download_limit_posts_bytes_per_second`: the form has
    `limit == "65536"`.
  - `test_rows_reads_speeds_and_leechers`: the row
    `{"hash":"h","name":"n","state":"uploading","upspeed":70000,"dlspeed":0,"num_leechs":1}`
    becomes `Row("h","n","uploading",70000,0,1)`.
  - `test_non_200_raises_harness_error`: a 400 answer to `addPeers` raises.
  - `test_make_torrent_public_has_no_private_key`:
    `b"7:privatei1e" not in make_torrent("x", private=False).metainfo`, and
    the default `make_torrent` still has it.
  - In `test_prefs_readback.py`: `test_extra_keys_are_read_back`, where a
    mismatched `dht` raises `HarnessError` only when it is passed in
    `extra_keys`.
- [ ] **Step 2: Run** `uv run pytest tests/conformance -q -k "qbt_client or prefs_readback"`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the additions.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(conformance): addPeers, download limit, rich rows and extra read-back keys`.

### Task 2: Lift the container lifecycle; add the network and the pair

**Files:**
- Create: `tests/conformance/containers.py`
- Modify: `tests/conformance/conftest.py`
- Test: `tests/conformance/test_containers.py` (new, not `container`)

**Interfaces:**
- Consumes: Task 1's `QbtHarness`.
- Produces:
  - **Moved unchanged** from `conftest.py` to `containers.py`: `_free_port`,
    `_run`, `_docker`, `_expired`, `_warn_rm_failed`, `_remove`,
    `_wait_ready`, `_temp_password`, `EXPIRY_LABEL`, `LIFETIME_SECONDS`,
    `READY_TIMEOUT`, `PULL_TIMEOUT`, `DOCKER_TIMEOUT` and the port-rule
    module docstring.
  - **Renamed:** `_sweep_expired` becomes the public `sweep_expired() -> None`.
    It removes expired containers first, then expired networks: it lists them
    with `docker network ls --filter label=<EXPIRY_LABEL> --format '{{.ID}} {{.Label "<EXPIRY_LABEL>"}}'`,
    then runs `docker network rm`. Like `_remove`, it never raises.
  - `start_qbt(downloads: Path, *, network: str | None = None) -> tuple[str, QbtHarness]`.
    This is today's fixture body. It adds `--network <network>` when one is
    given, and it returns the container name and a logged-in harness.
  - `stop_qbt(name: str, harness: QbtHarness | None) -> None`.
  - `container_ip(name: str, network: str) -> str`. It runs `docker inspect -f`
    with `{{(index .NetworkSettings.Networks "<network>").IPAddress}}` and
    raises `HarnessError` if the result is empty.
  - `qbt_pair(tmp: Path) -> Iterator[Pair]` (a context manager). It creates the
    network `lintarr-conformance-net-<port>` with the expiry label, starts the
    seeder on `tmp / "seeder"` and the leecher on `tmp / "leecher"`, and tears
    down both containers, then the network.
  - `Pair` holds `seeder: QbtHarness`, `leecher: QbtHarness` and
    `seeder_ip: str`.
  - In `conftest.py`, the `qbt` fixture wraps `start_qbt`/`stop_qbt` and calls
    `sweep_expired()`. A new module-scoped `qbt_pair` fixture wraps
    `qbt_pair(tmp_path_factory.mktemp("pair"))`.

- [ ] **Step 1: Write the failing tests.** Monkeypatch `containers._run` to
  record argument lists and return canned `CompletedProcess` results.
  - `test_sweep_removes_expired_networks`: given one expired network and one
    live network, only the expired network gets `network rm`, and every
    container `rm` comes before it.
  - `test_container_ip_empty_is_harness_error`.
  - `test_start_qbt_joins_the_network_when_given`: the `run` args contain
    `--network` followed by `lintarr-conformance-net-1`.
  - `test_pair_uses_separate_download_dirs`: patch `start_qbt` and assert the
    two `downloads` arguments differ.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** Keep `conftest.py`'s result-file hooks where they
  are.
- [ ] **Step 4: Run** `uv run pytest -q`, then `uv run pytest -m container -q tests/conformance/test_harness_smoke.py`. Expected: both PASS. The second run
  proves the refactored `qbt` fixture still works against Docker.
- [ ] **Step 5: Commit** `refactor(conformance): lift container lifecycle; add network and seeder/leecher pair`.

### Task 3: Peer smoke test (feasibility gate)

**Files:**
- Create: `tests/conformance/uploading.py`
- Test: `tests/conformance/test_peer_smoke.py` (`container`)

**Interfaces:**
- Consumes: Task 2's `Pair`, and Task 1's API and `extra_keys`.
- Produces, in `uploading.py`:
  - `PEER_PREFS: dict[str, object]` = `listen_port` 6881, `random_port` False,
    `upnp` False, `dht` False, `pex` False, `lsd` False, `limit_lan_peers`
    True.
  - `LEECHER_CFG = QueueConfig(False, -1, -1, -1, False, False)`.
  - `apply_prefs(pair, seeder_cfg: QueueConfig) -> None`. It runs
    `set_prefs_verified` on both sides with
    `extra_keys=frozenset(PEER_PREFS)`, using the seeder and leecher
    preferences from Global Constraints.
  - `add_seeders(h: QbtHarness, torrents) -> None`. It adds the torrents
    complete, then waits up to 15 s until every one shows `stalledUP` or
    `queuedUP`. Otherwise it raises `HarnessError`.
  - `attach_leecher(pair, torrents) -> None`. It adds the torrents incomplete
    on the leecher, calls `set_download_limit(..., 65536)` and then
    `add_peers(..., [f"{pair.seeder_ip}:6881"])`.
  - `reconnect(pair, torrents) -> None`. It re-issues `add_peers` and is called
    on every bring-up poll until the seeder is ready. This sidesteps
    libtorrent's 60 s reconnect back-off.

- [ ] **Step 1: Write** `test_leecher_makes_a_seeder_upload_above_threshold`.
  - `apply_prefs` with unlimited seeder limits; build one torrent, `smoke-0`.
  - `add_seeders`, then `attach_leecher`.
  - Poll for up to 60 s, calling `reconnect` on each poll. The seeder's row
    must be `uploading` with `upspeed > 2048` on 2 consecutive polls.
  - Then, over 10 s, the leecher's `dlspeed` must stay at or below
    65536 × 1.5. That shows the cap is enforced on the LAN.
- [ ] **Step 2: Run** `uv run pytest -m container -q tests/conformance/test_peer_smoke.py`.
  - If no peer connects, switch to `make_torrent(..., private=False)` for
    uploading torrents only. Record why in a code comment and the ledger, and
    re-run.
  - If it still fails, **stop**: report BLOCKED with the rows.
- [ ] **Step 3: Commit** `test(conformance): seeder uploads to a capped leecher over addPeers`.

### Task 4: Pure decision logic

**Files:**
- Modify: `tests/conformance/uploading.py`
- Test: `tests/conformance/test_uploading_logic.py` (new, not `container`)

**Interfaces:**
- Produces:
  - `Poll` is a frozen dataclass of `t: float` (seconds since the event) and
    `rows: tuple[Row, ...]`.
  - `ready(rows, seeders: set[str]) -> bool`. True when every seeder hash is
    either `queuedUP` or `uploading` with `upspeed > 2048`, and at least one is
    `uploading`. Callers require it on 2 consecutive polls.
  - `idle_ready(rows, seeders) -> bool`. True when every seeder is
    `stalledUP` or `queuedUP` (U0).
  - `active_seeders(rows, seeders) -> int`. The number of seeders not in
    `queuedUP`.
  - `before_counts(rows, seeders) -> Counts`. `tally` over the **seeders
    only**, so the downloads, which have not been added yet, never count as
    LEFT.
  - `check_uploading(polls, seeders) -> None`. On every poll, a seeder that
    has never been seen `queuedUP` so far must be `uploading` with
    `upspeed > 2048`. Once a seeder has been seen `queuedUP` it is excused for
    the rest of the window. A violation raises
    `HarnessError("seeder stopped uploading")`, naming the poll `t` and the
    row.
  - `settled(polls, seeders, downloads, *, stable_s=30.0) -> Counts`. It
    tallies seeders ∪ downloads over the polls with `t >= last.t - stable_s`.
    Those tallies must all be equal. Otherwise it raises
    `UnstableWindow(AssertionError)`, listing every poll's `t` and counts.
  - `verdict(before: Counts, after: Counts, *, tot: int, n_downloads: int) -> str`.
    Let `free = max(0, tot - (before.seeding)) if tot != -1 else n_downloads`
    and `started = after.downloading`.
    - `"yield"` if `after.seeding_queued > before.seeding_queued`, or if
      `started > free`.
    - `"hold"` if `after.seeding_queued == before.seeding_queued` and
      `started <= free` and `after.queued == n_downloads - started`.
    - Otherwise `"other"`.
  - `Outcome` is a frozen dataclass of `before: Counts`, `after: Counts`,
    `active_at_bringup: int` and `verdict: str`. The pin compares
    `(before, after)`. The verdict is for the doc.

- [ ] **Step 1: Write the failing tests** on hand-built `Row`s.
  - `test_ready_accepts_queued_seeders`: 3 `uploading` at 70000 plus 2
    `queuedUP` gives True. All 5 `queuedUP` gives False.
  - `test_ready_rejects_slow_uploader`: an `upspeed` of 2048 is not above the
    threshold.
  - `test_before_counts_ignores_absent_downloads`: 3 `uploading` seeders give
    `(0,0,3,0,0)`.
  - `test_seeder_stopping_mid_window_is_harness_error`: a seeder that never
    went `queuedUP` is `stalledUP` at `t=50`.
  - `test_demoted_seeder_is_excused`: a seeder goes `queuedUP` at `t=2` and
    has `upspeed` 0 afterwards. Nothing is raised.
  - `test_unstable_window_is_a_disagreement`: one poll in the final 30 s
    differs. `UnstableWindow` is raised, and its message contains every poll's
    `t`.
  - `test_verdict_yield_u1`: before `(0,0,3,0,0)`, after `(1,0,2,1,0)`,
    tot 3, 1 download gives `"yield"`.
  - `test_verdict_hold_u1`: after `(0,1,3,0,0)` gives `"hold"`.
  - `test_verdict_hold_when_downloads_fit_free_slots`: W-off, before
    `(0,0,3,2,0)`, after `(2,1,3,2,0)`, tot 5, 3 downloads gives `"hold"`.
  - `test_verdict_yield_w`: before `(0,0,3,2,0)`, after `(3,0,2,3,0)` gives
    `"yield"`.
  - `test_unstable_window_is_classified_disagreement`: `classify_report` on a
    report carrying `UnstableWindow` gives `"disagreement"`. Build the report
    the way `test_known_registry.py` does.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(conformance): readiness, upload continuity, window stability and verdict`.

### Task 5: Scenario driver and probe tool; take the measurement

**Files:**
- Modify: `tests/conformance/uploading.py`
- Create: `tools/probe_qbt_uploading.py`, `docs/measurements/2026-11-16-qbt-uploading-seeders.md`

**Interfaces:**
- Produces:
  - `UploadScenario` is a frozen dataclass of `id: str`, `cfg: QueueConfig`,
    `n_seeders: int`, `n_downloads: int` and `leecher: bool`.
  - `UPLOAD_SCENARIOS` holds the four scenarios from Global Constraints. U0
    has `leecher=False`.
  - `observe(pair, sc, *, rep: int) -> Recording`. It never judges, so a run
    keeps its raw data whatever happens.
    1. `reset()` both sides, then `apply_prefs`.
    2. Name torrents `f"{sc.id}-r{rep}-c{i}"` (complete) and
       `f"{sc.id}-r{rep}-i{i}"` (incomplete).
    3. Call `add_seeders`. If `sc.leecher` is set, call `attach_leecher`.
    4. Bring-up: poll every 1 s for up to 60 s, calling `reconnect` while not
       ready, until `ready` (or `idle_ready` for U0) holds on 2 consecutive
       polls.
    5. Add the `n_downloads` incomplete torrents to the **seeder only**. They
       get no peers.
    6. Poll `rows()` every 1 s for 120 s.
  - `Recording` is a dataclass of `bringup: list[Poll]`, `window: list[Poll]`
    and `error: str | None`. A bring-up timeout sets `error` and returns
    early, so it never raises. An exception during the window is caught into
    `error` too, with the polls kept.
  - `judge(sc, rec) -> Outcome`. It raises `HarnessError` for `rec.error`.
    Otherwise it computes `before_counts(rec.bringup[-1].rows, seeders)`,
    runs `check_uploading` (skipped for U0) and `settled`, and returns
    `Outcome(..., verdict(...))`.
  - The probe tool runs `--reps 3` (the default) of each scenario inside
    `qbt_pair()`. It writes each `Recording` as JSON lines (every bring-up and
    window poll) to stdout. It writes a summary to stderr, one line per run:
    the `Outcome`, or the error.

- [ ] **Step 1: Implement** `UPLOAD_SCENARIOS`, `observe`, `judge` and the probe.
- [ ] **Step 2: Run** `uv run python tools/probe_qbt_uploading.py --reps 3 > <scratchpad>/uploading.jsonl`.
  `<scratchpad>` is the session scratchpad directory, outside the repo.
- [ ] **Step 3: Write the measurement doc** in the style of
  `2026-11-13-qbt-conformance-assumptions.md`:
  - TL;DR, method, and the settings read back from both sides.
  - Per scenario: each run's `active_at_bringup`, `before`, `after` and
    verdict, plus the time from the event to the counts settling. That last
    figure is the justification the budget rule needs.
  - For one run of each scenario: the raw rows of the bring-up poll that
    passed readiness (A2 evidence) and of the decisive final poll.
  - What the results mean for the `_WEDGED` pin and for F3, following the
    spec's "Why". Write these as findings. The decisions are Tom's.
  - If runs disagree, mark the scenario **unresolved**.
- [ ] **Step 4: Commit** `docs(measurements): uploading seeders against max_active_torrents (P2 slice 2)`.

### Task 6: Pin in the nightly

**Files:**
- Create: `tests/conformance/recorded_uploading.py`,
  `tests/conformance/test_uploading_seeders.py` (`container`) and
  `tests/conformance/test_recorded_uploading.py` (not `container`)
- Modify: `.github/workflows/conformance.yml` (timeout comment only)

**Interfaces:**
- Consumes: `UPLOAD_SCENARIOS`, `observe`, `judge` and the `qbt_pair` fixture.
- Produces: `RECORDED: dict[str, tuple[Counts, Counts]]` in
  `recorded_uploading.py`. It maps each scenario not marked unresolved to the
  doc's `(before, after)`.

- [ ] **Step 1: Write** `test_uploading_scenario_matches_measurement`,
  parametrized over `RECORDED`.
  - Inside the attempt closure passed to `with_one_retry`, draw `rep` from a
    module-level `itertools.count()`. That way a retry never reuses torrent
    names.
  - Assert `(o.before, o.after) == RECORDED[id]`. The message carries the
    verdict and the final 30 s of polls.
- [ ] **Step 2: Write** `test_recorded_cites_the_doc` in its own non-container
  module. Every `RECORDED` key must appear in the measurement doc, and no
  scenario the doc marks unresolved may appear in `RECORDED`.
- [ ] **Step 3: Mutation check.**
  - Change one `RECORDED` count, run, and confirm it goes red with kind
    `disagreement` in `conformance-result.json`.
  - Restore it, and note the result in the ledger.
- [ ] **Step 4: Run the full container suite once:**
  `time uv run pytest -m container -q`. Apply the Global Constraints budget
  rule to the wall time. Then update the workflow's timeout comment with the
  measured figure and the worst case with retries.
- [ ] **Step 5: Commit** `test(conformance): pin the uploading-seeder outcomes in the nightly`.

### Task 7: Record and hand over

**Files:**
- Modify: `CONTINUATION.md`

- [ ] **Step 1:** Under Open items, add the slice 2 result:
  - each scenario's verdict
  - what it means for the `_WEDGED` pin and for F3
  - "`DECISION NEEDED:` (Tom): the `_WEDGED` pin and the F1/F3 predicate/simulate()/trace() change"
  - the nightly's measured run time
- [ ] **Step 2: Commit** `docs(continuation): uploading-seeder result and the decisions it unblocks`.
