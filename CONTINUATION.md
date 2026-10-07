# CONTINUATION — lintarr

**Last updated:** 2026-10-07 (UTC)
**State:** P0b's `queue-liveness` check is live on homelab. It runs hourly as an
edge-triggered systemd timer, and the first run (2026-10-07 21:40 UTC) PASSed
against the real stack. 853 tests, pre-commit clean.

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
    homelab's `unit-failure-alert-user@`, not every hour.
  - Units and env live in homelab `roles/native-apps` (homelab#542).
  - Sonarr/Radarr keys are read from each app's `config.xml` at deploy time.
- **Operate it:** `itguy deploy lintarr`, `itguy logs lintarr --level info`.
  `itguy restart lintarr` exits 1 by design, because nothing long-running
  exists.
- **Alert path proven end to end** (2026-10-07). A drop-in pointed
  SONARR_URL at a dead port. The run ERRORed with exit 2, OnFailure= fired,
  and the ntfy arrived. Reverting returned it to PASS.
- The former blockers are gone:
  - The qBittorrent password is vaulted (homelab#190).
  - The orphan-credential guard is a usage error (#3).
  - The homelab#393 fixture is in `tests/fixtures/homelab.py`.

## Open items

- **systemd 257 state-dir quirk.** `StateDirectory=lintarr` found
  `~/.config/lintarr` and made `~/.local/state/lintarr` a compatibility symlink
  into it, so `state.json` sits beside the env file. It's harmless (0600 in a
  0700 dir), but it isn't what the unit's comments say. Fix it by renaming one
  of the two directories.
- **Known limits of edge-triggering:** a config/usage error exits 2 before
  state is read, so it pages every run. There's also no periodic re-page for a
  long-lived problem.

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
missing. That is P2's container differential test, and until it exists the
flagship check rests on one reading of the qBittorrent docs.

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
  through PRs; run `uv run pre-commit install` once per clone. A clone without
  the hook let an E501 reach `main` (#36).
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
