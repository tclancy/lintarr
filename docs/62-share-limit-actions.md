# lintarr #62: measuring what a share limit actually does

*2026-11-21T14:04:14Z by Showboat 0.6.1*
<!-- showboat-id: 0ea4e349-6de4-416a-8bc3-0fe208720f61 -->

`orphan-inevitability` (lintarr#58) decides whether anything will ever remove a
finished download. Its whole answer rests on what qBittorrent's share-limit
*action* does: `Remove` is a FAIL because it drops the torrent and leaves the
files, `RemoveWithContent` is a PASS because it takes them. Those two sentences
were **assumed** — read off the qBittorrent source, never run.

Here is the axiom table as it stood on `main`.

```bash
git show 6c436c3:docs/superpowers/specs/2026-11-19-orphan-inevitability-design.md | sed -n "/^## Axioms/,/^## Deploy/p" | head -14
```

```output
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
```

## Why this needed a config file, not an API call

A category's action cannot be set through qBittorrent 5.2.3's WebAPI.
`torrents/createCategory` and `torrents/editCategory` read only `category`,
`savePath`, `downloadPathEnabled` and `downloadPath`
(`src/webui/api/torrentscontroller.cpp:1876-1925`), and they answer **200**
while silently ignoring a `shareLimitAction` parameter — measured before the
source was read: after posting `shareLimitAction=Remove`, the category still
reported `"Default"`.

The writable route is the client's own store. qBittorrent saves categories to
`<config>/qBittorrent/categories.json` and parses them with
`CategoryOptions::fromJSON`, so the harness writes that file before the
container starts (`tests/conformance/categories.py`) and `start_qbt` gained an
optional `/config` mount. Below, the probe prints what the API reports back for
each pre-seeded category, then fires a real limit for every action — globally
first, then per category under two contradicting globals.

```bash
unset VIRTUAL_ENV
uv run --frozen python tools/probe_qbt_share_limits.py
```

```output
2026-11-21T14:04:33+00:00  image lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.14-ls476
qBittorrent v5.2.3

categories as the endpoint reports them:
  default: share_limit_action='Default'
  enablesuperseeding: share_limit_action='EnableSuperSeeding'
  remove: share_limit_action='Remove'
  removewithcontent: share_limit_action='RemoveWithContent'
  stop: share_limit_action='Stop'

## the global action (`max_ratio_act`)

| case | action | state after | files | super seeding | fired after | matches the table |
|---|---|---|---|---|---|---|
| global 0 | Stop | stoppedUP | kept | off | 10.4 s | yes |
| global 1 | Remove | gone from torrents/info | kept | off | 10.1 s | yes |
| global 2 | EnableSuperSeeding | stalledUP | kept | on | 10.6 s | yes |
| global 3 | RemoveWithContent | gone from torrents/info | deleted | off | 10.3 s | yes |

max_ratio_act: set 2 -> reads 2; then set -1 -> reads 0

## the category action, under global 3 (RemoveWithContent)

| case | action | state after | files | super seeding | fired after | matches the table |
|---|---|---|---|---|---|---|
| category stop | Stop | stoppedUP | kept | off | 10.4 s | yes |
| category remove | Remove | gone from torrents/info | kept | off | 10.4 s | yes |
| category enablesuperseeding | EnableSuperSeeding | stalledUP | kept | on | 10.4 s | yes |
| category removewithcontent | RemoveWithContent | gone from torrents/info | deleted | off | 10.4 s | yes |
| category default | Default | gone from torrents/info | deleted | off | 10.4 s | yes |

## the category action, under global 0 (Stop)

| case | action | state after | files | super seeding | fired after | matches the table |
|---|---|---|---|---|---|---|
| category stop | Stop | stoppedUP | kept | off | 10.4 s | yes |
| category remove | Remove | gone from torrents/info | kept | off | 10.4 s | yes |
| category enablesuperseeding | EnableSuperSeeding | stalledUP | kept | on | 10.4 s | yes |
| category removewithcontent | RemoveWithContent | gone from torrents/info | deleted | off | 10.4 s | yes |
| category default | Default | stoppedUP | kept | off | 10.4 s | yes |
```

Read the last column: every row matches `tests/conformance/firing.FATES`, the
committed table. Note the two `category default` rows — the same category, two
different fates, because `Default` is not an action but a redirect to whichever
`max_ratio_act` is set. And `max_ratio_act` itself can never *be* `Default`:
written as `-1` it reads back `0`, since qBittorrent coerces the stored global
to Stop (`sessionimpl.cpp:554-555`). The spec claimed `-1` was observable; it
is not, and the spec now says so.

## The pins the nightly runs

The same table, asserted. 16 items: four global actions, the `-1` read-back,
the category strings as the endpoint reports them, and each action in a
category under both globals.

```bash
unset VIRTUAL_ENV
uv run --frozen pytest -m container tests/conformance/test_share_limit_actions.py -v --no-header -p no:cacheprovider
```

```output
============================= test session starts ==============================
collecting ... collected 16 items

tests/conformance/test_share_limit_actions.py::test_the_global_action_decides_what_a_fired_limit_does[Stop] PASSED [  6%]
tests/conformance/test_share_limit_actions.py::test_the_global_action_decides_what_a_fired_limit_does[Remove] PASSED [ 12%]
tests/conformance/test_share_limit_actions.py::test_the_global_action_decides_what_a_fired_limit_does[EnableSuperSeeding] PASSED [ 18%]
tests/conformance/test_share_limit_actions.py::test_the_global_action_decides_what_a_fired_limit_does[RemoveWithContent] PASSED [ 25%]
tests/conformance/test_share_limit_actions.py::test_the_global_action_never_reports_default PASSED [ 31%]
tests/conformance/test_share_limit_actions.py::test_each_action_string_reads_back_from_the_categories_endpoint PASSED [ 37%]
tests/conformance/test_share_limit_actions.py::test_a_categorys_action_overrides_the_global[Stop] PASSED [ 43%]
tests/conformance/test_share_limit_actions.py::test_a_categorys_action_overrides_the_global[Remove] PASSED [ 50%]
tests/conformance/test_share_limit_actions.py::test_a_categorys_action_overrides_the_global[EnableSuperSeeding] PASSED [ 56%]
tests/conformance/test_share_limit_actions.py::test_a_categorys_action_overrides_the_global[RemoveWithContent] PASSED [ 62%]
tests/conformance/test_share_limit_actions.py::test_a_categorys_action_overrides_a_stop_global[Stop] PASSED [ 68%]
tests/conformance/test_share_limit_actions.py::test_a_categorys_action_overrides_a_stop_global[Remove] PASSED [ 75%]
tests/conformance/test_share_limit_actions.py::test_a_categorys_action_overrides_a_stop_global[EnableSuperSeeding] PASSED [ 81%]
tests/conformance/test_share_limit_actions.py::test_a_categorys_action_overrides_a_stop_global[RemoveWithContent] PASSED [ 87%]
tests/conformance/test_share_limit_actions.py::test_the_default_category_action_follows_the_global[RemoveWithContent] PASSED [ 93%]
tests/conformance/test_share_limit_actions.py::test_the_default_category_action_follows_the_global[Stop] PASSED [100%]

======================== 16 passed in 90.12s (0:01:30) =========================
```

## Does the pin bite?

A pin you have only watched pass proves nothing. So: put the *old assumption's
opposite* into the table — claim `Remove` deletes the files, the way
`RemoveWithContent` does — and run the one case that reads that row. The block
restores the file on its way out and prints `git status` to prove it.

```bash
set -u
unset VIRTUAL_ENV
cp tests/conformance/firing.py /tmp/firing-62.bak
trap "cp /tmp/firing-62.bak tests/conformance/firing.py" EXIT
python3 - <<PY
from pathlib import Path
p = Path("tests/conformance/firing.py")
p.write_text(p.read_text().replace(
    "Fate(1, \"Remove\", None, keeps_files=True)",
    "Fate(1, \"Remove\", None, keeps_files=False)"))
PY
echo "table now claims:  $(grep "\"Remove\"" tests/conformance/firing.py | tr -s " ")"
uv run --frozen pytest -m container \
  "tests/conformance/test_share_limit_actions.py::test_the_global_action_decides_what_a_fired_limit_does[Remove]" \
  -q --no-header -p no:cacheprovider; echo "pytest exit: $?"
cp /tmp/firing-62.bak tests/conformance/firing.py
echo "table restored to: $(grep "\"Remove\"" tests/conformance/firing.py | tr -s " ")"
cmp -s /tmp/firing-62.bak tests/conformance/firing.py && echo "restored copy is byte-identical to the backup"
```

```output
table now claims:   Fate(1, "Remove", None, keeps_files=False),
F                                                                        [100%]
=================================== FAILURES ===================================
________ test_the_global_action_decides_what_a_fired_limit_does[Remove] ________

qbt = <tests.conformance.qbt.QbtHarness object at 0x109ebeba0>
fate = Fate(action=1, name='Remove', state=None, keeps_files=False, super_seeding=False)

    @pytest.mark.parametrize("fate", FATES, ids=_IDS)
    def test_the_global_action_decides_what_a_fired_limit_does(qbt: QbtHarness, fate: Fate) -> None:
        """Each `max_ratio_act` value, against a complete torrent at ratio 0."""
        disarm(qbt, fate.action)
        t = make_torrent(f"share-global-{fate.action}")
        add_settled(qbt, t, complete=True, want="stalledUP")
        arm_ratio_zero(qbt, fate.action)

        observed = watch_until_fired(qbt, {"t": t}, SHARE_LIMIT_TIMEOUT)

>       assert observed["t"] == fate.observed
E       AssertionError: assert Observed(stat...seeding=False) == Observed(stat...seeding=False)
E
E         Omitting 2 identical items, use -vv to show
E         Differing attributes:
E         ['file_exists']
E
E         Drill down into differing attribute file_exists:
E           file_exists: True != False

tests/conformance/test_share_limit_actions.py:65: AssertionError
=========================== short test summary info ============================
FAILED tests/conformance/test_share_limit_actions.py::test_the_global_action_decides_what_a_fired_limit_does[Remove]
1 failed in 14.34s
pytest exit: 1
table restored to:  Fate(1, "Remove", None, keeps_files=True),
restored copy is byte-identical to the backup
```

The failure is exactly the claim: `file_exists: True != False`. The orphan is
real, and the nightly will say so if qBittorrent ever stops making it.

## The cheap half

The watcher that decides a limit has "fired" is unit-tested without Docker —
a watcher that called every torrent fired on sight would pass the container
tests while measuring nothing — along with the keys the pre-seeded
`categories.json` must use and the harness calls that grew a `category`.

```bash
unset VIRTUAL_ENV
uv run --frozen pytest tests/conformance/test_firing.py tests/conformance/test_qbt_client.py -q --no-header -p no:cacheprovider
```

```output
...................                                                      [100%]
19 passed in 0.07s
```

## And the axiom table now

Four rows that said "assumed" or "not conformance-tested" are measured, two
rows are new (super seeding, and the global that cannot be `Default`), and the
closing sentence that promised this slice is gone.

```bash
sed -n "/^## Axioms/,/^## Deploy/p" docs/superpowers/specs/2026-11-19-orphan-inevitability-design.md | cut -c1-120
```

```output
## Axioms

Conformance-tested rows are pinned nightly by
`tests/conformance/test_share_limit_actions.py` against the image
`tests/conformance/image.py` names, and the run they come from is
`docs/measurements/2026-11-21-qbt-share-limit-actions.md` (lintarr#62).

| Axiom | Status |
|---|---|
| `ShareLimitAction` values; `Default` matches no branch | measured from source (existing `_RELEASING_ACTIONS`) |
| Category `share_limit_action` strings | conformance-tested, 2026-11-21: the endpoint reports `Stop`, `Remove`, `Enable
| A category's action overrides the global `max_ratio_act`, and `Default` redirects to it | conformance-tested, 2026-11-
| The arr removes a torrent only when it is stopped and has reached a torrent-level or global limit, never a category li
| A post-import category hides the torrent from the arr | read from Sonarr source, 2026-11-19 |
| Remove (1) deletes the torrent and keeps its files | conformance-tested, 2026-11-21 (globally and per category) |
| RemoveWithContent (3) deletes the torrent and its files | conformance-tested, 2026-11-21 (per category; globally since
| Stop (0) leaves the torrent `stoppedUP` with its files | conformance-tested, 2026-11-21 (globally and per category) |
| EnableSuperSeeding (2) leaves the torrent seeding with its files, so nothing reclaims | conformance-tested, 2026-11-21
| The global action can itself be `Default` (-1) | **false on 5.2.3** (measured 2026-11-21): a written `-1` reads back a

## Deploy impact
```

## What this costs the nightly

16 items, ~1 min 30 s. The full local container run went 56 items in 21 min
12 s (against 1267 s for 38 items on 2026-11-16, so the new items sit inside
the run-to-run spread). The worst case in `conformance.yml`'s budget comment
becomes ~33.1 min, still inside the 35 min step timeout, so no timeout moved.

One failure in that full run — `test_uploading_seeders[U0-off]`, the documented
macOS bring-up flake, classified `harness-error` — passed on its own in
144.9 s. Nothing in `src/` changed.
