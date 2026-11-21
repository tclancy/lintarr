# Remote path mapping: Windows case, a blank local path, and a '/' mapping (lintarr#68)

*2026-11-21T15:44:39Z by Showboat 0.6.1*
<!-- showboat-id: e9dc4430-3985-46b2-a1d7-25eda2ed4e76 -->

`checks/paths.py` `translate` models what Sonarr and Radarr do to a download
client's path before they look for the file: apply the first remote path
mapping whose host matches and whose `remotePath` is a prefix. Three edges of
that were never read from the source, and #68 parked them:

1. **Windows case.** The prefix test was case-sensitive, so a Windows
   qBittorrent reporting `c:\downloads\tv` did not match a mapping for
   `C:\Downloads\`.
2. **A blank `localPath`.** It produced `/tail` — an absolute path that reads
   as real.
3. **A `remotePath` of `/`.** Nobody knew what it meant, so it needed pinning
   before someone "fixed" it.

Edges 1 and 3 are answered by reading the arr, so this read runs the arr's own code:
`docs/68-ospath-probe.sh` fetches Sonarr's `OsPath.cs` at a pinned release tag,
compiles it, and performs the mapping arithmetic. The full read is
`docs/measurements/2026-11-21-arr-path-mapping-edges.md`. **One of #68's
assumptions was wrong:** a `/` → `/mnt` mapping does *not* turn `/data` into
`/mnt/data`. It turns every path the client reports into `/`, and lintarr now
does the same.

Everything below runs against a stub stack serving the bodies
`tests/fixtures/homelab_paths.py` measured on Tom's homelab on 2026-11-19 —
qBittorrent 5.2.3 plus one Sonarr — with the mapping, qBittorrent's save path,
the arr's root folder and its mount list on argv. `case.sh` starts the stub on
a free port, points one checkout's `lintarr` at it over real HTTP, and prints
the `hardlink-futility` finding (the only check that reads paths).

```bash
set -euo pipefail
DEMO=/tmp/lintarr68-demo
mkdir -p "$DEMO"

# A checkout of the commit this branch was cut from, for the "before" column.
[ -d "$DEMO/base" ] || git worktree add -f --detach "$DEMO/base" b2a76d0 >/dev/null 2>&1
git -C "$DEMO/base" log --oneline -1

cat > "$DEMO/case.sh" <<'RUNNER'
set -uo pipefail
STUB_PY=$1; CHECKOUT=$2; REMOTE=$3; LOCAL=$4; SAVE=$5; ROOT=$6; shift 6
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
python3 "$STUB_PY" "$PORT" "$REMOTE" "$LOCAL" "$SAVE" "$ROOT" "$@" >/dev/null 2>&1 &
STUB=$!
trap 'kill $STUB 2>/dev/null' EXIT
until curl -fs "http://127.0.0.1:$PORT/api/v2/app/version" >/dev/null; do sleep 0.1; done
cd "$CHECKOUT"
OUT=$(DOWNLOAD_CLIENT_KIND=qbittorrent DOWNLOAD_CLIENT_URL="http://127.0.0.1:$PORT" \
  DOWNLOAD_CLIENT_USER=admin DOWNLOAD_CLIENT_PASS=hunter2 \
  ARR_URL="http://127.0.0.1:$PORT" ARR_API_KEY=arrkey \
  uv run --quiet --frozen lintarr check 2>/dev/null)
RC=$?
printf '%s\n' "$OUT" | awk '/hardlink-futility/,/^[0-9]+ checked/' | sed '$d'
echo "exit code: $RC"
RUNNER
echo "runner written"
```

```output
b2a76d0 Merge pull request #78 from tclancy/claude/66-dump-facts-neutral-and-backend-facts-can
runner written
```

## 1. A Windows qBittorrent whose mapping differs in case

qBittorrent on Windows saves to `c:\downloads\tv`; the arr's mapping says
`C:\Downloads\` → `/data/torrents`, and its library is `/data/media/tv`. The
arr compares Windows paths case-insensitively, so it places that download on
`/data`, beside the library: one mount, hardlinks fine. Before, the mapping
never applied and lintarr could not read the Windows path it was left holding.

```bash
DEMO=/tmp/lintarr68-demo
for CO in "$DEMO/base" .; do
  echo "--- $([ "$CO" = . ] && echo "after (this branch)" || echo "before (b2a76d0)")"
  bash "$DEMO/case.sh" "$PWD/docs/68-stub-stack.py" "$CO" \
    'C:\Downloads\' /data/torrents 'c:\downloads\tv' /data/media/tv / /config /data
done
```

```output
--- before (b2a76d0)
SKIP  hardlink-futility  [qbittorrent[main]]

  Could not read:
    arr.hardlink_import_crosses_a_mount unknown
  sonarr[main]: save path 'c:\\downloads\\tv' is not absolute POSIX

exit code: 3
--- after (this branch)
PASS  hardlink-futility  [qbittorrent[main]]
  downloads and library folders share a mount for sonarr[main]

exit code: 0
```

## 2. A `remotePath` of `/`

The mapping is `/` → `/mnt`; qBittorrent saves to `/downloads/tv`; the library
is `/data/media/tv` and the arr's mounts are `/`, `/mnt` and `/data`. The arr
subtracts the mapping's remote path from the client's, `/` has no components to
subtract, and the subtraction hands back a rooted `/` — which wins the join
outright. So the arr looks for this download at `/`, full stop.

Before, lintarr built `/mnt/downloads/tv` and **failed the stack over it**:
a confident claim about a path this arr never computes. After, the download is
on `/` and the check says so, which is a thing an operator can go and check.

```bash
DEMO=/tmp/lintarr68-demo
for CO in "$DEMO/base" .; do
  echo "--- $([ "$CO" = . ] && echo "after (this branch)" || echo "before (b2a76d0)")"
  bash "$DEMO/case.sh" "$PWD/docs/68-stub-stack.py" "$CO" \
    / /mnt /downloads/tv /data/media/tv / /mnt /data
done
```

```output
--- before (b2a76d0)
FAIL  hardlink-futility  [qbittorrent[main]]

  What lintarr read from your stack — check these yourself:
    arr.hardlink_import_crosses_a_mount holds
  sonarr[main]: downloads in /mnt/downloads/tv are on mount /mnt, root folder /data/media/tv is on mount /data

  Therefore: every import from this client into that library is a full copy, not a hardlink, so each one doubles its disk use. Put downloads and library under one volume in the arr's container (TRaSH's `/data` layout), or turn hardlinks off to make the copying deliberate.

exit code: 1
--- after (this branch)
SKIP  hardlink-futility  [qbittorrent[main]]

  Could not read:
    arr.hardlink_import_crosses_a_mount unknown
  sonarr[main]: the arr reports no mount other than / for /; either it cannot see the download or that mount is not listed (network mounts are not)

exit code: 3
```

## 3. A mapping with a blank `localPath`

The arr's API refuses this one (`localPath` is validated with `IsValidPath`,
`MappedNetworkDriveValidator` and `PathExistsValidator`), so it takes a
hand-edited database — but the arr's arithmetic, run below, turns
`/downloads/tv` into `/tv`. That is the worst kind of wrong answer: an absolute
path that reads as real. Here the stack's library is `/tv/shows`, so the
invented `/tv` shared its mount and lintarr **passed** a stack it had not
actually read. After, the mapping places nothing, and the check names both the
path it was given and the nothing it got back.

```bash
DEMO=/tmp/lintarr68-demo
for CO in "$DEMO/base" .; do
  echo "--- $([ "$CO" = . ] && echo "after (this branch)" || echo "before (b2a76d0)")"
  bash "$DEMO/case.sh" "$PWD/docs/68-stub-stack.py" "$CO" \
    /downloads/ '' /downloads/tv /tv/shows / /tv /downloads
done
```

```output
--- before (b2a76d0)
PASS  hardlink-futility  [qbittorrent[main]]
  downloads and library folders share a mount for sonarr[main]

exit code: 0
--- after (this branch)
SKIP  hardlink-futility  [qbittorrent[main]]

  Could not read:
    arr.hardlink_import_crosses_a_mount unknown
  sonarr[main]: save path '' (translated from '/downloads/tv') is not absolute POSIX

exit code: 3
```

## Where those answers come from: the arr's own code, executed

`docs/68-ospath-probe.sh` fetches Sonarr's `OsPath.cs` at a pinned release tag
(`v4.0.20.3014`, the latest stable release and homelab's build), compiles it
with `docs/68-ospath-probe.cs` — the body of `RemapRemoteToLocal` and nothing
else — and runs it. This is Sonarr's arithmetic, not a transcription of it into
Python and then believed. Needs `brew install mono`; Sonarr is GPL-3.0, so its
source is fetched at run time and never vendored here.

```bash
docs/68-ospath-probe.sh
```

```output
Sonarr OsPath + RemapRemoteToLocal, Sonarr/Sonarr v4.0.20.3014
OsPath.cs: 13617 bytes, sha256 34233761dfb7810e
A Windows remote path folds case AND separators:
  remote 'C:\Downloads\'    local '/data/torrents' raw 'c:\downloads\tv'          -> [/data/torrents/tv]
  remote 'c:/downloads'     local '/data/torrents' raw 'C:\DOWNLOADS\tv\s1'       -> [/data/torrents/tv/s1]
  remote 'C:\Downloads'     local '/data/torrents' raw 'c:\downloads'             -> [/data/torrents]
A POSIX remote path folds neither:
  remote '/DL'              local '/a'             raw '/dl/x'                    -> no match, path unchanged: [/dl/x]
  remote '/dl'              local '/a'             raw '/DL/x'                    -> no match, path unchanged: [/DL/x]
  remote '/dl'              local '/a'             raw '/dl/x'                    -> [/a/x]
  remote '/DL'              local '/a'             raw '/dl\x'                    -> no match, path unchanged: [/dl/x]
A root remote path swallows the whole path:
  remote '/'                local '/mnt'           raw '/data'                    -> [/]
  remote '/'                local '/mnt'           raw '/data/tv/x'               -> [/]
  remote '//'               local '/mnt'           raw '/data'                    -> [/]
  remote '\\'               local '/mnt'           raw '/data'                    -> THROWS Exception: Cannot combine OsPaths of different platforms ('/mnt' + '\\')
A blank local path (which the arr's own API refuses):
  remote '/dl'              local ''               raw '/dl/x'                    -> [/x]
  remote '/dl'              local '   '            raw '/dl/x'                    -> [   /x]
  remote '/dl'              local ''               raw '/dl'                      -> []
An unrooted path on either side never matches:
  remote ''                 local '/a'             raw '/dl/x'                    -> no match, path unchanged: [/dl/x]
  remote 'dl'               local '/a'             raw '/dl/x'                    -> no match, path unchanged: [/dl/x]
  remote '/dl'              local '/a'             raw 'dl/x'                     -> no match, path unchanged: [dl/x]
```

## The pins

Eight new tests, each citing the measurement document. The last three are the
before/after columns above.

```bash
PINS="folds_case_and_separators or keeps_a_posix_remote_path or root_remote_mapping \
  or blank_local_path or unrooted_remote or mapping_whose_case_differs"
uv run --quiet --frozen pytest tests/checks/test_paths.py tests/checks/test_hardlink_futility.py \
  --no-header -v -k "$PINS" 2>&1 \
  | grep -E 'PASSED|passed' | sed 's/ PASSED.*//;s#tests/checks/##' | sed -E 's/ in [0-9.]+s//'
```

```output
test_paths.py::test_translate_folds_case_and_separators_for_a_windows_client_path
test_paths.py::test_translate_keeps_a_posix_remote_path_case_sensitive
test_paths.py::test_a_root_remote_mapping_swallows_the_whole_path
test_paths.py::test_a_blank_local_path_translates_to_nothing_rather_than_a_plausible_path
test_paths.py::test_an_unrooted_remote_or_client_path_never_matches
test_hardlink_futility.py::test_a_windows_client_path_matches_a_mapping_whose_case_differs
test_hardlink_futility.py::test_a_root_remote_mapping_leaves_the_download_on_root_not_under_the_local_path
test_hardlink_futility.py::test_a_mapping_with_a_blank_local_path_is_unplaceable_not_invented
======================= 8 passed, 63 deselected =======================
```

Seven of the eight fail on `b2a76d0` — run there by copying this branch's two
test files into the base checkout. The eighth,
`test_translate_keeps_a_posix_remote_path_case_sensitive`, passes on both: it
pins behaviour that was already right and now has a measurement behind it.

```bash
DEMO=/tmp/lintarr68-demo
PINS="folds_case_and_separators or keeps_a_posix_remote_path or root_remote_mapping \
  or blank_local_path or unrooted_remote or mapping_whose_case_differs"
cp tests/checks/test_paths.py tests/checks/test_hardlink_futility.py "$DEMO/base/tests/checks/"
cd "$DEMO/base"
uv run --quiet --frozen pytest tests/checks --no-header -q -k "$PINS" 2>&1 \
  | grep -E '^(FAILED|[0-9]+ failed)' | sed 's#tests/checks/##' | sed -E 's/ in [0-9.]+s//'
git checkout -- tests/checks
```

```output
FAILED test_hardlink_futility.py::test_a_windows_client_path_matches_a_mapping_whose_case_differs
FAILED test_hardlink_futility.py::test_a_root_remote_mapping_leaves_the_download_on_root_not_under_the_local_path
FAILED test_hardlink_futility.py::test_a_mapping_with_a_blank_local_path_is_unplaceable_not_invented
FAILED test_paths.py::test_translate_folds_case_and_separators_for_a_windows_client_path
FAILED test_paths.py::test_a_root_remote_mapping_swallows_the_whole_path
FAILED test_paths.py::test_a_blank_local_path_translates_to_nothing_rather_than_a_plausible_path
FAILED test_paths.py::test_an_unrooted_remote_or_client_path_never_matches
7 failed, 1 passed, 78 deselected
```

## The whole suite, and the linters

The pre-push battery is `ruff-check`, `ruff-format`, the whitespace hooks and
the full suite.

```bash
uv run --quiet --frozen pytest -q 2>&1 | tail -1 | sed -E 's/ in [0-9.]+s//'
uv run --quiet --frozen ruff check src tests docs/68-stub-stack.py
uv run --quiet --frozen ruff format --check src tests docs/68-stub-stack.py
```

```output
2932 passed, 40 deselected
All checks passed!
131 files already formatted
```

## What is still not modelled

A `remotePath` of `\\` — a UNC root with no server or share — makes the arr
**throw** (`Cannot combine OsPaths of different platforms`), because the
remainder it computes is a Windows path and the local path is POSIX. lintarr
treats any separators-only remote path as root and returns `/`. Reporting a
thrown import needs a verdict shape `translate` does not have, and the
configuration is nonsense; it is named in `paths.py` and in the measurement
document rather than modelled.

The blank `localPath` is the one place lintarr knowingly does not copy the arr,
and the reason is in the table at the end of
`docs/measurements/2026-11-21-arr-path-mapping-edges.md`: the arr cannot be
configured into it, and copying its arithmetic would have lintarr place a file
on a mount from a mapping that names no directory.
