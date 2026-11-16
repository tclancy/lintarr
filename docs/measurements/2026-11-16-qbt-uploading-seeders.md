# qBittorrent 5.2.3: do uploading seeders hold `max_active_torrents`?

**Measured 2026-11-16, 22:43:56 to 23:07:12 UTC** (23 min 16 s wall time, 12
runs), against the pinned conformance image
`lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.14-ls476` (qBittorrent **v5.2.3**
on both sides, as the WebUI reported it). Harness: a seeder/leecher pair from
`qbt_pair()` in `tests/conformance/containers.py`, driven by `observe` and
judged by `judge` in `tests/conformance/uploading.py`. Reproduce with:

    uv run python tools/probe_qbt_uploading.py --reps 3 > uploading.jsonl

The raw JSON lines (every bring-up and window poll, the preferences read back
and one result line per run) were written to a session scratchpad, outside the
repo. The excerpts below are copied from them.

This is P2 slice 2
(`docs/superpowers/specs/2026-11-15-uploading-seeders-measurement-design.md`).
It follows up F1 and F3 of `2026-11-13-qbt-conformance-assumptions.md`. That
doc measured **idle** seeders only. This one measures seeders that are
**actively uploading** to a peer. It changes nothing in `src/`, `simulate()`,
`trace()` or the `_WEDGED` pin. What the results mean for those is written
below as findings. The decisions are Tom's.

## TL;DR

- **Uploading seeders do not hold `max_active_torrents` against a new
  download.** In U1 (`-1/-1/3`), three seeders each uploading to the leecher
  filled all 3 slots. A peerless download added next started (`stalledDL`)
  within 2 s, and one of the **uploading** seeders was demoted to `queuedUP`.
  The other two kept uploading for the full 120 s. Counts
  `(0,0,3,0,0) → (1,0,2,1,0)`, verdict **yield**, 3 of 3 runs with the slow
  exemption off (U1-off) and 3 of 3 with it on (U1-on). The exemption made no
  difference.
- **`_WEDGED`'s own limits do not wedge with uploading seeders either.** In
  W-off (`3/3/5`, exemption off, 5 seeders uploading), `max_active_uploads=3`
  kept 2 of the 5 seeders `queuedUP` before anything else happened (F2 again).
  So 3 were active against a torrent limit of 5. All 3 downloads started, and a
  third seeder, one that was uploading, was demoted. Counts
  `(0,0,3,2,0) → (3,0,2,3,0)`, verdict **yield**, 3 of 3 runs. That is the same
  result as item 4 with idle seeders.
- **U0-off, the idle control, is unresolved.** Run 1 was a harness error (one
  seeder reported `missingFiles` when it was added). In runs 2 and 3, bring-up
  passed while all three idle seeders were still in the transient `queuedUP`
  state that follows a fresh add. So the download arrived while **no** seeder
  was active, and `before` reads `(0,0,0,3,0)`. Both runs agree on
  `(0,0,0,3,0) → (1,0,2,1,0)`, verdict **other**, but with 1 of 3 runs an error
  the agreement rule is not met. And the `before` is a readiness artifact, not
  the control the spec designed. See "U0-off".
- **Everything settles within about 2 s of the add, and nothing changes
  afterwards.** In all 11 completed runs the counts reached their final value
  by **t = 2.02 s** and stayed there to t = 120 s. Under the budget rule, a 60 s
  window would be justified if the nightly needs one.

## Method

One pair for the whole session (seeder `192.168.147.2` on a private docker
network, leecher on the same network). For each run, `observe(pair, sc, rep=n)`:

1. `reset()` both sides, then `apply_prefs`: the seeder gets
   `prefs_for(sc.cfg, slow_timer_s=1) | PEER_PREFS`, the leecher
   `prefs_for(LEECHER_CFG, slow_timer_s=1) | PEER_PREFS`, both checked by
   `set_prefs_verified`.
2. Add `n_seeders` complete torrents named `<id>-r<n>-c<i>` to the seeder
   (32 MiB, 256 KiB pieces, private, announce-less, data on disk,
   `skip_checking`). `add_seeders` waits up to 15 s for every one to be
   `stalledUP` or `queuedUP`. Five 32 MiB adds fitted inside that every time
   (W-off), so the 15 s was not raised.
3. If the scenario has a leecher: the same torrents are added incomplete on the
   leecher, capped at 65536 B/s each, and pointed at `seeder:6881` with
   `addPeers`.
4. Bring-up: poll the seeder's `torrents/info` every 1 s for up to 60 s,
   re-issuing `addPeers` while not ready, until readiness holds on 2 consecutive
   polls. Readiness with a leecher (`ready`): every seeder is `queuedUP` or
   `uploading` above 2048 B/s, and at least one is uploading. Without one
   (`idle_ready`, U0): every seeder is `stalledUP` or `queuedUP`.
5. The event: add `n_downloads` incomplete 16 KiB torrents (`<id>-r<n>-i<i>`)
   to the **seeder only**. They have no peer.
6. Poll the seeder every 1 s for 120 s (121 polls, t = 0 to 120 s).

`judge` then takes `before` from the last bring-up poll (seeders only),
re-checks over every window poll that each seeder never demoted to `queuedUP`
kept uploading above 2048 B/s (A1, U1 and W only), and takes `after` as the
one tally shared by every poll in the final 30 s. The verdict is **yield** if
a seeder was demoted or more downloads started than there were free slots,
**hold** if no seeder was demoted and the rest of the downloads queued, and
**other** otherwise.

Counts are `(downloading, queued, seeding, seeding_queued, left)`.

### Settings read back

Read back from `GET /api/v2/app/preferences` after each run. They were the same
in every run of a scenario.

| key | seeder U0/U1-off | seeder U1-on | seeder W-off | leecher (all runs) |
|---|---|---|---|---|
| `queueing_enabled` | `true` | `true` | `true` | `false` |
| `max_active_downloads` / `_uploads` / `_torrents` | `-1` / `-1` / `3` | `-1` / `-1` / `3` | `3` / `3` / `5` | `-1` / `-1` / `-1` |
| `dont_count_slow_torrents` | `false` | `true` | `false` | `false` |
| `slow_torrent_inactive_timer` | `1` | `1` | `1` | `1` |
| `slow_torrent_dl_rate_threshold` / `_ul_` | `2` / `2` (KiB/s) | same | same | same |
| `max_ratio_enabled` / `max_ratio` / `max_ratio_act` | `false` / `-1` / `0` | same | same | same |
| `max_seeding_time_enabled` / `max_inactive_seeding_time_enabled` | `false` / `false` | same | same | same |
| `listen_port` / `random_port` | `6881` / `false` | same | same | same |
| `dht` / `pex` / `lsd` / `upnp` | all `false` | same | same | same |
| `limit_lan_peers` | `true` | same | same | same |

## Results per scenario

`bring-up` is the bring-up time to the poll that passed readiness. `settled at`
is the window time of the first poll from which the counts equalled the final
counts through t = 120 s. Polls are 1 s apart, so the counts changed somewhere
in the second before it.

### U1-off: `-1/-1/3`, exemption off, 3 uploading seeders, then 1 download

| rep | run start (UTC) | bring-up | active at bring-up | before | after | verdict | settled at |
|---|---|---|---|---|---|---|---|
| 1 | 22:48:22Z | 4.01 s | 3 | `(0,0,3,0,0)` | `(1,0,2,1,0)` | yield | 2.01 s |
| 2 | 22:50:27Z | 6.01 s | 3 | `(0,0,3,0,0)` | `(1,0,2,1,0)` | yield | 2.01 s |
| 3 | 22:52:33Z | 6.01 s | 3 | `(0,0,3,0,0)` | `(1,0,2,1,0)` | yield | 2.01 s |

**Agreed, 3 of 3.** A2 holds: 3 uploading seeders active = `max_active_torrents`
when the download was added.

Rep 1, the bring-up poll that passed readiness (t = 4.01 s):

| hash | name | state | upspeed | num_leechs |
|---|---|---|---|---|
| `31ef608d69c9` | U1-off-r1-c0 | `uploading` | 13003 | 1 |
| `60089e563db3` | U1-off-r1-c1 | `uploading` | 13003 | 1 |
| `067a55944ae4` | U1-off-r1-c2 | `uploading` | 13003 | 1 |

Window: at t = 0.01 s the download `15bc0f876c13` was `checkingResumeData`, at
1.01 s `queuedDL` with all three seeders still `uploading`, and at 2.01 s
`stalledDL` with c2 `queuedUP` (`num_leechs` 0). The final poll (t = 120.02 s):

| hash | name | state | upspeed | num_leechs |
|---|---|---|---|---|
| `31ef608d69c9` | U1-off-r1-c0 | `uploading` | 70853 | 1 |
| `60089e563db3` | U1-off-r1-c1 | `uploading` | 70853 | 1 |
| `067a55944ae4` | U1-off-r1-c2 | `queuedUP` | 0 | 0 |
| `15bc0f876c13` | U1-off-r1-i0 | `stalledDL` | 0 | 0 |

### U1-on: `-1/-1/3`, exemption on, otherwise as U1-off

| rep | run start (UTC) | bring-up | active at bring-up | before | after | verdict | settled at |
|---|---|---|---|---|---|---|---|
| 1 | 22:54:41Z | 4.02 s | 3 | `(0,0,3,0,0)` | `(1,0,2,1,0)` | yield | 2.01 s |
| 2 | 22:56:46Z | 4.01 s | 3 | `(0,0,3,0,0)` | `(1,0,2,1,0)` | yield | 2.02 s |
| 3 | 22:58:51Z | 4.01 s | 3 | `(0,0,3,0,0)` | `(1,0,2,1,0)` | yield | 1.02 s |

**Agreed, 3 of 3**, and identical to U1-off.

Rep 1, the bring-up poll that passed readiness (t = 4.02 s):

| hash | name | state | upspeed | num_leechs |
|---|---|---|---|---|
| `6cde66545cf9` | U1-on-r1-c0 | `uploading` | 12862 | 1 |
| `06e7d7b1b948` | U1-on-r1-c1 | `uploading` | 12862 | 1 |
| `2410622c1aff` | U1-on-r1-c2 | `uploading` | 12862 | 1 |

Window: the download `27f965f276e3` was `queuedDL` at 1.01 s and `stalledDL` at
2.01 s, the same poll at which c2 went `queuedUP`. The final poll (t = 120.01 s):

| hash | name | state | upspeed | num_leechs |
|---|---|---|---|---|
| `6cde66545cf9` | U1-on-r1-c0 | `uploading` | 58641 | 1 |
| `06e7d7b1b948` | U1-on-r1-c1 | `uploading` | 65590 | 1 |
| `2410622c1aff` | U1-on-r1-c2 | `queuedUP` | 0 | 0 |
| `27f965f276e3` | U1-on-r1-i0 | `stalledDL` | 0 | 0 |

### W-off: `3/3/5` (`_WEDGED`'s limits), exemption off, 5 uploading seeders, then 3 downloads

| rep | run start (UTC) | bring-up | active at bring-up | before | after | verdict | settled at |
|---|---|---|---|---|---|---|---|
| 1 | 23:00:55Z | 6.01 s | 3 | `(0,0,3,2,0)` | `(3,0,2,3,0)` | yield | 2.01 s |
| 2 | 23:03:02Z | 4.01 s | 3 | `(0,0,3,2,0)` | `(3,0,2,3,0)` | yield | 2.01 s |
| 3 | 23:05:07Z | 4.02 s | 3 | `(0,0,3,2,0)` | `(3,0,2,3,0)` | yield | 2.01 s |

**Agreed, 3 of 3.** A2 does **not** hold here, and cannot: the upload limit of
3 queued 2 of the 5 seeders before the event (F2), so 3 of the 5 torrent slots
were taken and 2 were free when the downloads arrived. That is the spec's
expectation for W-off. Its result is the two counts, not a full-limit test.

Rep 1, the bring-up poll that passed readiness (t = 6.01 s):

| hash | name | state | upspeed | num_leechs |
|---|---|---|---|---|
| `b8703137fb59` | W-off-r1-c0 | `queuedUP` | 0 | 0 |
| `002388f567b2` | W-off-r1-c1 | `uploading` | 70473 | 1 |
| `cf2f3504effd` | W-off-r1-c2 | `queuedUP` | 0 | 0 |
| `ad5d3fe7130d` | W-off-r1-c3 | `uploading` | 70473 | 1 |
| `0c28dcfef6ef` | W-off-r1-c4 | `uploading` | 70473 | 1 |

Window: the three downloads were `checkingResumeData` at 0.02 and 1.01 s, and
all three `stalledDL` at 2.01 s, the same poll at which c1, which had been
uploading, went `queuedUP`. The final poll (t = 120.02 s):

| hash | name | state | upspeed | num_leechs |
|---|---|---|---|---|
| `b8703137fb59` | W-off-r1-c0 | `queuedUP` | 0 | 0 |
| `002388f567b2` | W-off-r1-c1 | `queuedUP` | 0 | 0 |
| `cf2f3504effd` | W-off-r1-c2 | `queuedUP` | 0 | 0 |
| `ad5d3fe7130d` | W-off-r1-c3 | `uploading` | 77127 | 1 |
| `0c28dcfef6ef` | W-off-r1-c4 | `uploading` | 62101 | 1 |
| `09eba8fe7fe3` | W-off-r1-i0 | `stalledDL` | 0 | 0 |
| `743450503c0c` | W-off-r1-i1 | `stalledDL` | 0 | 0 |
| `299710dd45ea` | W-off-r1-i2 | `stalledDL` | 0 | 0 |

The active total is 5 = `max_active_torrents`, and 3 = `max_active_downloads`
of them are the downloads, as in item 4 of the 2026-11-13 doc.

### U0-off: `-1/-1/3`, exemption off, 3 idle seeders (no leecher), then 1 download — **unresolved**

| rep | run start (UTC) | bring-up | active at bring-up | before | after | verdict | settled at |
|---|---|---|---|---|---|---|---|
| 1 | 22:44:03Z | — | — | — | — | **harness error** | — |
| 2 | 22:44:19Z | 1.02 s | 0 | `(0,0,0,3,0)` | `(1,0,2,1,0)` | other | 1.01 s |
| 3 | 22:46:21Z | 1.02 s | 0 | `(0,0,0,3,0)` | `(1,0,2,1,0)` | other | 1.01 s |

**Unresolved: the agreement rule is not met.** Two separate problems:

- **Rep 1: harness error.** `add_seeders` timed out after 15 s with seeder
  `93aeb1e47ee6…` in `missingFiles` (the other two `stalledUP`). It was the
  first add on a fresh pair. The data file is written before `torrents/add`
  is posted, so the client should have found it. Why it reported
  `missingFiles` was not investigated. It did not recur in the other 11 runs,
  which included 39 more seeder adds. In the nightly, `with_one_retry` would
  retry such a run.
- **Reps 2 and 3: readiness passed on a transient.** A freshly added seeder
  sits in `queuedUP` for about 1 s before it goes `stalledUP` (the 2026-11-13
  doc's transient table). `add_seeders` and `idle_ready` both accept
  `queuedUP`, so bring-up passed on its first two polls (t = 0 and 1.02 s)
  with all three seeders still in that transient. The download was added while
  no seeder was active. In the window, at t = 1.01 s, c0 and c1 went
  `stalledUP`, c2 stayed `queuedUP`, and the download went `stalledDL`, and that
  held to t = 120 s. So the `after` shows the same shape as U1 and as F1. But
  the event did not meet a full `max_active_torrents`, which is what the
  control is for. The `before` of `(0,0,0,3,0)` and the verdict **other** both
  come from the readiness rule, not from qBittorrent.

Rep 2, the bring-up poll that passed readiness (t = 1.02 s): `7f329fa3eadd`
U0-off-r2-c0, `8a06092a9844` c1 and `c8c5eefa06d3` c2, all `queuedUP`, upspeed
0, `num_leechs` 0. Final poll (t = 120.01 s): c0 and c1 `stalledUP`, c2
`queuedUP`, download `c69bf7f8b5c1` `stalledDL`, all at upspeed 0.

The U1 and W scenarios did not have this problem: `ready` requires an
`uploading` seeder, and the transient `queuedUP` had already turned
`stalledUP`, then `uploading`, by the time readiness passed (see the rep 1
bring-up tables).

### Settle time and the nightly budget

Across the 11 completed runs, the counts reached their final value at
t = 1.01 to 2.02 s after the downloads were added, and **no count changed
after that in any run**, through t = 120 s. So every run was stable by
t = 30 s, with a wide margin. If the full `-m container` run measures over
30 min (Task 6), the budget rule's condition for shrinking the window to 60 s
is met.

Wall time per run was about 2 min 4 s (setup, bring-up of 4 to 6 s, and the
120 s window). The four scenarios once each come to about 8.3 min, plus about
7 s to start the pair.

### A1: the seeders really uploaded

`check_uploading` passed in all 9 leecher runs: every seeder that was never
demoted was `uploading` above 2048 B/s at every window poll. The lowest such
`upspeed` in any window poll was 12862 B/s (U1-on rep 1, t = 0.01 s, a
ramp-up value). After ramp-up the seeders ran at about 45000 to 77000 B/s,
near the leecher's 65536 B/s per-torrent cap.

Two caveats about the `upspeed` field. Neither changes a verdict:

- Seeders often report the **same `upspeed` to the byte** at a poll (13003 for
  all three at U1-off rep 1, t = 4.01 s). They are separate torrents with
  separate hashes, each with `num_leechs` 1, so this looks like
  qBittorrent/libtorrent sampling the rate per tick across torrents that
  transfer in lockstep under the same cap.
- A just-demoted seeder keeps its last `upspeed` for a few polls (U1-on rep 1:
  c2 is `queuedUP` with 59502 at t = 2.01 and 3.01 s), then reads 0. The
  `queuedUP` state and `num_leechs` 0 are the reliable signs of demotion, and
  `check_uploading` keys on the state.

## Findings

These are for Tom. None of them is acted on here.

**U1. Actively uploading seeders yield `max_active_torrents` slots to a new
download, as idle ones do (F1).** With 3 seeders each uploading at about
64 KiB/s to a connected peer and `max_active_torrents=3` full, a peerless
download started within about 2 s, and one uploading seeder was demoted to
`queuedUP` and lost its peer. 6 of 6 runs, exemption off and on. So
"uploading versus idle", one of the candidate explanations F1 listed, is not
what makes seeders hold their slots on this build.

**For the `_WEDGED` pin** (`tests/test_check_cli.py:83`, exit 1). W-off ran
`_WEDGED`'s own limits with uploading seeders, and the 3 downloads started in
3 of 3 runs. As the spec's "Why" puts it, the pin asserts a wedge that 5.2.3
does not produce by seeders filling `max_active_torrents`, whether they are
idle (item 4) or uploading (W-off). #393 did wedge, so some other mechanism
produced it. Candidates not measured here: stalled dead-swarm **downloads**
holding the slots (2026-11-13 item 3e, the leading one), torrents that
finished downloading in place rather than being added complete, a population
of 52 rather than 5, and a different qBittorrent or libtorrent build at the
time of #393.

**For F3 and the `qbt.slow_exempt_off` premise** (`queue_liveness.py:1042`).
The spec named the risk: if uploading seeders wedged, the premise would give a
false PASS, because the exemption cannot free an uploading seeder's slot.
They did not wedge. U1-on and U1-off gave the same counts, so the exemption
did not decide the outcome here. The seeders yielded with it on or off. On
this build, then, the seeder route to that false PASS was not observed. As the
spec says, removing the premise because of seeders would trade a false PASS
that was not seen for false FAILs on seeder-full configs. F3 itself still
stands: the exemption does not lift `max_active_torrents` (item 3e), and a
slot held by stalled downloads is a real wedge with the exemption on. That is
the download route, which this slice does not measure.

**U0 needs a readiness fix before it can be pinned.** `idle_ready` accepts the
`queuedUP` transient of a fresh add, so the control never met a full limit.
Requiring `stalledUP` for idle readiness, or waiting until no seeder is
`queuedUP` under an unlimited upload limit, would fix it. That is a change to
Task 4's `idle_ready` and is left to Tom. Until then U0-off is **unresolved**
and must not be pinned.

## Agreement summary

| id | runs agreeing on `(before, after)` | recorded `(before, after)` | verdict | status |
|---|---|---|---|---|
| U0-off | 2 of 3 (rep 1 harness error) | — | other (artifact) | **unresolved** |
| U1-off | 3 of 3 | `(0,0,3,0,0)`, `(1,0,2,1,0)` | yield | agreed |
| U1-on | 3 of 3 | `(0,0,3,0,0)`, `(1,0,2,1,0)` | yield | agreed |
| W-off | 3 of 3 | `(0,0,3,2,0)`, `(3,0,2,3,0)` | yield | agreed |
