# CONTINUATION — lintarr

**Last updated:** 2026-11-18 (UTC)
**State:** The pluggable-backends seam (download clients behind a registry,
client-neutral `DOWNLOAD_CLIENT_*` / `ARR_*` env names, a fake backend proving it)
is on branch `claude/pluggable-backends`, not yet merged. P2 slice 1 (the nightly qBittorrent 5.2.3 container differential of
`trace()`) is merged to `main` (83e162e) and its nightly alert is **armed**
(2026-11-15). It found F1-F3 (Open items). P0b's `queue-liveness` check is
unchanged and still live on homelab, hourly as an edge-triggered systemd timer
(first PASS against the real stack 2026-10-07 21:40 UTC). P2 slice 2 (uploading seeders against
`max_active_torrents`) is merged to `main`; its Linux validation run
43998520392 was green, 38 passed in 21m17s.

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
  runs 38 items, ~21 min measured locally (worst case with retries ~32 min vs
  the 35 min step timeout). **Watch item:** a `missingFiles` on the first
  seeder add was seen twice in ~60 adds, both after a fresh pair (macOS Docker, cause unconfirmed); seeder data is now
  fsynced before the add and the one retry absorbs a single hit.
  `DECISION NEEDED:` (Tom): the `_WEDGED` pin (`tests/test_check_cli.py`, exit
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
- `queue-liveness` over the three-limit queue model (`invariants/queue_liveness.py`)
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
