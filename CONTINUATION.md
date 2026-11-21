# CONTINUATION — lintarr

**Last updated:** 2026-11-21 (UTC)
**State:** `orphan-inevitability` is merged (lintarr#58, main 4623438) and
#70's 3.14.10 test fix is merged (#71, main 6c436c3).
`hardlink-futility` is merged (lintarr#57) and deployed; its first homelab run
passed 2026-11-19 19:08 UTC. `IN PROGRESS:` lintarr#62 on
`claude/62-conformance-test-qbittorrent-share-limit` — container tests for the
qBittorrent share-limit actions (tests and docs only, nothing in `src/`).
Pluggable backends are merged and deployed (lintarr#56, homelab#692). P2
slices 1 and 2 are merged, and the nightly alert is armed.

## #70: the deep-JSON test no longer raises on 3.14.10

- **The symptom:** on a clean `main` at 4623438 under CPython 3.14.10, the
  whole pre-push battery is red on one test. `collect/http.py`'s `get_json`
  catches `RecursionError` beside `ValueError`, and the test provoked it with a
  body nested 20,000 deep.
- **Why it broke:** the depth a CPython can parse is not a constant. Measured,
  default recursion limit 1000 on both: **3.13.7 raises at 20,000**; **3.14.10
  parses 20,000** and raises at 100,000. The guard was never wrong — the
  *provocation* was interpreter-dependent.
- **The fix is test-only.** Nothing in `src/` changed. A
  `_RecursionErrorOnDecode(httpx.Response)` subclass raises from `.json()`
  directly, so the `except` arm is reached on any interpreter at any recursion
  limit; a second test keeps a real 200,000-deep body and asserts only the
  version-independent property (returns a value **or** raises `ServiceError`,
  never `RecursionError`), asserting in both branches.
- **Mutation round** (whole suite, both interpreters, control between every
  mutant): dropping `RecursionError` from the `except` kills the named test on
  3.13.7 and 3.14.10 alike; the mirror mutant dropping `ValueError` is killed
  only by the *other* tests, so the new pair is specific to its arm. Receipts
  in `docs/70-recursion-error-provocation.md`.
- **Left alone on purpose:** CI still pins `python-version: "3.13"` /
  `UV_PYTHON: "3.13"`. Testing 3.14 in CI is a separate decision for Tom, and
  the walkthrough shows the suite green on both so the matrix is cheap if he
  wants it.

## `orphan-inevitability`: does anything remove finished downloads?

- **What it catches:** a seed limit can fire for an arr's torrents, but no
  configured setting removes their files. `remove-keeps-files`: the
  share-limit action is "Remove" (keep files). `nothing-reclaims`: Stop
  without the arr removing it, or super seeding / `Default`.
- **Verified arr behaviour** (Sonarr source, 2026-11-19): the arr removes a
  torrent only when it is stopped *and* has reached a torrent-level or global
  limit, never a category limit; a post-import category hides the torrent from
  the arr; Sonarr v4 season packs use `seasonPackSeedTime` only (unset defers,
  never falls back to `seedTime`).
- **A qBittorrent backend check**, not shared: it reuses `queue_liveness`'s
  share-limit helpers through `clients/qbittorrent/share_limits.py`.
  `Backend.arr_dependent` now tells `run.py` which checks SKIP when an arr
  failed, so `run.py` names no check.
- **Live, strict, homelab, at 414494c and again at 7e78c92 (final check
  logic, 2026-11-19 20:23 UTC):** exit 0, `orphan-inevitability` PASS,
  "qBittorrent removes files at the limit" for both arrs (action
  RemoveWithContent, ratio 1.5 and 14 days).
- **Known limits:** indexer `downloadClientId` pinning; qBittorrent's parent
  category fallback; older arrs not knowing `stoppedUP`; Sonarr v5's
  season-pack ratio/goal toggle; Cleanuparr-reliant stacks FAIL until
  suppression exists. No size is reported (runtime state).
- **Follow-ups:** `seed-goal-conflict` (informational). The share-limit
  actions are now conformance-tested (lintarr#62, below).

## #62: the share-limit actions are measured, not assumed

- **What it adds:** `tests/conformance/test_share_limit_actions.py`, 16
  container items in the nightly, pinning what each `ShareLimitAction` does to
  the torrent *and* to its files. The table lives in
  `tests/conformance/firing.py` (`FATES`) and the check's `ACTION_BY_NAME` is
  asserted against it, so an action nobody measured cannot be mapped silently.
- **Measured 2026-11-21** (`docs/measurements/2026-11-21-qbt-share-limit-actions.md`,
  reproduce with `uv run python tools/probe_qbt_share_limits.py`): Stop (0)
  leaves the torrent `stoppedUP` with its files; Remove (1) drops the torrent
  and **keeps** them (the orphan `remove-keeps-files` is about);
  RemoveWithContent (3) takes both; EnableSuperSeeding (2) leaves it seeding
  with `super_seeding` on. A category's action overrides the global — each of
  the four measured under two contradicting globals — and a `Default` category
  takes whichever fate the global names.
- **Why the harness writes `categories.json`:** qBittorrent 5.2.3's WebAPI
  cannot set a category's share-limit action (`createCategory` /
  `editCategory` read only the paths), so the category test pre-seeds
  `<config>/qBittorrent/categories.json` before the container starts.
  `start_qbt` gained an optional `/config` mount for it.
- **One spec correction:** the *global* `max_ratio_act` can never read back
  `Default`. Set to `-1` it reads back `0`, because qBittorrent coerces the
  stored global to Stop (`sessionimpl.cpp:554-555`). The check's `-1` arm is
  unreachable from this input rather than wrong; the spec's Inputs table said
  "`-1` Default is observable" and now says otherwise.
- **Local gate 2026-11-21:** non-container suite 2853 passed; the 16 new items
  passed in 88.6 s on their own; the whole nightly ran 56 items in 21 min 12 s
  with one failure, `test_uploading_seeders[U0-off]`, the documented macOS
  bring-up flake (classified `harness-error`), which passed on its own in
  144.9 s.

## `hardlink-futility`: the first shared check

- **What it catches:** an arr with hardlinks on, whose download folder and
  library folder sit on different mounts *in the arr's own container*. Every
  "hardlink" import is then silently a full copy.
- **Why it can run from the host:** Sonarr/Radarr's `GET /api/v3/diskspace`
  lists the arr's own mount points (homelab: `/`, `/config`, `/data`), which
  catches the two-bind-mounts-one-disk case that host `stat`/`os.link()` cannot
  see. The original design's free-space cross-check and host link probe are
  dropped (spec, "What changed").
- **First neutral client fact:** `ClientFacts.save_paths` (category -> save
  dir, `""` = uncategorised), populated by qBittorrent and the test fake.
- **Axiom pinned nightly:** `tests/conformance/test_hardlink_exdev.py`
  (`link(2)` across bind mounts is `EXDEV`; within one it succeeds). Fails,
  not skips, without Docker in CI.
- **Live, strict, from a throwaway checkout on homelab:** at 7c0573f
  (2026-11-19 ~18:42 UTC) every new fact read `Known`; at 07a7413
  (2026-11-19 19:00 UTC, the final check logic) `check --json` exit 0, both
  checks PASS, detail "downloads and library folders share a mount for
  radarr[movies], sonarr[tv]".
- **Known limits:** mergerfs/btrfs-subvolume links inside one mount are not
  detected; two same-kind clients sharing a category name can make the wrong
  one claim an arr (a `Check` sees one instance; disambiguating needs a wider
  signature).
- **Merged** as lintarr#57 and deployed 2026-11-19.

## Conformance nightly: alert proven

- **R9** (no token): run 43639901533 failed on the drill disagreement and the
  alert step failed loudly with "NTFY_TOKEN secret is not set".
- **R8** (token set): run 43664824051 failed on the drill disagreement and ntfy
  accepted "lintarr conformance: disagreement" on `homelab-alerts`; Tom
  confirmed it arrived on his phone.
- **Plain run** 43666053096: green, 33 passed in 12m24s, alert step skipped.
- Lesson: `gh secret set NAME` run through Claude Code's `!` has no TTY, reads
  an empty stdin and stores `""` without complaint (run 43663689254). The
  secret list then shows a fresh `updatedAt`, which proves nothing. An empty
  secret logs as `NAME: ` where a real one logs `NAME: ***`. Set secrets from a
  real terminal.

## What lintarr is, and why

A sidecar that reads the live configuration of an arr stack and proves, **from
configuration alone**, whether the settings can coexist.

It exists because of a real incident on homelab: qBittorrent had
`max_active_torrents = 5` with share limits disabled. Seeding torrents count
against that limit, so once five torrents completed they held every slot
permanently. Fifty-two torrents, zero active downloads, zero kB/s, **no error
anywhere**, for weeks. Every individual setting was legal; the combination was
not.

Every peer tool (Cleanuparr, Decluttarr, Maintainerr, Sanitarr) reacts to
observed runtime state, so by construction they engage only after the damage.
Recyclarr writes config but never checks it. The gap is static cross-service
consistency.

## Where it stands

- **Collect (P0a) and `queue-liveness` (P0b) are done.** `lintarr check` exits
  0/1/2/3 (PASS / FAIL / ERROR / SKIP-or-N/A under `--strict`). See
  `src/lintarr/outcomes.py`.
- **Deployed** per `docs/superpowers/specs/2026-10-05-deploy-scope.md`:
  - `lintarr.timer` fires `lintarr.service` at :17 each hour.
  - `--state-file` (`src/lintarr/edge.py`) means a problem pages once through
    homelab's `unit-failure-alert-user@`, not every hour. That handler
    (`/usr/local/bin/unit-failure-alert.sh`) pushes to ntfy topic
    **`homelab-alerts`**, and escalates a repeating failure as `urgent` with a
    copy to `claude`.
  - Units and env live in homelab `roles/native-apps` (homelab#542).
  - Sonarr/Radarr keys are read from each app's `config.xml` at deploy time.
- **Operate it:** `itguy deploy lintarr`, `itguy logs lintarr --level info`.
  `itguy restart lintarr` exits 1 by design, because nothing long-running
  exists.
- **Alert path proven end to end** (2026-10-07). A drop-in pointed
  SONARR_URL (now `ARR_URL`) at a dead port. The run ERRORed with exit 2, OnFailure= fired,
  and the ntfy arrived. Reverting returned it to PASS.
- The former blockers are gone:
  - The qBittorrent password is vaulted (homelab#190).
  - The orphan-credential guard is a usage error (#3).
  - The homelab#393 fixture is in `tests/fixtures/homelab.py`.

## Review gate

- **Provisioned 2026-11-12 (UTC).** `CLAUDE_CODE_OAUTH_TOKEN` was set via
  `metaframework/scripts/sync-claude-review-secret.sh`; the Claude GitHub App
  is installed account-wide. `claude-code-review.yml` now reviews for real and
  `require-review.yml` should go green once a review exists for the head.
- Still a soft signal: no ruleset requires the status (`rulesets` is `[]`).
- Re-check fleet-wide with `sync-claude-review-secret.sh --check`.

## Open items

- **P2 slice 2 result (2026-11-16, merged to `main` 2026-11-17; Linux run
  43998520392 green, 21m17s).** Measured in
  `docs/measurements/2026-11-16-qbt-uploading-seeders.md`; all four scenarios
  agreed 3 of 3 and every one is **yield**. Count tuples (before -> after) are
  as the measurements doc defines them:
  U0-off (idle control) and U1-off/U1-on (uploading, exemption off/on)
  `(0,0,3,0,0)->(1,0,2,1,0)`; W-off (`_WEDGED`'s 3/3/5)
  `(0,0,3,2,0)->(3,0,2,3,0)`. Uploading seeders yield `max_active_torrents`
  slots exactly as idle ones do, the slow exemption made no difference, and
  `_WEDGED`'s limits do not wedge through seeders on 5.2.3. The nightly now
  runs 56 items, ~21 min measured locally (38 items when this was written;
  worst case with retries ~33 min vs the 35 min step timeout). **Watch item:** a `missingFiles` on the first
  seeder add was seen twice in ~60 adds, both after a fresh pair (macOS Docker, cause unconfirmed); seeder data is now
  fsynced before the add and the one retry absorbs a single hit.
  Decided (Tom, 2026-11-17): leave the `_WEDGED` pin as it is. Still open: the `_WEDGED` pin (`tests/test_check_cli.py`, exit
  1) rests on a config that does not wedge through seeders on 5.2.3, so #393's
  wedge needs another mechanism (leading candidate: stalled dead-swarm
  downloads, item 3e). And the F1/F3 change: the predicate premise,
  `simulate()` and `trace()` must change together, keeping R3 (`trace()` agrees with `simulate()`, tests/model/test_queue_trace.py:89).
- **F1 (P2 differential, 2026-11-13; extended 2026-11-16).** `trace()` rule 1 and the predicate's
  absorbing-state claim ("seeders never release slots", `queue_liveness.py:9`)
  are believed wrong for idle seeders on qBittorrent 5.2.3: with
  `max_active_torrents` full of seeders, new downloads start and seeders yield
  their slots (`queuedUP`). Seeders still count against the limit. Known in
  scenarios 1-wedged-slow-off, 1b-*-slow-off and 7-*-slow-off
  (`tests/conformance/known.py`). It leaves spec Goal 4 (a live test that a
  seeder blocks a download) unmet; #393's wedge needs a mechanism F1 did not
  reproduce (see the measurements doc). Slice 2 extends F1 to actively
  uploading seeders: they yield slots too (U1), so the absorbing-state claim is
  wrong for them as well.
- **F2 (P2 differential, 2026-11-13).** `trace()` rule 2 (`max_active_uploads`
  is not consulted, `seeding_queued` is always 0) is wrong: the upload limit
  queues seeders (`queuedUP`) when the slow exemption is off. Known in
  1-wedged-slow-off.
  It also contradicts the **shipped predicate**, not only `trace()`. The
  predicate FAILs the `_WEDGED` fixture (3/3/5, exemption off) and
  `tests/test_check_cli.py` pins exit 1 on it. On 5.2.3 that fixture does not
  wedge: in measurement item 4 (ul=3, tot=5) two seeders sit `queuedUP` and the
  three downloads start. The predicate's own docstring already says why that
  must be so: "a seeder that cannot get one does not hold a download back"
  (`_no_slot_for_a_first_download`, `queue_liveness.py:483-484`). With ul=3 only
  three seeders are active, so five seeders cannot hold five torrent slots. The
  module's caveat that the wedge axiom "has never been validated against a
  running qBittorrent" (`queue_liveness.py:80-85`) is stale: it has now been
  measured, and on this build it does not hold for idle seeders. `src/` was not
  changed in P2 slice 1 (R7), so the fixture and its exit-1 pin stand until Tom
  decides what the FAIL should rest on.
- **F3 (P2 differential, 2026-11-13).** `trace()` rule 3 is wrong:
  `dont_count_slow_torrents` frees `max_active_downloads` (and, observed in
  1-wedged-slow-on, the upload limit) but NOT `max_active_torrents`. Known in
  1-wedged-slow-on, 1b-*-slow-on and 7-*-slow-on. The shipped predicate's
  slow-exemption premise (`qbt.slow_exempt_off`, `queue_liveness.py:1042`)
  treats the exemption as rescuing a full `max_active_torrents`. That is a false
  PASS for **seeder absorption** only if some seeder wedge exists, and F1 says
  idle seeders do not produce one. Actively uploading seeders are not "slow", so
  the exemption would not free their slots, so if they could wedge the predicate
  would PASS that config wrongly, independently of F3. Slice 2 measured it: the seeder false-PASS route was **not observed**
  (uploading seeders yield, U1-off/U1-on, W-off). A different route is real and
  separate: measurement item 3e, a stalled **download** holding
  `max_active_torrents=1` with the exemption on, keeps the next download
  `queuedDL` for the 120 s watched. The predicate does not model that route at
  all, and no differential scenario reproduces it yet.
- **The registry records joint effects (blind spot).** Every Known entry that F2
  or F3 explains also carries F1, so fixing F2 or F3 alone in `trace()` makes no
  entry stale and turns nothing red. Fixing F1 alone does stale the F1-only
  entries (1b-*-slow-off, and 7-*-slow-off if the fix models the step-2 resume).
  "Model fixed, so the stale entry pages" holds only when every finding behind a
  scenario is fixed together. Relatedly, mutating
  rule 1, and the F3-shaped rule-3 change ("the exemption frees downloads but
  not total"), both survive: every scenario that depends on them is Known, so
  the live differential cannot yet tell the right rule from the wrong one (R4
  partial; spec Goal 4 unmet).
- **The nightly is silent when no run fails.** It pages only when a run exists
  and fails. Three cases page nobody: GitHub auto-disables scheduled workflows on
  a public repo after 60 days without repo activity; scheduled runs can be
  dropped or delayed; and a revoked or empty `NTFY_TOKEN` turns the alert step
  red, but nobody hears it.
  **Accepted gap** (Tom, 2026-11-14): no dead-man's switch. If the nightly
  matters later, the fix is a homelab timer that pages when the last successful
  conformance run is over 36 h old.

- **Known limits of edge-triggering:** a config/usage error exits 2 before
  state is read, so it pages every run. There's also no periodic re-page for a
  long-lived problem.

The systemd state-dir quirk recorded here earlier is fixed: the unit uses
`StateDirectory=lintarr-state`, a real 0700 dir holding `state.json`, and the
old `~/.local/state/lintarr` symlink is gone (checked on the box 2026-11-12).

## The dogfooding asset nobody should lose

**homelab#393 records the exact broken qBittorrent values** from the original
incident — the documented-vs-found table. Since the live stack has since been
*repaired*, that issue is now the only surviving record of the known-positive
case.

That makes it the acceptance fixture for `queue-liveness`:

- the **repaired** live stack must report PASS
- the values recorded in homelab#393 must report FAIL

Done: the table is in `tests/fixtures/homelab.py` (wedged and repaired), and
`tests/test_check_cli.py` pins exit 1 / exit 0 against it. On 2026-10-07 the
repaired live stack also PASSed for real.

## P0b: done

Per `docs/superpowers/specs/2026-08-26-lintarr-design.md`, all present:

- the premise combinator (`invariants/combinator.py`)
- `queue-liveness` over the three-limit queue model (`clients/qbittorrent/queue_liveness.py`)
- the state-machine simulator (`tests/model/queue.py`)
- the five-outcome lattice and exit codes (`outcomes.py`)

Next: pick the next spec phase. The design doc's Runtime section (webhook,
HTTP API, SQLite history) and the P3 invariants remain.

**Known gap carried forward:** the simulator validates that the closed form was
*derived* correctly. It cannot catch a *wrong model* — a sweep over the
predicate's own parameters cannot discover a parameter the predicate is
missing. P2's container differential now exists (P2 slice 1, branch
`claude/p2-differential`) and has done exactly that: it found F1-F3, and each
bears on the shipped predicate's assumptions, not only on `trace()`. What it found, and what it still cannot
distinguish, is in Open items above.

## Hard-won lessons from the P0a build

Recorded because they cost real review cycles:

1. **Running the tool against the real stack found what four code reviews
   missed.** Sonarr indexers have no `enable` key; the adapter defaulted it to
   `False` and reported all four enabled indexers as disabled. Reviews read
   code; only live data reveals what an API actually returns.
2. **Seed criteria live on `/api/v3/indexer`, not `/api/v3/downloadclient`.**
   The download client carries no seed fields at all.
3. **Sonarr omits the `value` key entirely** for unset seed criteria — it does
   not send `"value": null`. Anything using `.get("value")` fabricates a null.
4. **Three separate tests passed while proving nothing**, including the one
   carrying the read-only safety claim. Mutation-test every guard: reintroduce
   the defect, watch the test fail, restore.

5. **Probe that an estate-wide guard actually sees the new thing.** The
   homelab deploy relied on generic guards (syslog identifier, OnFailure,
   OnCalendar). Each one was confirmed by breaking the property in lintarr's
   templates and watching it go red. "The suite passes" alone would have
   proved nothing about lintarr.
6. **Read the box after a deploy, not just the recap.** `failed=0` hid the
   systemd state-dir symlink. Only the journal showed it.

## Repo notes

- Remote `origin` is `git@github.com:tclancy/lintarr` (public). Work lands
  through PRs; run `uv run pre-commit install -f` once per clone, **and**
  re-run it in any clone armed before #45 added `default_install_hook_types`
  — until it is re-run, that clone's pre-push battery is still inert. The
  `-f` matters on a machine whose git `init.templateDir` ships a `pre-push`
  hook (#45). A clone without the hook let an E501 reach `main` (#36).
- MIT licensed. Python 3.13, uv, hatchling, src layout, click, httpx, pytest.
- Hypothesis is a declared dev dependency and is now used: `tests/strategies.py`
  holds the fact/snapshot strategies and `tests/test_properties.py` the spec's
  four properties (issue #6). Two of the spec's four are written differently
  from its wording and say why in their docstrings — "never PASS with an
  unknown required fact" is false for the three `arr.*` needs by design, and
  "premises are a subset of declared needs" is not checkable while a premise
  label is a derived name with no published mapping back to a need.
- Reachability controls are not optional in that file: a control that builds
  its own input cannot vouch for a generator's. The first version of
  `test_the_strategies_still_generate_every_shape_they_claim` did exactly that,
  and narrowing `JUNK_VALUES` to booleans left all eighteen property tests
  green. Both halves now read `MALFORMED_SCALARS`.
