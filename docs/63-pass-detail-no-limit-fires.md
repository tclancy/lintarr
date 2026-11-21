# lintarr #63: a PASS detail that said a limit fires when none can

*2026-11-21T14:19:25Z by Showboat 0.6.1*
<!-- showboat-id: e6ae1511-ced1-46a8-8b9d-a4f85c412ef9 -->

`orphan-inevitability` PASSes a qBittorrent stack where the share-limit action is Stop and the arr has Remove Completed on. Two different things earn that PASS: a limit fires and the arr removes the download, or no limit can fire at all. Both printed the same line — "qBittorrent stops at the limit and the arr removes completed downloads" — so the second case advertised a stop that cannot happen. The verdict was right; the sentence was not. The "no limit can fire; seeding is unlimited" line already existed for the other Stop arms, so this reuses it.

## The change

`4623438` is this branch's base, and the file is unchanged from there to the branch point, so every "before" below is read straight out of git at that commit. Nothing in the tree is swapped.

```bash
# The trailing-whitespace hook would strip git's blank context lines out of
# the captured output below, so strip them here and keep `verify` honest.
git diff 4623438 -- src/lintarr/clients/qbittorrent/orphan_inevitability.py \
  | sed -E 's/[[:space:]]+$//'

```

```output
diff --git a/src/lintarr/clients/qbittorrent/orphan_inevitability.py b/src/lintarr/clients/qbittorrent/orphan_inevitability.py
index b746a73..c039453 100644
--- a/src/lintarr/clients/qbittorrent/orphan_inevitability.py
+++ b/src/lintarr/clients/qbittorrent/orphan_inevitability.py
@@ -139,15 +139,28 @@ def _stop_without_arr_removal(qbt: QbtInstance, arr: ArrInstance, pair: _Pair) -
     return _fires(pair, NOTHING_RECLAIMS, can_fire(qbt, arr, pair.category), why)


+def _arr_reclaims(qbt: QbtInstance, arr: ArrInstance, pair: _Pair) -> Verdict:
+    """No unrecognised limit fires: say which of the two reasons settled it.
+
+    Either no limit can fire at all, or one can and it is a limit the arr
+    recognises. Both are PASS, but only the second involves a limit firing, so
+    the first borrows the unlimited-seeding line rather than claiming a stop
+    that cannot happen.
+    """
+    if can_fire(qbt, arr, pair.category) is False:
+        return _fires(pair, NOTHING_RECLAIMS, False, "")
+    return Verdict(
+        False,
+        (NOTHING_RECLAIMS,),
+        f"{pair.label}: qBittorrent stops at the limit and the arr removes completed downloads",
+    )
+
+
 def _stop_with_arr_removal(qbt: QbtInstance, arr: ArrInstance, pair: _Pair) -> Verdict:
     """Only a category's own limit the global does not cover leaves files."""
     fires = can_fire_unrecognised(qbt, arr, pair.category)
     if fires is False:
-        return Verdict(
-            False,
-            (NOTHING_RECLAIMS,),
-            f"{pair.label}: qBittorrent stops at the limit and the arr removes completed downloads",
-        )
+        return _arr_reclaims(qbt, arr, pair)
     why = (
         f"action is Stop and the limit that fires is category {pair.category}'s own, "
         "which the arr does not check"
```

## The two stacks, before and after

Both are the homelab qBittorrent with the global share-limit action set to Stop and sonarr's Remove Completed on. The only difference is whether any global seed limit is armed. The second stack is the homelab's real ratio 1.5 and 20160-minute seeding time; the first turns all three arms off. Each finding is rendered by the CLI's own `_render_findings`, so these are the lines `lintarr check` prints.

```bash
uv run --frozen python - <<'PY' 2>&1 | grep -v VIRTUAL_ENV
"""Two Stop + Remove Completed stacks, rendered by the check on 4623438 and by the fix."""

import importlib.util
import subprocess
import tempfile
from pathlib import Path

from lintarr.cli import _render_findings
from lintarr.clients.qbittorrent.orphan_inevitability import check
from lintarr.facts import is_known
from tests.clients.test_orphan_inevitability import GLOBALS_OFF, _arr, _qbt

BASE = "4623438"
MODULE = "src/lintarr/clients/qbittorrent/orphan_inevitability.py"


def base_check(tmp):
    """The check as it stands at BASE, loaded from git without touching the tree."""
    source = subprocess.run(
        ["git", "show", f"{BASE}:{MODULE}"], text=True, capture_output=True, check=True
    ).stdout
    path = Path(tmp) / "orphan_before.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("orphan_before", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


def arm(flag, value=None):
    """How one global seed limit is set, as the fixture actually holds it."""
    if not (is_known(flag) and flag.value):
        return "off"
    return "on" if value is None else f"on at {value.value}"


def inputs(qbt, arr):
    """The inputs that decide the pair, read back off the fixture."""
    q = qbt.specific
    return (
        f"  action {q.max_ratio_act.value} (Stop); "
        f"global ratio {arm(q.max_ratio_enabled, q.max_ratio)}, "
        f"seeding time {arm(q.max_seeding_time_enabled, q.max_seeding_time)}, "
        f"inactive {arm(q.max_inactive_seeding_time_enabled)}; "
        f"Remove Completed {arr.download_clients.value[0].remove_completed}"
    )


ARR = _arr(remove_completed=True)
STACKS = [
    ("NO seed limit can fire", _qbt(max_ratio_act=0, **GLOBALS_OFF)),
    ("a seed limit DOES fire", _qbt(max_ratio_act=0)),
]

with tempfile.TemporaryDirectory() as tmp:
    for name, qbt in STACKS:
        print(f"=== {name} ===")
        print(inputs(qbt, ARR))
        for label, one in [(BASE, base_check(tmp)), ("fixed", check)]:
            print(f"\n  {label}:")
            for line in _render_findings([one(qbt, (ARR,))]).rstrip().splitlines()[:2]:
                print(f"    {line}")
        print()
PY

```

```output
=== NO seed limit can fire ===
  action 0 (Stop); global ratio off, seeding time off, inactive off; Remove Completed True

  4623438:
    PASS  orphan-inevitability  [qbittorrent[main]]
      sonarr[tv]: qBittorrent stops at the limit and the arr removes completed downloads

  fixed:
    PASS  orphan-inevitability  [qbittorrent[main]]
      sonarr[tv]: no limit can fire; seeding is unlimited

=== a seed limit DOES fire ===
  action 0 (Stop); global ratio on at 1.5, seeding time on at 20160, inactive off; Remove Completed True

  4623438:
    PASS  orphan-inevitability  [qbittorrent[main]]
      sonarr[tv]: qBittorrent stops at the limit and the arr removes completed downloads

  fixed:
    PASS  orphan-inevitability  [qbittorrent[main]]
      sonarr[tv]: qBittorrent stops at the limit and the arr removes completed downloads

```

## Pinned by a pair of tests

One test per reason, so neither line can drift into the other's case. Durations are stripped so this document can be re-verified without diffing on the clock.

```bash
uv run --frozen pytest tests/clients/test_orphan_inevitability.py -v \
  -k "no_limit_at_all or keeps_the_stop_line or global_limits_passes" 2>&1 \
  | grep -E "PASSED|passed" | sed -E 's/ in [0-9.]+s//'

```

```output
tests/clients/test_orphan_inevitability.py::test_stop_with_arr_removal_and_global_limits_passes PASSED [ 33%]
tests/clients/test_orphan_inevitability.py::test_stop_with_arr_removal_and_no_limit_at_all_says_seeding_is_unlimited PASSED [ 66%]
tests/clients/test_orphan_inevitability.py::test_stop_with_arr_removal_keeps_the_stop_line_when_a_limit_does_fire PASSED [100%]
======================= 3 passed, 35 deselected =======================
```

## Both directions of the mapping are load-bearing

Watching a new test pass proves nothing on its own, so each half was mutated and the suite re-run. Both rounds were done by swapping the module in the tree and restoring it afterwards, which is why they are recorded here rather than executed by this document.

**Mutant 1 — the fix removed** (the module restored to 4623438, new tests kept):

    >       assert finding.detail == "sonarr[tv]: no limit can fire; seeding is unlimited"
    E       AssertionError
    E         - sonarr[tv]: no limit can fire; seeding is unlimited
    E         + sonarr[tv]: qBittorrent stops at the limit and the arr removes completed downloads

    FAILED tests/clients/test_orphan_inevitability.py::test_stop_with_arr_removal_and_no_limit_at_all_says_seeding_is_unlimited
    1 failed, 37 passed

**Mutant 2 — the new guard widened** from `can_fire(...) is False` to `is not None`, so the unlimited line is used whenever reachability is readable:

    FAILED tests/clients/test_orphan_inevitability.py::test_stop_with_arr_removal_and_global_limits_passes
    FAILED tests/clients/test_orphan_inevitability.py::test_stop_with_a_category_ratio_the_global_covers_passes
    FAILED tests/clients/test_orphan_inevitability.py::test_stop_with_arr_removal_keeps_the_stop_line_when_a_limit_does_fire
    3 failed, 35 passed

So the tests fail without the fix, and they also fail if the fix over-reaches. No mutant left the suite green.

## The whole gate

```bash
uv run --frozen pytest -q 2>&1 | tail -1 | sed -E 's/ in [0-9.]+s//'
uv run --frozen ruff check src tests 2>&1 | grep -v VIRTUAL_ENV
uv run --frozen ruff format --check src tests 2>&1 | grep -v VIRTUAL_ENV

```

```output
2841 passed, 40 deselected
All checks passed!
130 files already formatted
```

## Not shown

No live homelab run. The homelab's global action is RemoveWithContent, which settles every pair before this line is reachable — its PASS detail is "qBittorrent removes files at the limit" and is unchanged by this fix. The fixtures above are the measured homelab snapshot with the action set to Stop, which is the configuration the line belongs to.
