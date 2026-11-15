# Uploading-seeder measurement (P2 slice 2) — design

**Date:** 2026-11-15 (UTC). **Status:** draft for Tom's approval.
**Builds on:** `2026-11-12-p2-container-differential-design.md`,
`docs/measurements/2026-11-13-qbt-conformance-assumptions.md` (F1-F3).

## Why

lintarr's founding claim is that homelab#393's config (`_WEDGED`: 3/3/5, slow
exemption off, no share limits) wedges qBittorrent: seeders hold every
`max_active_torrents` slot and no download ever starts. `tests/test_check_cli.py:83`
pins exit 1 (FAIL) on it.

P2 slice 1 measured that claim with **idle** seeders (no peers, upspeed 0) and
it does not hold on 5.2.3. New downloads take the slots and idle seeders yield
them, going `queuedUP` (F1). #393 did wedge on the real stack, where seeders had
peers. The untested case is seeders that are **actively uploading**.

That one measurement decides two open items:

- **The `_WEDGED` FAIL pin.** If uploading seeders wedge `_WEDGED`'s own
  limits (scenario W-off), the pin is right, and for the reason the code gives. If they do not, the pin asserts a wedge 5.2.3
  does not produce.
- **F3 in the shipped predicate.** The `qbt.slow_exempt_off` premise
  (`queue_liveness.py:1042`) treats the slow exemption as rescuing a full
  `max_active_torrents`. If uploading seeders wedge, that is a false PASS,
  because an uploading seeder is not "slow" and the exemption cannot free its
  slot. If they do not, removing the premise would trade that false PASS for
  false FAILs.

This slice measures and records the result. It does not change `src/`, the
predicate, `simulate()`, `trace()` or the `_WEDGED` pin. Those changes come
after it, in one coherent change (see "What comes next").

## Requirements (confirmed by Tom 2026-11-15)

| # | Requirement | Check |
|---|---|---|
| A1 | Seeders in the pinned 5.2.3 image upload above `slow_torrent_ul_rate_threshold` for the whole observation window. | Every poll in the window logs each seeder's `upspeed` above the threshold. A run where this fails is a `harness-error`, not a result. |
| A2 | Uploading seeders fill `max_active_torrents` when a new download is added. | A recorded poll shows K uploading seeders active, K = `max_active_torrents`, at the moment the download is added. |
| A3 | The result says whether the new download starts or stays `queuedDL`. | The measurements doc gets a dated item with the raw poll trace and the verdict. |
| A4 | The scenario runs with the slow exemption both off and on. | There is one trace per setting. |
| A5 | The result is repeatable. | A `container`-marked test re-runs it in the nightly and pins the verdict. A change in qBittorrent's behaviour then turns the nightly red. |

## Approaches considered

1. **A second qBittorrent container as the leecher (chosen by Tom).** Same
   pinned image, on a private docker network with the seeder. The leecher adds
   the same torrents incomplete and connects to the seeder with
   `POST /api/v2/torrents/addPeers`. It downloads slowly under a per-torrent
   cap, so the seeder keeps uploading through the window. It reuses the
   existing harness client and image pin, and nothing new is installed.
2. **A libtorrent Python client as the leecher.** It gives finer control, but
   adds a dependency and a second implementation of the BitTorrent side.
   Rejected.
3. **Public torrents with real peers.** Not reproducible, and it puts traffic
   on the internet from CI. Rejected.

## Design

### Topology

- One docker network per session, `lintarr-conformance-net-<port>`, carrying
  the same expiry label as the containers. `_sweep_expired()` removes leaked
  networks as well as containers.
- **Seeder:** the scenario's qBittorrent. Its BitTorrent listen port is fixed
  (`listen_port` pref, e.g. 6881). `addPeers` takes `IP:port`, so the harness
  resolves the seeder's network IP with `docker inspect` (builder's call).
- **Leecher:** a second container from `QBT_IMAGE` on the same network. Its
  WebUI is published to 127.0.0.1 like the seeder's. Queueing is off
  (`queueing_enabled=false`), so its own queue never limits the test.
- Both set `limit_lan_peers=true`, so rate limits apply on the docker network.
  Both have DHT, PEX and LSD off; `addPeers` is the only way peers meet.
- The existing session `qbt` fixture is untouched. The pair is a separate
  fixture used only by this test module, so slice 1's scenarios keep their
  isolated, peerless world.
- **One pair per module** (my call). `reset()` runs on **both** sides between
  scenarios, deleting torrents and files and restoring default prefs, which
  clears per-torrent limits along with the torrents. A fresh pair per
  scenario would cost about 30 s each for isolation that `reset()` already
  gives.
- `QbtHarness` gains `add_peers()`, `set_download_limit()` and a `rows()`
  reader that returns `upspeed` and `num_leechs` with each state.

### Torrents

- `make_torrent()` already takes `size` and `piece_length`. Uploading seeders
  use 32 MiB with 256 KiB pieces. Their data is written to disk and they are
  added complete with `skip_checking`, as today.
- The leecher caps each torrent's download (`torrents/setDownloadLimit`) at
  64 KiB/s. That is 32× the 2 KiB/s slow threshold, and it keeps one torrent
  uploading for about 8.5 minutes, longer than any window here.
- The torrents stay `private: 1` and announce-less. libtorrent accepts a
  manually connected peer on a private torrent. The **first thing the
  implementation checks**: if a private torrent will not connect via
  `addPeers`, drop `private` for these torrents only and record that.

### Scenarios

Counts are `(downloading, queued, seeding, seeding_queued, left)`.

| id | limits (dl/ul/tot) | exemption | events |
|---|---|---|---|
| U1-off | -1 / -1 / 3 | off | 3 seeders uploading, then 1 incomplete torrent with no peer |
| U1-on | -1 / -1 / 3 | on | the same |
| U0-off (control) | -1 / -1 / 3 | off | the same 3 seeders with **no** leecher, then the incomplete torrent |
| W-off | 3 / 3 / 5 | off | `_WEDGED`'s real limits: 5 seeders uploading, then 3 incomplete torrents with no peer |

U0 reproduces F1 inside this harness. It shows the only difference between U0
and U1 is that the seeders are uploading. Both download and upload limits are
unlimited, so only the total limit binds. That keeps F2 (the upload limit) out
of the picture.

**W-off decides the `_WEDGED` pin** (added on the builder check, Tom
2026-11-15). U1 cannot settle it by itself, because `_WEDGED`'s upload limit of
3 queued 2 of 5 idle seeders (F2). If that limit also binds uploading seeders,
five seeders can never hold five slots, and #393's wedge needs another
mechanism. The leading candidate is stalled dead-swarm downloads holding the
slots (measurement item 3e; #405's "dead swarms holding every
`max_active_torrents` slot"). W-off records, at bring-up, how many of the 5
uploading seeders are active versus `queuedUP`, as well as where the 3
downloads end up. Its verdict is the two counts, not just starts/queued.

`slow_torrent_inactive_timer=1`, as in slice 1.

### Observing

- Bring-up: add the seeders, start the leecher downloads and connect the peers.
  Then wait until every seeder shows `uploading` with `upspeed` above the
  threshold on two consecutive polls. Allow at most 60 s, or it is a
  `harness-error`. This is A1's precondition, checked before the event under
  test.
- Bring-up gets the existing `with_one_retry` (my call): one retry on a slow
  runner, then `harness-error`.
- Then add the incomplete torrent and poll the seeder every 1 s for **120 s**.
  That matches the window of measurement item 3e, the longest stall measured
  so far. Each poll records state, `upspeed` and `num_leechs` for every row.
- The verdict comes from the final 30 s of polls, which must be stable:
  - **starts**: the new torrent is `stalledDL` or `downloading`, and a seeder
    went `queuedUP`.
  - **queued**: the new torrent stays `queuedDL` and every seeder stays
    `uploading`.
  - Anything else is reported as **other**, with the raw rows, and not forced
    into either verdict.
- A1 is re-checked over the whole window. One seeder dropping below threshold
  invalidates the run, unless that seeder was the one demoted to `queuedUP`,
  which is the "starts" signature.

### Recording and pinning

- The measurements doc gets a new dated file,
  `docs/measurements/2026-11-xx-qbt-uploading-seeders.md`. It holds the method,
  the settings read back, the raw rows at the decisive polls and the verdict
  per scenario, in the same style as the 2026-11-13 doc. Three runs per
  scenario before anything is called a result. **If the three runs
  disagree, the scenario is recorded as unresolved and is not pinned** (my
  call). The doc reports it, and Tom decides. A stable `other` is pinned as
  its exact counts, as a Known entry would be.
- `tests/conformance/test_uploading_seeders.py`, marked `container`, re-runs
  U0, U1-off, U1-on and W-off in the nightly and asserts the verdicts the doc
  records.
  It is written after the measurement, not before, so the pin records what was
  seen.
- Failures feed the existing result file. A broken bring-up is
  `harness-error`, and a changed verdict is `disagreement`.
- Nightly budget: roughly 4 × (60 s bring-up + 120 s window) plus about 30 s
  for the second container, so about 13 minutes. That takes the run from about
  13 to about 26 minutes, inside the 35-minute step timeout. If it exceeds
  that, the window shrinks to 60 s, and the doc must justify that 60 s is
  enough.

## Non-goals

- No change to `src/`, `simulate()`, `trace()`, the `_WEDGED` fixture or its
  exit-1 pin.
- Not F2 as a fix. Only W-off sets an upload limit, and it does so only to
  test `_WEDGED` as configured.
- Not the stalled-**download** route (item 3e). That is real and separate.

## What comes next (not this slice)

The result feeds one coherent follow-up. That follow-up corrects the
predicate's seeder-absorption premise, `simulate()` and `trace()` **together**,
so that R3 (`trace()` agrees with `simulate()`, `tests/model/test_queue_trace.py:89`)
keeps holding. The F1/F3 Known entries then go stale and are removed, and
mutating rule 1 or rule 3 goes red in the nightly. Tom decides the `_WEDGED`
pin there, with this slice's verdict in hand.

## Left to the builder

The exact listen port, how bring-up is factored, and how the pair fixture
shares code with the existing `qbt` fixture (prefer lifting `_docker`/`_remove`
over copying them).
