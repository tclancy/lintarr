# qBittorrent 5.2.3: what the conformance differential assumes, measured

**Measured 2026-11-13, 00:11 to 00:38 UTC, plus two item-4b replications at
00:51 to 00:55 UTC**, against the pinned conformance image
`lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.14-ls476` (qBittorrent **v5.2.3**,
libtorrent **2.0.14**, the build homelab runs today). Harness: the container
lifecycle of the `qbt` fixture in `tests/conformance/conftest.py`, logging every
state transition of `GET /api/v2/torrents/info` at a 0.25 s poll. Every item is
reproducible with `tools/probe_qbt_conformance.py` (its docstring says how to
run each one, e.g. `uv run python tools/probe_qbt_conformance.py item4b --reps
3`). The first session ran the same code from a throwaway pytest file; the 4b
replications ran from the committed tool. The pinning test is
`tests/conformance/test_assumptions.py`.

This is Task 4 (spec check R10) of P2 slice 1
(`docs/superpowers/specs/2026-11-12-p2-container-differential-design.md`). It
measures four things the scenarios rely on before any scenario is written. Under
the spec's stop rule, a result that contradicts the spec or `trace()` is recorded
and reported here. `trace()`, `states.py` and the spec were not changed.

## TL;DR

- **The headline wedge does not reproduce in the container with idle,
  peerless seeders added complete, then stalled downloads added.** In item 4b
  (`_WEDGED` with `max_active_uploads=-1`), five `stalledUP` seeders fill
  `max_active_torrents=5`. Three downloads added next all start, and three
  seeders are demoted to `queuedUP` to make room. `trace()` predicts
  `queued=3`; the container says `queued=0`, in 3 of 3 runs. Four more runs at
  `max_active_torrents=2` (3b, 3c) show the same displacement. #393 did wedge,
  so this narrows the mechanism that can produce the wedge; it does not refute
  the incident. See "Stop-rule findings" F1 for what was not measured.
- **`max_active_uploads` binds seeders.** With five seeders and
  `max_active_uploads=3`, two sit in `queuedUP` (F2). This is the rule-2
  disagreement the spec predicted.
- **`dont_count_slow_torrents` does not exempt anything from
  `max_active_torrents`** (F3). It exempts slow downloads from
  `max_active_downloads` within about 3 s. But one slow download that holds
  `max_active_torrents=1` keeps a second download in `queuedDL` for the full 120 s
  it was watched. `trace()` rule 3 says the second one starts.
- **Share limits of 0 fire.** `max_ratio=0` and `max_seeding_time=0` each stop a
  zero-upload seeder about 10 s after it is added (items 1 and 1b).
  `max_ratio_act=3` makes it vanish from `torrents/info` and deletes its data
  (item 2). Scenario 6 can use `max_ratio=0` as the spec planned.
- **Settling is event-driven, not 30 s.** Every add settled within 1.3 to 3.2 s.
  No state changed after that in any 30 to 150 s hold. libtorrent's 30 s
  auto-manage tick never showed up. The only slow step is the share-limit action,
  about 10 s after the add. The recommended `settle_s` is **15 s** (see "Settle
  time").

## Method

One container for the session. Before each item: `QbtHarness.reset()` (delete
all torrents and their files), then `setPreferences` with the queue and
share-limit keys back at the image defaults. Then the item's own preferences,
read back from `GET /api/v2/app/preferences` and logged. Torrents come from
`make_torrent()`: 16 KiB, single file, private, announce-less, so no peers ever
arrive. Complete torrents are added with their data on disk and
`skip_checking=true`. Incomplete ones are added without data.

The image's own defaults, read at 00:11:08.997Z:

| key | default |
|---|---|
| `queueing_enabled` | `true` |
| `max_active_downloads` / `_uploads` / `_torrents` | `3` / `3` / `5` |
| `dont_count_slow_torrents` | `false` |
| `slow_torrent_dl_rate_threshold` / `_ul_` | `2` / `2` (KiB/s) |
| `slow_torrent_inactive_timer` | `60` |
| `max_ratio_enabled` / `max_ratio` / `max_ratio_act` | `false` / `-1` / `0` |
| `max_seeding_time_enabled` / `max_seeding_time` | `false` / `-1` |

The queue limits in `_WEDGED` are exactly qBittorrent's defaults (3/3/5, slow
exemption off). That is a side note, not a finding. But it does mean an
untouched qBittorrent install sits in the configuration #393 was diagnosed in.

## Transient states

Every state seen that was not the settled one, in all runs:

| transient | when | lasted | count |
|---|---|---|---|
| absent from `torrents/info` | the poll right after `torrents/add` | ≤0.26 s | 1 of 63 adds (00:32:25.188Z) |
| `checkingResumeData` | every add, complete or not | 0.26 to 1.59 s | every add |
| `queuedUP` on a fresh seeder, then `stalledUP` | 2 seeders, all limits free | 1.32 s (00:15:40.610Z to 00:15:41.928Z) | 1 run |
| `queuedDL` on a fresh download, then `stalledDL` | 3 downloads added to 5 seeders (items 4, 4b) | 1.31 to 1.57 s (e.g. 00:34:22.147Z to 00:34:23.472Z) | 3 of 4 runs (item 4; 4b reps 2 and 3; 4b rep 1 went straight to `stalledDL`) |
| `queuedDL`, then `stalledDL` | slow exemption releasing `max_active_downloads` | 1.57 s (00:24:17.552Z to 00:24:19.130Z) | 1 of 4 runs |

`tally()` counts the first row as **left** and the last two as **queued**. So
Task 5 must not compare on the first poll after an event. It needs the
stable-window rule from the spec.

## Item 1: `max_ratio=0`, action stop

`setPreferences {"max_ratio_enabled": true, "max_ratio": 0, "max_ratio_act": 0}`
(read back the same), then one complete torrent.

| UTC | t (s) | state | ratio | uploaded | seeding_time |
|---|---|---|---|---|---|
| 00:11:09.019 | 0.00 | `checkingResumeData` | 0 | 0 | 0 |
| 00:11:10.591 | 1.57 | `stalledUP` | 0 | 0 | 1 |
| 00:11:19.498 | 10.48 | **`stoppedUP`** | 0 | 0 | 9 |

Hash `27c2587e4b366fac534fcfa6211097bb0d893137`. The row carries
`ratio_limit: -2` (use global) and `max_ratio: 0`. **The assumption holds**: a
ratio limit of 0 fires on a zero-upload torrent. It fires 8.9 s after
`stalledUP`. The state is `stoppedUP` (5.x naming), not `pausedUP`.

**1b, the fallback, measured too.** `max_seeding_time_enabled=true,
max_seeding_time=0, max_ratio_act=0`. Hash `7b64cb58bc3c…`: `stalledUP` at
1.58 s (00:12:40.651Z), `stoppedUP` at 10.45 s (00:12:49.518Z), so 8.9 s
after `stalledUP`. Either limit works for scenario 6.

## Item 2: `max_ratio=0`, action 3 (remove with files)

| UTC | t (s) | state |
|---|---|---|
| 00:14:09.320 | 0.00 | `checkingResumeData` |
| 00:14:10.632 | 1.31 | `stalledUP` |
| 00:14:18.978 | 9.66 | **absent from `torrents/info`** |

Hash `f0b5ca31369ad07a7f85e71c037307d35e37a851`. The data file on the mounted
volume was gone afterwards (`exists() == False` at 00:15:39.406Z). **The
spec's "a harness-added torrent that is gone counts as left" is the right
mapping for action 3.**

## Item 3: the slow-torrent timer and the end-to-end start delay

**3a. The API sets no minimum.** `setPreferences` answered 200 with an empty body
for every value tried, and the read-back equals what was sent: `-5, -1, 0, 1, 2,
5, 60` (00:15:39.442Z to .531Z). So "the minimum accepted" is not a floor the
API enforces. The useful question is what works behaviourally. Both `0` and `1`
let the exemption act within one settle (3d, 3f). Task 5 should use `1`. A
negative value is accepted over the API, but what it means to libtorrent was not
measured.

**3b. `max_active_torrents` full of seeders: the incomplete never queues.**
`max_active_torrents=2`, downloads and uploads `-1`,
`dont_count_slow_torrents=true`, timer `1`. Two complete torrents reach
`stalledUP`, then one incomplete torrent is added. Three repetitions, all the
same:

| rep | incomplete reached `stalledDL` | seeder demoted to `queuedUP` | held |
|---|---|---|---|
| 1 | 1.57 s (00:15:43.506Z) | `cb0d8622abb2…` at 00:15:43.507Z | 150 s |
| 2 | 1.30 s (00:18:14.940Z) | `24023088aa65…` at 00:18:14.940Z | not recorded (run stopped to redesign; rep 3 replaces it) |
| 3 | 1.31 s (00:19:11.446Z) | `ca667a7a8aa2…` at 00:19:11.447Z | 30 s |

Raw final rows for rep 3 (00:19:11.44Z): `b2841211d833… item3b-r3-seed0
stalledUP priority 0 progress 1`, `ca667a7a8aa2… item3b-r3-seed1 queuedUP
priority 0 progress 1`, `26cb860b3bf7… item3b-r3-inc stalledDL priority 1
progress 0`.

**3c, control with the exemption off** (00:19:40Z): the same thing. The
incomplete reached `stalledDL` at 1.32 s and seeder `59f26fe3ac22…` went to
`queuedUP`, held for 30 s. So the displacement does not depend on
`dont_count_slow_torrents`.

That means the brief's question ("time until it leaves `queuedDL`") has no
answer for this population. The download never enters `queuedDL`. This is
finding F1. To measure a real release, item 3 also ran populations where a
**download** holds the only slot:

**3d. `max_active_downloads=1` held by one slow download, exemption on, timer
1.** A second incomplete torrent is added as soon as the first reaches
`stalledDL`:

| rep | first `queuedDL` | left `queuedDL` (to `stalledDL`) | UTC of start |
|---|---|---|---|
| 1 | never | 1.32 s | 00:20:14.444Z |
| 2 | never | 1.32 s | 00:22:15.927Z |
| 3 | 1.57 s | **3.15 s** | 00:24:19.130Z |

**3f, timer 0, one rep**: never queued, `stalledDL` at 1.56 s (00:30:26.665Z).

**3g, control, exemption off**: `queuedDL` at 1.58 s (00:32:28.076Z), and still
`queuedDL` 75 s later (00:33:41.690Z). So the download limit is real, and the
exemption is what frees it.

**3e. `max_active_torrents=1` held by one slow download, exemption on, timer
1.** The second download **never starts**:

| rep | `queuedDL` from | still `queuedDL` at (held 120 s) | hashes (a holds the slot, b queued) |
|---|---|---|---|
| 1 | 00:24:22.054Z | 00:26:20.617Z | a `b5d2805477ad…` `stalledDL` p1, b `cb4838e16091…` `queuedDL` p2 |
| 2 | 00:26:23.537Z | 00:28:22.103Z | a `42e6e228d857…` `stalledDL` p1, b `26f556277b42…` `queuedDL` p2 |
| 3 | 00:28:25.024Z | 00:30:23.5Z | a `17f2d7c721a4…` `stalledDL` p1, b `f8da5ef26ff3…` `queuedDL` p2 |

This is finding F3.

### Settle time

Across every add in every item, the end-to-end time from `torrents/add` to the
state that then held for the rest of the watch was **1.30 to 3.15 s**. The 3.15 s
case is the one where the exemption released a queued download (3d rep 3). No
torrent changed state after that in any hold of 30, 75, 90, 120 or 150 s.
Several of those holds span multiple 30 s periods, so a periodic auto-manage
tick changed nothing. The releases seen were triggered by the event itself, not
by the tick. The one slower step is the share-limit action: **9.7 to 10.5 s after
the add** (items 1, 1b, 2).

**Recommended `settle_s` for Task 5: 15 s.** That is the share-limit action at
10.5 s plus margin, and about 5× the slowest queue settle. Use it as a minimum
wait after each event, then require two equal polls. Keep a hard timeout of
about 45 s so one unmeasured 30 s auto-manage tick still fits. The spec's
"stable after the slow timer has elapsed" rule is satisfied with timer `1`.

## Item 4: the `_WEDGED` scenario (5 complete, then 3 incomplete)

Preferences: `_WEDGED` from `tests/fixtures/homelab.py` with `categories`
dropped (it is not a preference key) and `slow_torrent_inactive_timer=1` added
per the spec. Read back at 00:33:41.7Z: `queueing_enabled true`, downloads/
uploads/torrents `3/3/5`, `dont_count_slow_torrents false`, `max_ratio_enabled
false` (`max_ratio` read back `-1`), `max_seeding_time_enabled false`
(`max_seeding_time` `-1`), `max_ratio_act 3`, `max_inactive_seeding_time_enabled
false`. Note that sending `max_ratio: 1.5` with the flag off reads back `-1`,
which is what `homelab.py`'s docstring says about `appcontroller.cpp`.

`trace()`'s prediction comes from `QueueConfig(queueing_enabled=True,
max_active_downloads=3, max_active_uploads=3, max_active_torrents=5,
dont_count_slow_torrents=False, share_limit_enabled=False)` with events
`[AddComplete(5), AddIncomplete(3)]`. 4b is the same with uploads `-1`, and the
prediction is identical because rule 2 ignores the upload limit.

Counts are `(downloading, queued, seeding, seeding_queued, left)`:

| scenario | step | predicted by `trace()` | observed | agree? |
|---|---|---|---|---|
| 4 (ul=3) | after `AddComplete(5)` | `(0, 0, 5, 0, 0)` | `(0, 0, 3, 2, 0)` | **no** |
| 4 (ul=3) | after `AddIncomplete(3)` | `(0, 3, 5, 0, 0)` | `(3, 0, 2, 3, 0)` | **no** |
| 4b (ul=-1) | after `AddComplete(5)` | `(0, 0, 5, 0, 0)` | `(0, 0, 5, 0, 0)` | yes |
| 4b (ul=-1) | after `AddIncomplete(3)` | `(0, 3, 5, 0, 0)` | `(3, 0, 2, 3, 0)` | **no** |

Raw `torrents/info` rows (`hash` prefix, `name`, `state`, `progress`,
`priority`; every row had `force_start false`, `num_seeds 0`, speeds 0):

Item 4, step 1, 00:34:21.873Z:

| hash | name | state | progress | priority |
|---|---|---|---|---|
| `4c5bbf409fb5` | item4-seed0 | `queuedUP` | 1 | 0 |
| `50299cd57e2a` | item4-seed1 | `stalledUP` | 1 | 0 |
| `c3f5bb827c54` | item4-seed2 | `queuedUP` | 1 | 0 |
| `8bc390a23a27` | item4-seed3 | `stalledUP` | 1 | 0 |
| `2d0f290a05f6` | item4-seed4 | `stalledUP` | 1 | 0 |

Item 4, step 2, 00:35:51.989Z (90 s after the adds; final since 00:34:23.47Z):

| hash | name | state | progress | priority |
|---|---|---|---|---|
| `37d1c7f3058b` | item4-inc0 | `stalledDL` | 0 | 1 |
| `917ab3f32ead` | item4-inc1 | `stalledDL` | 0 | 2 |
| `92bdc2e54f0e` | item4-inc2 | `stalledDL` | 0 | 3 |
| `4c5bbf409fb5` | item4-seed0 | `queuedUP` | 1 | 0 |
| `50299cd57e2a` | item4-seed1 | `queuedUP` | 1 | 0 |
| `c3f5bb827c54` | item4-seed2 | `queuedUP` | 1 | 0 |
| `8bc390a23a27` | item4-seed3 | `stalledUP` | 1 | 0 |
| `2d0f290a05f6` | item4-seed4 | `stalledUP` | 1 | 0 |

The incompletes went `checkingResumeData`, then `queuedDL` at 00:34:22.147Z,
then `stalledDL` at 00:34:23.472Z. At that same poll seed1 dropped to
`queuedUP`.

Item 4b, step 1, 00:36:32.245Z: all five seeds `stalledUP` (`7eefccc5dd4c`,
`6e1fccd68a53`, `b1821dda6017`, `865192f3c4fa`, `e7f5e32558d8`). Step 2,
00:38:02.341Z: `876a9bf356ff`, `468067f58b4e`, `924c79927fc1` `stalledDL`
(priority 1 to 3). `7eefccc5dd4c`, `6e1fccd68a53`, `b1821dda6017` `queuedUP`.
`865192f3c4fa` and `e7f5e32558d8` `stalledUP`. All three downloads were
`stalledDL` by 00:36:32.524Z, 0.27 s after being added.

**Item 4b, replicated** with `tools/probe_qbt_conformance.py item4b --reps 2`
(00:51:02Z and 00:53:12Z). Both runs: step 1 `(0, 0, 5, 0, 0)`, step 2
`(3, 0, 2, 3, 0)`. The downloads went `queuedDL` 0.27 s after the add, then
`stalledDL` at 1.58 s (rep 2) and 1.84 s (rep 3), and three seeders went
`queuedUP` on the same poll. Raw step-2 rows of rep 2, 00:53:12.938Z, as
returned (fields cut to those that matter):

```json
{"hash": "5c56dd09212bfe7b1ef0754fce732cfcd27604ea", "name": "probe4b-r1-inc0", "state": "stalledDL", "num_seeds": 0, "priority": 1, "dlspeed": 0, "upspeed": 0}
{"hash": "3fe279c63392c5c9a03a2a027557b319e8370e71", "name": "probe4b-r1-inc1", "state": "stalledDL", "num_seeds": 0, "priority": 2, "dlspeed": 0, "upspeed": 0}
{"hash": "e5e3a377060442cf0dae58f1a874e9cc73125d05", "name": "probe4b-r1-inc2", "state": "stalledDL", "num_seeds": 0, "priority": 3, "dlspeed": 0, "upspeed": 0}
{"hash": "3a34f408cb7fca15f444d07d902af318ad73f91a", "name": "probe4b-r1-seed0", "state": "queuedUP", "num_seeds": 0, "priority": 0, "dlspeed": 0, "upspeed": 0}
{"hash": "ee29b60048e6a240e1ae42a6b8f77b8a11a23422", "name": "probe4b-r1-seed1", "state": "queuedUP", "num_seeds": 0, "priority": 0, "dlspeed": 0, "upspeed": 0}
{"hash": "35a3afed312b1f68a073658cc68540afe035d65c", "name": "probe4b-r1-seed2", "state": "queuedUP", "num_seeds": 0, "priority": 0, "dlspeed": 0, "upspeed": 0}
{"hash": "9d6bdadbfab7dc05bbee1e8953d96eecbfa3dd0d", "name": "probe4b-r1-seed3", "state": "stalledUP", "num_seeds": 0, "priority": 0, "dlspeed": 0, "upspeed": 0}
{"hash": "28daa5bde5aab6250c5bfa9349a69482b2f3c59a", "name": "probe4b-r1-seed4", "state": "stalledUP", "num_seeds": 0, "priority": 0, "dlspeed": 0, "upspeed": 0}
```

(The tool numbers its own reps from 1, so the `-r1-` names are this doc's rep
2.) Rep 3 is the same shape with hashes `972c908c304b…`, `8d011887a0f7…`,
`3a4d6ff13422…` `stalledDL` (priority 1 to 3); `14ccd814054c…`,
`5b7628cd0dc4…`, `4e6f8d19952b…` `queuedUP`; `8a9bef809971…`, `6992b66b43b3…`
`stalledUP`.

| 4b rep | UTC | step 1 | step 2 |
|---|---|---|---|
| 1 | 00:35:52Z | `(0, 0, 5, 0, 0)` | `(3, 0, 2, 3, 0)` |
| 2 | 00:51:02Z | `(0, 0, 5, 0, 0)` | `(3, 0, 2, 3, 0)` |
| 3 | 00:53:12Z | `(0, 0, 5, 0, 0)` | `(3, 0, 2, 3, 0)` |

**What item 4 and 4b each show.** Only 4b is a clean test of a full torrent
limit. In 4b all five seeders were active, so `max_active_torrents=5` was full
when the downloads arrived, and the downloads still started. In item 4 (`ul=3`)
only three seeders were active at step 1, because `max_active_uploads` had
queued two (F2). The torrent limit therefore had two free slots when the
downloads arrived. Three downloads in two free slots still demoted one more
seeder, but item 4 does not show seeders being displaced from a *full* limit.
In every run the active total after step 2 is exactly 5 = `max_active_torrents`,
and 3 = `max_active_downloads` of them are the downloads. **The torrent limit is
enforced, but for these idle seeders, downloads take priority over them for
its slots.**

## Stop-rule findings

None of these changes `trace()`, `states.py` or the spec. Each is for Tom.

**F1. In this container, idle peerless seeders added complete do not hold a
full `max_active_torrents` against stalled downloads added after them.** The
downloads start and seeders are demoted to `queuedUP`. Evidence: item 4b at the
`_WEDGED` shape, 3 of 3 runs (`max_active_torrents=5` full of five `stalledUP`
seeders), and items 3b (3 runs) and 3c (1 run) at `max_active_torrents=2`, with
the slow exemption on and off. Item 4 (`ul=3`) shows a seeder being demoted too,
but its torrent limit was not full when the downloads arrived (see Item 4), so
it is not counted here.

What it contradicts:
- `trace()` rule 1 ("every seeding torrent occupies a `max_active_torrents`
  slot"), insofar as the rule is used to block a queued start.
- The predicate's absorbing-state claim: "seeders never release slots"
  (`queue_liveness.py:9`) and "once five torrents completed they held every slot
  permanently" (`queue_liveness.py:3-5`).

What it does not contradict: that seeders *count* against `max_active_torrents`
(`_max_active_torrents_binds`, `queue_liveness.py:497-499`: "Seeding torrents
count against it"). That is confirmed. Seeders do occupy the slots until a
download needs them, which is why the active total is always exactly 5 (or 2).
The predicate already flags the gap: "libtorrent's allocation order may bound
how many slots seeders actually reach" (`queue_liveness.py:83`).

**Reconciling with #393.** #393 did wedge: 52 torrents, no active downloads, for
weeks. So F1 does not refute the incident. It narrows the mechanism. "Seeders
fill `max_active_torrents`" is not enough on its own, at least for idle seeders
on this build. Something F1 did not reproduce must also hold.

Not measured, and each could be that something:
- Torrents that *finish* downloading in place (the #393 path) rather than being
  added complete. The container cannot finish a download because no peers exist.
- **Actively uploading** seeders. Every seeder here had `num_seeds 0`,
  `upspeed 0`, so libtorrent may rank it differently from a seeder that is
  serving peers.
- **Population size.** #393 had 52 torrents; these runs had at most 8.
- **Whether #393 ran on this build.** The pin is what homelab runs today. Which
  qBittorrent and libtorrent #393 ran on was not recorded here.
- The mechanism itself. libtorrent ranking downloading torrents ahead of seeding
  ones when it hands out `active_limit` slots would explain it. That has not
  been checked against libtorrent source.

**F2. `max_active_uploads` binds seeders** (item 4 step 1: 3 `stalledUP`, 2
`queuedUP` under `max_active_uploads=3`). This contradicts `trace()` rule 2 (the
upload limit is not consulted, `seeding_queued` is always 0). The spec
anticipated it, and 4b confirms the upload limit is the cause.

**F3. `dont_count_slow_torrents` does not exempt slow torrents from
`max_active_torrents`** (item 3e, 3 of 3 runs, and implied by 3b, where a seeder
was demoted even with the exemption on). It does exempt slow downloads from
`max_active_downloads` (3d, 3f). This contradicts `trace()` rule 3 ("slow
torrents count against no limit, seeders included"). It also bears on the
predicate's `qbt.slow_exempt_off` premise (`_slot_premises`), which treats the
exemption as rescuing `max_active_torrents`. What 3e shows is a client that is
wedged by stalled **downloads**: `max_active_torrents=1`, exemption on, one dead
download, and everything behind it queued for the 120 s observed.

Raw rows from the 3e runs. Each row is as of that torrent's last state change;
the final read at the end of each 120 s hold showed the same states:

```json
{"utc": "2026-11-13T00:24:20.486Z", "hash": "b5d2805477adb27d3a8e41a014a81c1a4e12c028", "name": "item3e-r1-a", "state": "stalledDL", "num_seeds": 0, "priority": 1, "dlspeed": 0, "upspeed": 0}
{"utc": "2026-11-13T00:24:22.054Z", "hash": "cb4838e160917dc4e8b7e85ccd4bfcc339eebbcb", "name": "item3e-r1-b", "state": "queuedDL", "num_seeds": 0, "priority": 2, "dlspeed": 0, "upspeed": 0}
{"utc": "2026-11-13T00:26:21.960Z", "hash": "42e6e228d857d98895ae8c5bccca00bdc21a5de0", "name": "item3e-r2-a", "state": "stalledDL", "num_seeds": 0, "priority": 1, "dlspeed": 0, "upspeed": 0}
{"utc": "2026-11-13T00:26:23.537Z", "hash": "26f556277b42bc9317c2ad47e88aadd4bcd98df7", "name": "item3e-r2-b", "state": "queuedDL", "num_seeds": 0, "priority": 2, "dlspeed": 0, "upspeed": 0}
{"utc": "2026-11-13T00:28:23.459Z", "hash": "17f2d7c721a4007e372c9c0787b0a719e8cc86aa", "name": "item3e-r3-a", "state": "stalledDL", "num_seeds": 0, "priority": 1, "dlspeed": 0, "upspeed": 0}
{"utc": "2026-11-13T00:28:25.024Z", "hash": "f8da5ef26ff35f7581e84498c763d5ec63454e65", "name": "item3e-r3-b", "state": "queuedDL", "num_seeds": 0, "priority": 2, "dlspeed": 0, "upspeed": 0}
```

**Consequence for Task 5.** Scenarios 1 and 1b, as built from the population
measured here, will disagree on night one. So
will every scenario whose prediction relies on rules 1, 2 or 3 with seeders
present or `max_active_torrents` binding. The brief says a disagreement here is
expected to become a known disagreement in Task 5. That registry was not
created in this task.

## What `test_assumptions.py` pins

Eleven `container` tests, 11 passed in 189 s with the 35 s holds (2026-11-13). The mutation round
went red as intended: flipping the exemption off in the download-limit test, and
setting `max_ratio_act` from 3 to 0 in the remove test, each failed. Both were
restored.

- `max_ratio=0` with stop → `stoppedUP` within 30 s (item 1).
- `max_seeding_time=0` with stop → `stoppedUP` within 30 s (item 1b).
- `max_ratio=0` with action 3 → gone from `torrents/info` and data deleted
  (item 2).
- `slow_torrent_inactive_timer` reads back `-5`, `0` and `1` unchanged (3a).
- With the exemption on, the download limit releases a second download to
  `stalledDL` within 10 s (3d). With it off, the second download is held
  `queuedDL` (3g).
- With the exemption on, a full `max_active_torrents=1` holds the second
  download `queuedDL` (3e, F3).
- With `max_active_torrents=2` full of seeders, an added download reaches
  `stalledDL` and exactly one seeder goes `queuedUP`, exemption on and off (3b,
  3c, F1).

Every "held" state is asserted on every poll for 35 s, not only at the end. That
is longer than libtorrent's 30 s auto-manage interval, so a release driven by
that tick would still turn the pin red.

Item 4 is deliberately not pinned here. It is Task 5's scenario 1, and pinning
it here would be a known-disagreement registry by another name.
