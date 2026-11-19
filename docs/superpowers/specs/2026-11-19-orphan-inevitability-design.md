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
   no configured setting removes the files. That covers three cases:
   - the action is Stop and the arr won't remove the torrent: "Remove Completed"
     is off, a post-import category hides it, or the limit that fires is one the
     arr doesn't check;
   - the action is anything other than Stop, Remove or RemoveWithContent.
3. PASS when every arr using the client has a configured path:
   - the action is RemoveWithContent; or
   - the action is Stop, the arr removes completed downloads, and every limit
     that can fire is one the arr recognises.

   Also PASS when no limit can fire, because unlimited seeding is a deliberate
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

## How the arr removes a download (verified)

Read from Sonarr `v5-develop` on 2026-11-19. Radarr shares the code shape.

- `QBittorrent.GetItems`: `CanBeRemoved = RemoveCompletedDownloads && state is pausedUP or stoppedUP && HasReachedSeedLimit(torrent, prefs)`.
- `HasReachedSeedLimit` and its seeding-time and inactive siblings read the
  **torrent's own** limit when it is `>= 0`. When the torrent's limit is `-2`,
  they read the **global** preference instead. They never read a category's
  limits. A torrent that qBittorrent stops at a *category* limit still has
  `-2` as its own limit, so the arr checks the same-arm global. At the stop it
  has exactly the category's ratio or seeding time, and a stopped torrent's
  counters stop accruing. So the arr removes it only when the same-arm global
  (`max_ratio_enabled` + `max_ratio`, or `max_seeding_time_enabled` +
  `max_seeding_time`) is reachable and at or below the category's value.
  Otherwise it is never removable by the arr.
- `QBittorrentProxyV2.GetTorrents` lists only torrents in `tvCategory`. After
  import, a post-import category (`tvImportedCategory`, `movieImportedCategory`)
  re-labels the torrent, so the arr can no longer see it or remove it. From then
  on, qBittorrent applies the *imported* category's action and limits.
- Indexer seed criteria become the torrent's own limits when the arr adds it.
  For season packs, `seasonPackSeedTime` sets the seeding-time limit.

## The check

This is a **qBittorrent backend check**, not a shared one. Share-limit
semantics are qBittorrent's, and every predicate below is in qBittorrent
terms. It lives beside `queue-liveness` in `clients/qbittorrent/`.

For client `C` and each arr `A`, the arr download clients that are `C` are
found with exactly `hardlink-futility`'s rule (kind, enabled, category present
in `save_paths`, `""` for blank). That rule is moved to a public helper in
`lintarr.checks` and reused, not copied. For each matching `(A, D)`:

0. **Settled category.** `k` is `D.imported_category` if it is a non-empty
   string different from `D.category`, and `D.category` otherwise.
   `arr_removes` is `D.remove_completed`, except that it is `False` when `k` is
   the imported category: the arr no longer sees the torrent. In the
   imported-category case, the limits that apply before import are ignored. The
   seeding that matters is the seeding after import.
1. **Effective action for `k`.** If category `k` exists and its
   `share_limit_action` is not `"Default"`, map `"Stop"` → 0, `"Remove"` → 1,
   `"EnableSuperSeeding"` → 2 and `"RemoveWithContent"` → 3. Otherwise use the
   global `max_ratio_act`. That covers `k = ""`, a category not in the map, and
   a missing key (older qBittorrent; absence is information, as in
   `_own_share_limit`). An unknown string or a wrong type is unknown.
2. **Decide from the action first.** Reachability is evaluated only when the
   action could conflict:
   - action 3 → **no conflict**, without reading reachability;
   - action unknown → **unknown**;
   - action 1 → conflict `remove-keeps-files` iff `can_fire`;
   - action 0 with `arr_removes` true → conflict `nothing-reclaims` iff
     `can_fire_unrecognised`. With `arr_removes` false → `nothing-reclaims` iff
     `can_fire`. With `arr_removes` unknown → unknown if `can_fire` is not
     False, else no conflict;
   - any other action (2, -1, other) → `nothing-reclaims` iff `can_fire`.

   Here `None` from `can_fire` or `can_fire_unrecognised` makes the pair
   unknown.
3. **`can_fire`:** *some* enabled torrent indexer of `A` grabs torrents for
   which at least one arm in category `k` can be reached. The three arms are:
   - **ratio:** the indexer's `seed_ratio` is a goal. Or it defers (unset, null
     or `-2`) and the category's own `ratio_limit` is reachable. Or it defers,
     the category inherits (`-2`/absent), and the global ratio is reachable.
   - **seed time:** the same with `seed_time` (or, for sonarr, also
     `season_pack_seed_time`), `seeding_time_limit` and
     `max_seeding_time_enabled`.
   - **inactive:** the category's own `inactive_seeding_time_limit` is
     reachable, or it inherits and `max_inactive_seeding_time_enabled` is true.
     The arr sets no per-torrent inactive limit.

   An indexer criterion that overrides (a negative value other than `-2`, `inf`
   or `nan`) blocks its arm. An arr with no enabled torrent indexer can't fire.
   This reuses `queue-liveness`'s helpers by import.
4. **`can_fire_unrecognised`:** like `can_fire`, but it counts only arms the arr
   does **not** recognise. An indexer goal or an inherited global is
   recognised. An arm reached through the category's own `ratio_limit` or
   `seeding_time_limit` (including the season-pack arm) is **unrecognised**
   unless the same-arm global is reachable and its value is at or below the
   category's value; then it is recognised. An unreadable global flag or value
   makes that arm's recognition unknown, which makes `can_fire_unrecognised`
   unknown unless another arm settles it (`can_fire` is unaffected: the arm
   fires either way). An arm reached through the category's own
   `inactive_seeding_time_limit` is always unrecognised (see Known limits).
   Concretely: some enabled torrent indexer has an arm reached through a
   category's own limit that the global does not cover.

**Aggregation** (one finding per client, built like `hardlink-futility`'s):

- FAIL on the first conflict found, `remove-keeps-files` before
  `nothing-reclaims`. SKIP if no pair conflicts and some pair is unknown.
- Otherwise PASS, or N/A when no arrs were collected. With arrs present but none
  using `C`, PASS with the shared `NO_ARR_SENDS_HERE` detail.
- An arr that failed to collect turns PASS or N/A into SKIP through `run.py`.
  The invariant is listed in the qBittorrent backend's new `arr_dependent`
  mapping (see Model changes), not hard-coded in `run.py`.

**Labels and wording:**

- **Premises:** one per conflict, `arr.limit_fires_and_files_stay` and
  `arr.limit_fires_and_nothing_reclaims`.
- **Details** name each pair. For example:
  - `sonarr[tv] (category tv): a limit can fire; action is Remove, which keeps files`
  - `… action is Stop and the arr's Remove Completed is off`
  - `… action is Stop and the limit that fires is category tv's own, which the arr does not check`
  - `… the arr re-labels imports to category tv-done, so it can no longer remove them`
- **PASS details** say what decided it. For example:
  - `sonarr[tv]: qBittorrent removes files at the limit`
  - `sonarr[tv]: qBittorrent stops at the limit and the arr removes completed downloads`
  - `radarr[movies]: no limit can fire; seeding is unlimited`

**Therefore** (qBittorrent `THEREFORE` table):

- `remove-keeps-files`: "when a seed limit is reached, qBittorrent drops the
  torrent but leaves its files, and nothing else knows they exist. They stay
  until someone deletes them by hand. Set the share-limit action to 'Remove
  torrent and its files', or use Stop and turn on the arr's Remove Completed."
- `nothing-reclaims`: "torrents reach their seed limit and then keep their files
  forever. qBittorrent stops or super-seeds them, and the arr either isn't set
  to remove completed downloads, can't see them after a post-import category,
  or doesn't recognise the limit that stopped them. Use 'Remove torrent and its
  files', or the Stop action with Remove Completed on, global or indexer seed
  limits, and no post-import category."

## Known limits

- **Indexer pinning.** An indexer's `downloadClientId` can pin it to another
  client. Every indexer of the arr is counted for every client.
- **Parent categories.** qBittorrent falls back to a parent category's limits.
  This check reads a flat map.
- **Older arr builds** don't recognise qBittorrent 5's `stoppedUP` state, so the
  Stop path never reclaims there. That isn't modelled.
- **Stacks relying on Cleanuparr** with a keep-files action FAIL honestly, until
  suppression (P1) exists.
- **Global inactive value.** The global inactive-seeding-time *value* is not
  collected, so a category's own inactive limit is always unrecognised, even
  when the global inactive limit is armed at or below it. That case is a
  possible false FAIL.
- **Arrs without top-level `removeCompletedDownloads`.** An arr too old to
  report it reads Remove Completed as unknown, so a Stop action with a
  reachable limit SKIPs on every strict run.

## Model changes

- `ArrDownloadClient` gains two fields:
  - `remove_completed: bool | None`, from the top-level `removeCompletedDownloads`. Absent, null or non-bool reads as `None`, never `bad-response`, so a junk value can't ERROR the whole arr.
  - `imported_category: str`, from the `tvImportedCategory` or `movieImportedCategory` field. Absent, null or empty reads as `""`, and a non-string reads as `""` too. The value is only consulted when non-empty, so the default is harmless.
- `Backend` gains `arr_dependent: Mapping[str, str]`, mapping invariant id to the premise used for the failed-arr SKIP. It defaults to empty. `lintarr.checks` gains `SHARED_ARR_DEPENDENT` (hardlink-futility's entry). `run._resolve_failed_arr` reads both instead of naming `hardlink_futility`. Its existing hardlink output stays byte-identical.
- No new qBittorrent prefs.

## Axioms

| Axiom | Status |
|---|---|
| `ShareLimitAction` values; `Default` matches no branch | measured from source (existing `_RELEASING_ACTIONS`) |
| Category `share_limit_action` strings and the `Default` redirect to the global | assumed from qBittorrent 5.x source |
| The arr removes a torrent only when it is stopped and has reached a torrent-level or global limit, never a category limit (a category stop counts only when the same-arm global is at or below it) | read from Sonarr source, 2026-11-19 |
| A post-import category hides the torrent from the arr | read from Sonarr source, 2026-11-19 |
| Remove (1) deletes the torrent and keeps its files | qBittorrent semantics; not conformance-tested |

No new conformance test in this slice. Container-testing the share-limit
actions is the follow-up.

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
