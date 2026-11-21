# lintarr #60: a path with .. cannot be placed on a mount

*2026-11-21T12:28:27Z by Showboat 0.6.1*
<!-- showboat-id: ccab17a5-cd5b-4ad1-a574-817fe4db336c -->

`hardlink-futility` decides whether an arr's downloads and its library sit on
the same mount, and it decides it by comparing strings: `posix_abs` normalised
slashes, and `mount_of` used `PurePosixPath.is_relative_to`, which is lexical.
A `..` segment survived that normalisation, so a save path was placed on the
mount its *text* starts with rather than the one it reaches.

```bash
python3 -c '
from pathlib import PurePosixPath as P
p = P("/downloads/../media/tv")
print(f"{p} is_relative_to /downloads -> {p.is_relative_to(P("/downloads"))}")
print(f"{p} is_relative_to /media     -> {p.is_relative_to(P("/media"))}")
'
```

```output
/downloads/../media/tv is_relative_to /downloads -> True
/downloads/../media/tv is_relative_to /media     -> False
```

Two save paths show both directions of the fault. Both arrs have mounts
`/`, `/downloads` and `/media`, hardlinks on, and one root folder
`/media/tv/shows`. The first save path really sits beside that root folder on
`/media`, so hardlinking works. The second really sits on `/downloads`, so it
cannot. Read lexically, each one reads as the other.

```bash
rm -rf /tmp/lintarr60 && mkdir -p /tmp/lintarr60
cat > /tmp/lintarr60/probe.py <<'PY'
"""Run hardlink-futility over two save paths written with a `..` segment."""

from lintarr.checks.hardlink_futility import check
from lintarr.models import RootFolder
from tests.fixtures.clients import as_client
from tests.fixtures.homelab import qbt_with
from tests.fixtures.homelab_paths import arr_with

MOUNTS = ("/", "/downloads", "/media")

CASES = (
    ("really one mount: /media/tv beside /media/tv/shows", "/downloads/../media/tv"),
    ("really two mounts: /downloads/tv against /media/tv/shows", "/media/../downloads/tv"),
)


def qbt(save_path):
    return as_client(
        qbt_with(
            "main",
            save_path="/data/torrents",
            auto_tmm_enabled=True,
            use_category_paths_in_manual_mode=False,
            categories={"tv": {"name": "tv", "savePath": save_path}},
        )
    )


for truth, save in CASES:
    arr = arr_with(mounts=MOUNTS, root_folders=(RootFolder("/media/tv/shows", True),))
    finding = check(qbt(save), (arr,))
    print(f"save path {save}")
    print(f"  {truth}")
    print(f"  -> {finding.outcome.value}: {finding.detail}")
PY
echo "wrote /tmp/lintarr60/probe.py ($(wc -l < /tmp/lintarr60/probe.py) lines)"
```

```output
wrote /tmp/lintarr60/probe.py (      34 lines)
```

## Before: on `main` at 6c436c3

A throwaway worktree at the commit this branch forks from, running the probe.

```bash
base=/tmp/lintarr60/base
git worktree add -q --detach "$base" 6c436c3
(cd "$base" && PYTHONPATH=. uv run --frozen -q python /tmp/lintarr60/probe.py)
git worktree remove --force "$base"
```

```output
save path /downloads/../media/tv
  really one mount: /media/tv beside /media/tv/shows
  -> FAIL: sonarr[tv]: downloads in /downloads/../media/tv are on mount /downloads, root folder /media/tv/shows is on mount /media
save path /media/../downloads/tv
  really two mounts: /downloads/tv against /media/tv/shows
  -> PASS: downloads and library folders share a mount for sonarr[tv]
```

A FAIL that pages an operator about a hardlink that works, and a PASS over one
that does not.

## After: this branch

`..` is refused rather than resolved. Resolving it lexically would be right
only when no earlier component is a symlink, and the string cannot say; the
mount is unknowable from the path alone, so the triple is unknown and the
finding SKIPs. The detail names the `..` rather than claiming the path "is not
absolute POSIX", which it is.

```bash
PYTHONPATH=. uv run --frozen -q python /tmp/lintarr60/probe.py
```

```output
save path /downloads/../media/tv
  really one mount: /media/tv beside /media/tv/shows
  -> SKIP: sonarr[tv]: save path '/downloads/../media/tv' contains a .. segment, so the mount it is on cannot be read
save path /media/../downloads/tv
  really two mounts: /downloads/tv against /media/tv/shows
  -> SKIP: sonarr[tv]: save path '/media/../downloads/tv' contains a .. segment, so the mount it is on cannot be read
```

## The tests that hold it there

```bash
uv run --frozen -q pytest -v tests/checks/test_paths.py tests/checks/test_hardlink_futility.py -k "parent_segment or dots or reason_names" --no-header -p no:randomly 2>&1 | grep -E "PASSED|passed"
```

```output
tests/checks/test_paths.py::test_a_parent_segment_makes_a_path_unplaceable PASSED [ 16%]
tests/checks/test_paths.py::test_a_component_that_merely_starts_with_dots_is_a_real_directory PASSED [ 33%]
tests/checks/test_paths.py::test_the_reason_names_the_parent_segment_and_passes_a_good_path_through PASSED [ 50%]
tests/checks/test_paths.py::test_a_mount_with_a_parent_segment_is_not_a_mount PASSED [ 66%]
tests/checks/test_hardlink_futility.py::test_a_save_path_with_a_parent_segment_is_unknown_not_a_cross_mount_fail PASSED [ 83%]
tests/checks/test_hardlink_futility.py::test_a_root_folder_with_a_parent_segment_is_unknown PASSED [100%]
======================= 6 passed, 57 deselected in 0.09s =======================
```

## Do those tests actually hold the guard?

Four mutants of the guard, in a throwaway worktree at the fix commit, with a
control run before and after. Each line under a mutant is a test it killed.

```bash
mut=/tmp/lintarr60/mut
git worktree add -q --detach "$mut" c2beb30
src="$mut/src/lintarr/checks/paths.py"
suite() { (cd "$mut" && uv run --frozen -q pytest -q --tb=no 2>&1 \
  | grep -E '^FAILED|passed' | sed -e 's/^FAILED /  killed /' -e 's/^/  /'); }
mutant() {  # name, old, new
  printf '%s\n' "$1"
  python3 -c 'import sys,pathlib
p=pathlib.Path(sys.argv[1]); t=p.read_text(); assert sys.argv[2] in t, "anchor missing"
p.write_text(t.replace(sys.argv[2], sys.argv[3], 1))' "$src" "$2" "$3"
  suite
  (cd "$mut" && git checkout -q -- src/lintarr/checks/paths.py)
}
GUARD='    if _PARENT in raw.split("/"):
        return HAS_PARENT_SEGMENT
'
echo 'control, unmutated'; suite
mutant 'M1: drop the guard (the behaviour before this branch)' "$GUARD" ''
mutant 'M2: resolve .. lexically with posixpath.normpath instead of refusing it' \
  "${GUARD}    return PurePosixPath(\"/\" + raw.lstrip(\"/\"))" \
  '    import posixpath

    return PurePosixPath(posixpath.normpath("/" + raw.lstrip("/")))'
mutant 'M3: test .. as a substring rather than as a path component' \
  'if _PARENT in raw.split("/"):' 'if _PARENT in raw:'
mutant 'M4: swap the reason text for the generic one' \
  'HAS_PARENT_SEGMENT = "contains a .. segment, so the mount it is on cannot be read"' \
  'HAS_PARENT_SEGMENT = "is not absolute POSIX"'
echo 'control again'; suite
git worktree remove --force "$mut"
```

```output
control, unmutated
  2845 passed, 40 deselected in 34.69s
M1: drop the guard (the behaviour before this branch)
    killed tests/checks/test_hardlink_futility.py::test_a_save_path_with_a_parent_segment_is_unknown_not_a_cross_mount_fail
    killed tests/checks/test_hardlink_futility.py::test_a_root_folder_with_a_parent_segment_is_unknown
    killed tests/checks/test_paths.py::test_a_parent_segment_makes_a_path_unplaceable
    killed tests/checks/test_paths.py::test_the_reason_names_the_parent_segment_and_passes_a_good_path_through
  4 failed, 2841 passed, 40 deselected in 33.01s
M2: resolve .. lexically with posixpath.normpath instead of refusing it
    killed tests/checks/test_hardlink_futility.py::test_a_save_path_with_a_parent_segment_is_unknown_not_a_cross_mount_fail
    killed tests/checks/test_hardlink_futility.py::test_a_root_folder_with_a_parent_segment_is_unknown
    killed tests/checks/test_paths.py::test_a_parent_segment_makes_a_path_unplaceable
    killed tests/checks/test_paths.py::test_the_reason_names_the_parent_segment_and_passes_a_good_path_through
    killed tests/checks/test_paths.py::test_a_mount_with_a_parent_segment_is_not_a_mount
  5 failed, 2840 passed, 40 deselected in 35.34s
M3: test .. as a substring rather than as a path component
    killed tests/checks/test_paths.py::test_a_component_that_merely_starts_with_dots_is_a_real_directory
  1 failed, 2844 passed, 40 deselected in 33.89s
M4: swap the reason text for the generic one
    killed tests/checks/test_hardlink_futility.py::test_a_save_path_with_a_parent_segment_is_unknown_not_a_cross_mount_fail
    killed tests/checks/test_hardlink_futility.py::test_a_root_folder_with_a_parent_segment_is_unknown
    killed tests/checks/test_paths.py::test_the_reason_names_the_parent_segment_and_passes_a_good_path_through
  3 failed, 2842 passed, 40 deselected in 33.24s
control again
  2845 passed, 40 deselected in 33.05s
```

Every mutant dies, the control is green either side, and the one that only
changes wording (M4) dies on the wording while the one that only widens the
test to a substring (M3) dies on `/data/..hidden/x`, a real directory name.

## The gate

```bash
uv run --frozen -q ruff check src tests && uv run --frozen -q ruff format --check src tests && uv run --frozen -q pytest -q 2>&1 | tail -1
rm -rf /tmp/lintarr60
```

```output
All checks passed!
130 files already formatted
2845 passed, 40 deselected in 32.32s
```

Real arr and qBittorrent paths do not contain `..`, so no homelab finding
changes: the homelab fixtures still PASS in the run above. What changes is that
a path lintarr cannot place now reads as unknown instead of as a verdict.
