# qBittorrent 5.2.3: what each share-limit action does, globally and per category

**Measured 2026-11-21** against the pinned conformance image
`lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.14-ls476` (qBittorrent **v5.2.3**,
as the WebUI reported it). One container, one complete peerless 16 KiB torrent
per case, the global ratio limit armed at 0 so the limit fires on the next
share-limit pass. Reproduce with:

    uv run python tools/probe_qbt_share_limits.py

This is lintarr#62. It measures the four axioms
`docs/superpowers/specs/2026-11-19-orphan-inevitability-design.md` listed as
assumed: that `Remove` (1) drops the torrent and keeps its files, that
`RemoveWithContent` (3) takes the files too, that `Stop` (0) leaves the torrent
`stoppedUP`, and that a category's `share_limit_action` string overrides the
global while `Default` redirects to it. Nothing in `src/` changes; the check
already read the actions this way. What changes is that the nightly now fails
if qBittorrent stops behaving like this —
`tests/conformance/test_share_limit_actions.py`, 16 items, which assert exactly
the table below through `tests/conformance/firing.FATES`.

## TL;DR

- **Every action does what the check assumed.** `Stop` leaves the torrent
  `stoppedUP` with its files; `Remove` drops the torrent and **keeps its
  files** (the orphan the check's `remove-keeps-files` finding is about);
  `RemoveWithContent` deletes both; `EnableSuperSeeding` leaves the torrent
  seeding with `super_seeding` on, so nothing is reclaimed at all.
- **A category's action overrides the global**, and each of the four was
  measured under two contradicting globals, so no row can be the global's
  behaviour in disguise. A `Default` category takes whichever fate the global
  names.
- **The category strings are exactly the four `ACTION_BY_NAME` maps**, plus
  `Default`, reported verbatim by `GET /api/v2/torrents/categories`.
- **One correction to the spec: the *global* action can never read back
  `Default`.** `max_ratio_act` set to `-1` reads back as `0`, because
  qBittorrent coerces the stored global to `Stop`
  (`src/base/bittorrent/sessionimpl.cpp:554-555`, release 5.2.3). The check's
  `-1` arm (it reclaims nothing) is therefore unreachable from this input
  rather than wrong, and the Inputs table's "`-1` Default is observable" is
  now corrected in the spec.
- **A limit fires 8-10 s after the torrent settles**, one share-limit pass, so
  the harness's 30 s `SHARE_LIMIT_TIMEOUT` is three passes, unchanged.

## Why a category needs a pre-seeded config file

qBittorrent 5.2.3's WebAPI cannot set a category's share-limit action.
`torrents/createCategory` and `torrents/editCategory` read only `category`,
`savePath`, `downloadPathEnabled` and `downloadPath`
(`src/webui/api/torrentscontroller.cpp:1876-1925`), and both answer `200` while
silently ignoring a `shareLimitAction` parameter — measured first, before the
source was read: after posting `shareLimitAction=Remove` the category still
reported `"Default"`.

The client's own store is the writable route. qBittorrent keeps categories in
`<config>/qBittorrent/categories.json` and parses them with
`CategoryOptions::fromJSON`, whose keys are `save_path`, `download_path`,
`ratio_limit`, `seeding_time_limit`, `inactive_seeding_time_limit` and
`share_limit_action` (`src/base/bittorrent/categoryoptions.cpp`). So the
harness writes that file before the container starts
(`tests/conformance/categories.py`), which is why `start_qbt` gained an
optional `/config` mount. A category written that way reads back through the
API verbatim, which is itself one of the measurements below.

## The run

One container (`qbt_with_categories`), five categories pre-seeded with one
action each, and 14 cases. Captured verbatim from
`tools/probe_qbt_share_limits.py` at 2026-11-21T14:04:33Z, qBittorrent v5.2.3;
the same tables, with the pytest run that asserts them, are in
`docs/62-share-limit-actions.md`.

The last column compares each observation with `tests/conformance/firing.FATES`,
the committed table. Every row agreed.

    categories as the endpoint reports them:
      default: share_limit_action='Default'
      enablesuperseeding: share_limit_action='EnableSuperSeeding'
      remove: share_limit_action='Remove'
      removewithcontent: share_limit_action='RemoveWithContent'
      stop: share_limit_action='Stop'

### The global action (`max_ratio_act`)

| case | action | state after | files | super seeding | fired after | matches the table |
|---|---|---|---|---|---|---|
| global 0 | Stop | stoppedUP | kept | off | 10.4 s | yes |
| global 1 | Remove | gone from torrents/info | kept | off | 10.1 s | yes |
| global 2 | EnableSuperSeeding | stalledUP | kept | on | 10.6 s | yes |
| global 3 | RemoveWithContent | gone from torrents/info | deleted | off | 10.3 s | yes |

`max_ratio_act`: set 2 → reads 2; then set -1 → **reads 0**. The write before
it is what makes that meaningful: the global did change when a legal value was
sent, so the `-1` was accepted and coerced, not ignored.

### The category action, under global 3 (RemoveWithContent)

| case | action | state after | files | super seeding | fired after | matches the table |
|---|---|---|---|---|---|---|
| category stop | Stop | stoppedUP | kept | off | 10.4 s | yes |
| category remove | Remove | gone from torrents/info | kept | off | 10.4 s | yes |
| category enablesuperseeding | EnableSuperSeeding | stalledUP | kept | on | 10.4 s | yes |
| category removewithcontent | RemoveWithContent | gone from torrents/info | deleted | off | 10.4 s | yes |
| category default | Default | gone from torrents/info | deleted | off | 10.4 s | yes |

### The category action, under global 0 (Stop)

| case | action | state after | files | super seeding | fired after | matches the table |
|---|---|---|---|---|---|---|
| category stop | Stop | stoppedUP | kept | off | 10.4 s | yes |
| category remove | Remove | gone from torrents/info | kept | off | 10.4 s | yes |
| category enablesuperseeding | EnableSuperSeeding | stalledUP | kept | on | 10.4 s | yes |
| category removewithcontent | RemoveWithContent | gone from torrents/info | deleted | off | 10.4 s | yes |
| category default | Default | stoppedUP | kept | off | 10.4 s | yes |

The `category default` row is the whole redirect: one category, two fates,
chosen by the global. Every other row is identical under both globals, which is
what "the category's action overrides the global" means. Each batch of five
torrents fired inside one share-limit pass (10.4 s from arming the limit,
including the poll that saw it), so the measured pass length is unchanged from
the 8.4-8.9 s of 2026-11-13 once the 0.25 s polling and the arming request are
allowed for.

## What is pinned, and what it costs

`tests/conformance/test_share_limit_actions.py` asserts the tables above as 16
container items: four global actions, the `-1` read-back, the category strings
as the endpoint reports them, and each of the four actions in a category under
both globals. The category cases are batched two phases deep — one torrent per
category, fired together — so the whole file costs ~1 min 30 s of the nightly
rather than one share-limit pass per case.

Measured 2026-11-21 locally: the file alone, 16 passed in 90.1 s. The whole
nightly, 56 items in 21 min 12 s, with one unrelated failure
(`test_uploading_seeders[U0-off]`, the documented macOS bring-up flake,
classified `harness-error`), which passed on its own in 144.9 s.

The pins were mutation-checked, not merely watched passing: putting the old
assumption's opposite into the table (`Remove` deletes its files) fails the
`Remove` case on exactly that attribute, `file_exists: True != False`. The
receipt is in `docs/62-share-limit-actions.md`.

## Known limits

- **One image.** Everything here is 5.2.3 (`tests/conformance/image.py`), the
  version homelab runs. The nightly is what notices a change.
- **Parent categories.** qBittorrent falls back to a parent category's action
  (`SessionImpl::categoryShareLimitAction` recurses on
  `parentCategoryName`). Not measured, and the check reads a flat map — the
  spec's existing "Parent categories" known limit.
- **Ratio only.** The limit that fires in every case is the global ratio at 0.
  Seeding-time and inactive-seeding-time limits are not re-measured here; that
  a seeding-time limit fires the same way was measured on 2026-11-13.
- **The arr's half is still source-read.** What the arr does when a torrent
  stops (`removeCompletedDownloads`, the post-import category) is Sonarr
  behaviour, not qBittorrent's, and needs an arr container to test.
