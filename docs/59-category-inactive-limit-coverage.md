# lintarr #59: a category inactive limit the global already covers

*2026-11-21T12:07:07Z by Showboat 0.6.1*
<!-- showboat-id: c74dca78-c967-43fe-bc27-ff3c72f242ef -->

`orphan-inevitability` asks whether anything removes a finished download's
files. On a stack using **Stop** plus the arr's **Remove Completed**, that turns
on one question: does the arr *recognise* the limit qBittorrent stopped the
torrent at? It reads the torrent's own limit, or — when that is `-2` — the
matching **global** preference. It never reads a category's limits.

So a category's own limit counts when the same-arm global is armed at or below
it. `share_limits` applied that rule to ratio and to seeding time, and
hard-returned `False` for the third arm, because `max_inactive_seeding_time`
was collected only as a flag and never as a value. A category inactive limit
was therefore *always* unrecognised, and a stack that reclaims perfectly well
got a FAIL `nothing-reclaims`.

The scenario below is the measured homelab snapshot with three edits: global
action Stop, the global inactive limit armed at 30 minutes, and category `tv`
stopping its own torrents after 60 minutes of no activity. The arr removes
completed downloads. qBittorrent stops a torrent at 60 minutes idle; the arr
looks at the global, sees 30, and removes it. Nothing is orphaned.

```bash
set -euo pipefail

# The scenario, built from the committed fixtures: `_qbt` is the measured
# homelab qBittorrent, `_arr` the measured sonarr beside it. The global inactive
# VALUE is only passed when the model has a field for it, so this one script
# runs on either side of the fix.
scenario=$(cat <<'PY'
from dataclasses import fields

from lintarr.cli import _render_findings
from lintarr.clients.qbittorrent.orphan_inevitability import check
from lintarr.models import QbtInstance
from tests.clients.test_orphan_inevitability import _arr, _qbt

collected = any(f.name == "max_inactive_seeding_time" for f in fields(QbtInstance))
print(f"global inactive VALUE collected: {collected}\n")

prefs = {"max_ratio_act": 0, "max_inactive_seeding_time_enabled": True}
if collected:
    prefs["max_inactive_seeding_time"] = 30
qbt = _qbt({"tv": {"inactive_seeding_time_limit": 60}}, **prefs)

print(_render_findings([check(qbt, (_arr(remove_completed=True),))]))
PY
)

echo "=== BEFORE — main at 6c436c3, in a throwaway worktree ==="
base=$(mktemp -d)/base
git worktree add -q --detach "$base" 6c436c3
( cd "$base" && printf '%s' "$scenario" | uv run -q --frozen python - )
git worktree remove --force "$base"

echo
echo "=== AFTER — this branch ==="
printf '%s' "$scenario" | uv run -q --frozen python -

```

```output
=== BEFORE — main at 6c436c3, in a throwaway worktree ===
global inactive VALUE collected: False

FAIL  orphan-inevitability  [qbittorrent[main]]

  What lintarr read from your stack — check these yourself:
    arr.limit_fires_and_nothing_reclaims holds
  sonarr[tv] (category tv): a limit can fire; action is Stop and the limit that fires is category tv's own, which the arr does not check

  Therefore: torrents reach their seed limit and then keep their files forever. qBittorrent stops or super-seeds them, and the arr either isn't set to remove completed downloads, can't see them after a post-import category, or doesn't recognise the limit that stopped them. Use 'Remove torrent and its files', or the Stop action with Remove Completed on, global or indexer seed limits, and no post-import category.

1 checked: 1 FAIL

=== AFTER — this branch ===
global inactive VALUE collected: True

PASS  orphan-inevitability  [qbittorrent[main]]
  sonarr[tv]: qBittorrent stops at the limit and the arr removes completed downloads

1 checked: 1 PASS
```

## It is coverage, not a blanket pass

The fix is the same `global <= category` rule the other two arms use, so the
FAIL this check owes is still there. Three neighbouring stacks, one command:
the global armed **below** the category's value (covered, PASS), armed
**above** it (not covered, FAIL), and a global the API would not let us read
(undecidable, SKIP).

```bash
uv run -q --frozen python - <<'PY'
from lintarr.clients.qbittorrent.orphan_inevitability import check
from lintarr.facts import Unknown
from tests.clients.test_orphan_inevitability import _arr, _qbt

CASES = {
    "global armed at 30 min, category stops at 60": 30,
    "global armed at 1440 min, category stops at 60": 1440,
    "global armed, value unreadable (insufficient-permission)": Unknown(
        "insufficient-permission", "max_inactive_seeding_time"
    ),
}

for label, value in CASES.items():
    qbt = _qbt(
        {"tv": {"inactive_seeding_time_limit": 60}},
        max_ratio_act=0,
        max_inactive_seeding_time_enabled=True,
        max_inactive_seeding_time=value,
    )
    f = check(qbt, (_arr(remove_completed=True),))
    print(f"{label}:")
    print(f"  {f.outcome}  {f.detail}")
    for p in f.premises:
        state = "holds" if p.state else "unknown" if p.state is None else "does not hold"
        print(f"  premise {p.label} {state}")
    print()
PY

```

```output
global armed at 30 min, category stops at 60:
  PASS  sonarr[tv]: qBittorrent stops at the limit and the arr removes completed downloads

global armed at 1440 min, category stops at 60:
  FAIL  sonarr[tv] (category tv): a limit can fire; action is Stop and the limit that fires is category tv's own, which the arr does not check
  premise arr.limit_fires_and_nothing_reclaims holds

global armed, value unreadable (insufficient-permission):
  SKIP  sonarr[tv] (category tv): whether a seed limit can fire could not be read
  premise arr.limit_fires_and_nothing_reclaims unknown

```

## The preference pair is now read whole

`appcontroller.cpp` publishes both halves and derives the flag from the value:

    data[u"max_inactive_seeding_time_enabled"_s] = (shareLimits.inactiveSeedingTimeLimit >= 0);
    data[u"max_inactive_seeding_time"_s] = shareLimits.inactiveSeedingTimeLimit;

`setShareLimits` clamps the value with `std::max(..., NO_SEEDING_TIME_LIMIT)`
and that sentinel is `-1`, so the flag alone still answers *can this gate ever
fire* — which is why `queue-liveness` keeps reading the flag by itself and a
pre-4.6 client that reports neither key still decides. The value answers the
new question only, and only when the flag is on. Collected against a mock
transport, through the real adapter:

```bash
uv run -q --frozen python - <<'PY'
from tests.collect.test_qbittorrent_facts import PREFS, _collect

armed = PREFS | {"max_inactive_seeding_time_enabled": True, "max_inactive_seeding_time": 30}
for name, prefs in (("armed at 30 min", armed), ("off", PREFS)):
    qbt, _ = _collect(prefs=prefs)
    print(f"{name}:")
    print(f"  enabled = {qbt.max_inactive_seeding_time_enabled.value}")
    print(f"  value   = {qbt.max_inactive_seeding_time.value}")

pre_46 = {k: v for k, v in PREFS.items() if not k.startswith("max_inactive")}
qbt, _ = _collect(prefs=pre_46)
print("a client that reports neither key (pre-4.6):")
print(f"  enabled = {qbt.max_inactive_seeding_time_enabled}")
print(f"  value   = {qbt.max_inactive_seeding_time}")
PY

```

```output
armed at 30 min:
  enabled = True
  value   = 30
off:
  enabled = False
  value   = -1
a client that reports neither key (pre-4.6):
  enabled = Unknown(reason='field-absent', detail="'max_inactive_seeding_time_enabled' absent from GET /api/v2/app/preferences")
  value   = Unknown(reason='field-absent', detail="'max_inactive_seeding_time' absent from GET /api/v2/app/preferences")
```

## Mutation round: the new tests are the thing holding the fix in place

A test you have only watched pass proves nothing. Each mutant below is applied
to the shipped `_global_inactive_covers`, the **whole** suite is run, and the
file is restored from a backup before the next one. The round refuses to start
on a dirty copy of the file, so it cannot eat uncommitted work.

The third mutant is the interesting one: it weakens `<=` to `<` in the shared
`_global_covers`, and only the two equality cases — one for ratio, the new one
for inactive — notice.

```bash
set -uo pipefail

f=src/lintarr/clients/qbittorrent/share_limits.py
git diff --quiet -- "$f" || { echo "refusing to mutate: $f has uncommitted changes"; exit 1; }
backup=$(mktemp)
cp "$f" "$backup"
trap 'cp "$backup" "$f"; rm -f "$backup"' EXIT

mutate() {
  uv run -q --frozen python - "$f" "$1" "$2" <<'PY'
import sys
path, old, new = sys.argv[1:4]
text = open(path).read()
assert text.count(old) == 1, f"mutation target not unique: {old!r}"
open(path, "w").write(text.replace(old, new))
PY
}

round() {
  cp "$backup" "$f"
  mutate "$1" "$2" || { echo "could not apply mutation"; exit 1; }
  printf '%-52s ' "$3"
  uv run -q --frozen pytest -q 2>&1 | tail -1
}

body=$'    return _global_covers(\n        _global_inactive_reachable(qbt), _as_number(qbt.max_inactive_seeding_time), own\n    )'

round "$body" '    return False' 'mutant 1: back to the hard-coded "never covers"'
round "$body" '    return True' 'mutant 2: "always covers"'
round 'return _can_be_reached(value) and value <= own' \
      'return _can_be_reached(value) and value < own' \
      'mutant 3: the global must be strictly below'

cp "$backup" "$f"
printf '%-52s ' 'control: restored'
uv run -q --frozen pytest -q 2>&1 | tail -1
git diff --quiet -- "$f" && echo && echo "$f restored byte-for-byte"

```

```output
mutant 1: back to the hard-coded "never covers"      7 failed, 2842 passed, 40 deselected in 32.59s
mutant 2: "always covers"                            8 failed, 2841 passed, 40 deselected in 32.79s
mutant 3: the global must be strictly below          2 failed, 2847 passed, 40 deselected in 32.93s
control: restored                                    2849 passed, 40 deselected in 32.21s

src/lintarr/clients/qbittorrent/share_limits.py restored byte-for-byte
```

Which two, for mutant 3:

```bash
set -uo pipefail

f=src/lintarr/clients/qbittorrent/share_limits.py
git diff --quiet -- "$f" || { echo "refusing to mutate: $f has uncommitted changes"; exit 1; }
backup=$(mktemp)
cp "$f" "$backup"
trap 'cp "$backup" "$f"; rm -f "$backup"' EXIT

uv run -q --frozen python - "$f" <<'PY'
import sys
path = sys.argv[1]
old = "return _can_be_reached(value) and value <= own"
text = open(path).read()
assert text.count(old) == 1
open(path, "w").write(text.replace(old, old.replace("<=", "<")))
PY

uv run -q --frozen pytest -q 2>&1 | grep '^FAILED'

```

```output
FAILED tests/clients/test_qbittorrent_share_limits.py::test_a_category_ratio_the_global_covers_is_recognised[2.0]
FAILED tests/clients/test_qbittorrent_share_limits.py::test_a_category_inactive_limit_the_global_covers_is_recognised[60]
```

## The whole local gate, which is also CI's

```bash
uv run --frozen pre-commit run --all-files
```

```output
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
check yaml...............................................................Passed
check for merge conflicts................................................Passed
ruff check...............................................................Passed
ruff format..............................................................Passed
pytest...................................................................Passed
```

## What is still a known limit

The spec's "Global inactive value" item is gone, replaced by a narrower one: a
global inactive limit armed **above** a category's own value still reads as
unrecognised and still FAILs. Sonarr compares `now - torrent.LastActivity`,
which — unlike a ratio or an accumulated seeding time — keeps growing after
qBittorrent stops the torrent, so such a torrent may well cross the global
later and be removed then. Whether a stopped torrent keeps reporting a growing
`last_activity` gap is not something this project has measured, so that case
stays a possible false FAIL rather than a guessed PASS. Measuring it is a
container test, and that is its own slice.
