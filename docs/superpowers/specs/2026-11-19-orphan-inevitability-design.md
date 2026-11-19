# `orphan-inevitability`: design

Status: design, 2026-11-19. Fourth P3 slice, after `hardlink-futility`
(lintarr#57). Narrows the `orphan-inevitability` paragraph in
`2026-08-26-lintarr-design.md` (see "What changed").

## Intent

A finished download stays on disk until something removes it. In an arr
stack, the configuration offers exactly two ways for that to happen:

1. **qBittorrent removes it.** A share limit fires and the action is
   "Remove torrent and its files".
2. **The arr removes it.** The arr's download client has "Remove Completed"
   on. The arr then deletes the torrent and its data once qBittorrent has
   *stopped* the torrent at a seed limit.

When a limit does fire but neither path follows, the files stay forever and
the disk fills. Two cases are common, and neither raises an error anywhere:

- **Remove without files.** qBittorrent drops the torrent and leaves its
  files. Nothing references those files any more, and the arr can no longer
  remove what qBittorrent has already forgotten. These are true orphans.
- **The limit frees nothing.** qBittorrent stops the torrent at the limit, but
  the arr has "Remove Completed" off. Super seeding (and the unpublished
  `Default` action) keeps the torrent running, and the arr never removes a
  torrent that hasn't stopped. Either way the data is kept forever.

The check answers: **for torrents this arr sends to this client, when a seed
limit fires, does any configured setting remove their files?**

## Goals

1. FAIL `remove-keeps-files` when the action for the arr's category is Remove
   (keep files) and a limit can fire for that arr's torrents.
2. FAIL `nothing-reclaims` when a limit can fire for that arr's torrents and
   either the action is Stop and the arr's "Remove Completed" is off, or the
   action is anything other than Stop, Remove or RemoveWithContent.
3. PASS when every arr using the client has a configured path: the action is
   RemoveWithContent, or the action is Stop and "Remove Completed" is on. Also
   PASS when no limit can fire, because unlimited seeding is a deliberate
   choice and `queue-liveness` already owns its consequences.
4. SKIP, never guess, when a needed input is unreadable.

## Non-goals

- **Sizing waste.** Disk use is runtime state, and the admission criterion
  forbids it. So the original design's "link-count aware" size reporting is
  dropped: the check reports no size.
- Cleanuparr, cron jobs and humans. These are reclamation paths outside the
  configuration, so the claim is only about configuration. A stack relying on
  Cleanuparr with a keep-files action FAILs honestly. Suppression (P1) is its
  remedy once it exists.
- **The removal-before-import race.** This is the design spec's cut
  `import-race`.
- Usenet clients.

## Anti-goals

- **A FAIL on Tom's homelab.** It runs RemoveWithContent with ratio 1.5 and 14
  days of seeding time, and must PASS.
- Paging on a stack that seeds forever on purpose.

## What changed from the original design

The original made one claim: "the arr does not remove completed items and
qBittorrent's share-limit action removes the torrent without its content." This
design keeps that as `remove-keeps-files`, with one correction. The arr's
setting is irrelevant there: a removed torrent never sits stopped, so the arr
can't remove it either way.

It adds `nothing-reclaims` for Stop without arr removal and for super seeding.
It also drops sizing (see Non-goals).

## Inputs

Measured live on homelab, 2026-11-19:

| Fact | Source | Homelab |
|---|---|---|
| Arr "Remove Completed" | arr `GET /api/v3/downloadclient` → top-level `removeCompletedDownloads` | `false` (both) |
| Global action | qBittorrent prefs `max_ratio_act` (0 Stop, 1 Remove, 2 EnableSuperSeeding, 3 RemoveWithContent; `-1` Default is observable) | `3` |
| Category action | `GET /api/v2/torrents/categories` → `share_limit_action` (string) | `"Default"` (both) |
| Global limits | `max_ratio_enabled`/`max_ratio`, `max_seeding_time_enabled`/`max_seeding_time`, `max_inactive_seeding_time_enabled` | on: 1.5 / 20160 min / off |
| Category limits | categories → `ratio_limit`, `seeding_time_limit`, `inactive_seeding_time_limit` | `-2` (inherit) |
| Indexer seed criteria | existing `IndexerFacts` | all unset (defer) |

Homelab: Remove Completed is off, but the action is RemoveWithContent, so path
1 covers both arrs. PASS.

## The check

This is a **qBittorrent backend check**, not a shared one. Share-limit
semantics are qBittorrent's, and every predicate below is in qBittorrent
terms. It lives beside `queue-liveness` in `clients/qbittorrent/`.

For client `C` and each arr `A`, the arr download clients that are `C` are
found with exactly `hardlink-futility`'s rule (kind, enabled, category present
in `save_paths`, `""` for blank). That rule is moved to a public helper in
`lintarr.checks` and reused, not copied. For each matching `(A, D)`, with
category `k = D.category`:

1. **Effective action for `k`.** If category `k` exists and its
   `share_limit_action` is not `"Default"`, map `"Stop"` → 0, `"Remove"` → 1,
   `"EnableSuperSeeding"` → 2 and `"RemoveWithContent"` → 3. Otherwise use the
   global `max_ratio_act`. An unknown string or wrong type is unknown. `k = ""`
   (uncategorised) uses the global action. A category whose key is absent
   (older qBittorrent) uses the global action, the same way `_own_share_limit`
   treats absence as information.
2. **Can a limit fire for `A`'s torrents in `k`?** True if *some* enabled
   torrent indexer of `A` grabs torrents for which at least one arm can be
   reached:
   - **ratio arm:** the indexer's `seed_ratio` is a goal; or it defers (unset,
     null or `-2`) and the category's own `ratio_limit` is reachable; or it
     defers and the category inherits (`-2` or absent) and the global ratio is
     reachable;
   - **seeding-time arm:** the same, with `seed_time`, `seeding_time_limit` and
     `max_seeding_time_enabled`;
   - **inactive arm:** the category's own `inactive_seeding_time_limit` is
     reachable, or it inherits and `max_inactive_seeding_time_enabled` is true.
     Sonarr sets no per-torrent inactive limit.

   An indexer criterion that overrides (a negative value other than `-2`,
   `inf`, `nan`) blocks its arm. This reuses `queue-liveness`'s
   `_is_a_seed_goal`, `_overrides_the_share_limit`, `_can_be_reached`,
   `_is_a_torrent_source`, `_no_global_ratio` and `_not_inactive_gate`
   semantics by import. Nothing is re-derived.

   An arr with no enabled torrent indexer has no route: no limit can fire for
   it, and it gives no conflict.
3. **Conflict for `(A, D)`**, given a limit can fire:
   - action 1 → `remove-keeps-files`
   - action 0 with `D.remove_completed` false → `nothing-reclaims`
   - action not in {0, 1, 3} → `nothing-reclaims`
   - action 3, or action 0 with `D.remove_completed` true → no conflict.

   If no limit can fire, there is no conflict, whatever the action. If the
   action is unknown, or (only when it is 0) `remove_completed` is unknown, or
   reachability is unknown, the pair is unknown.

**Aggregation**, one finding per client and conflict, built like
`hardlink-futility`'s:

- `check` returns one `Finding`. It FAILs on the first conflict, in the order
  `remove-keeps-files` then `nothing-reclaims`, if any pair has it. Pairs with
  unknown inputs give SKIP when no pair conflicts.
- Otherwise it PASSes, or is N/A when no arrs were collected.
- With arrs present but none using `C`, it PASSes with the detail
  `hardlink-futility` uses.
- An arr that failed to collect turns PASS or N/A into SKIP through
  `run.py`'s existing `_resolve_failed_arr`, widened to this invariant.

Labels and wording:

- One premise per conflict: `arr.limit_fires_and_files_stay` for
  `remove-keeps-files`, and `arr.limit_fires_and_nothing_reclaims` for
  `nothing-reclaims`.
- Detail lines name each pair, for example
  `sonarr[tv] (category tv): limit can fire; action is Remove, which keeps files`,
  or `… action is Stop and the arr's Remove Completed is off`.
- PASS details say what decided it, for example
  `sonarr[tv]: qBittorrent removes files at the limit`,
  `radarr[movies]: no limit can fire; seeding is unlimited`.

**Therefore** (in the qBittorrent `THEREFORE` table):

- `remove-keeps-files`: "when a seed limit is reached, qBittorrent drops the
  torrent but leaves its files, and nothing else knows they exist. They stay
  until someone deletes them by hand. Set the share-limit action to 'Remove
  torrent and its files', or use Stop and turn on the arr's Remove Completed."
- `nothing-reclaims`: "torrents reach their seed limit and then keep their files
  forever: qBittorrent stops or super-seeds them, and the arr isn't set to
  remove completed downloads. Turn on Remove Completed in the arr's download
  client with the Stop action, or use 'Remove torrent and its files'."

## Model changes

- `ArrDownloadClient` gains `remove_completed: bool | None`, read from the
  top-level `removeCompletedDownloads`. Absent gives `None`; a non-bool is
  `bad-response`.
- No new qBittorrent prefs. Every pref used is already collected.
  `categories` carries `share_limit_action` and the inactive limit.

## Axioms

| Axiom | Status |
|---|---|
| `ShareLimitAction` values and `Default` matching no branch | measured from source (existing, `queue_liveness._RELEASING_ACTIONS`) |
| Category `share_limit_action` strings and the `Default` redirect to the global | assumed from qBittorrent 5.x source (`effectiveShareLimitAction`) |
| The arr removes a torrent (with data) only once qBittorrent reports it stopped at a seed limit | assumed from Sonarr/Radarr source (`QBittorrent.CanBeRemoved`) |
| Remove (1) deletes the torrent and keeps its files | qBittorrent UI semantics; not conformance-tested |

No new conformance test in this slice. The share-limit *action* behaviour can
be measured with the existing container harness later, and it is noted as the
follow-up.

## Deploy impact

None to config. Homelab must PASS, verified live in strict mode before merge.

## Approaches considered

1. **qBittorrent backend check reusing queue-liveness semantics (chosen).**
   Exact and consistent with the flagship check. The cost is coupling to
   qBittorrent, which is honest, because the actions are qBittorrent's.
2. **Shared check over a neutral "reclaims at limit" fact.** Each backend would
   then compute reachability, and the neutral fact would hide the very premises
   the finding must list. Rejected.
3. **The original narrow claim only (`remove-keeps-files`).** Simpler, but
   misses the Stop-without-arr-removal setup, which fills disks just the same.
   Rejected.

## Left to the builder

- Whether the reachability helpers are imported from `queue_liveness` or moved
  into a shared `share_limits.py`. A move must keep `queue-liveness` outputs
  byte-identical, and its tests are the gate.
- Exact SKIP detail wording.
