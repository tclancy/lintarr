# queue-liveness: a category's own share-limit action (lintarr#61)

*2026-11-21T13:05:38Z by Showboat 0.6.1*
<!-- showboat-id: 2c615899-267b-4063-8dbf-5bf19748bda5 -->

`queue-liveness`'s `ACTION` route asks one question: when a torrent reaches a
share limit, does qBittorrent actually free its active slot? It answered from
the global `max_ratio_act` alone. But `effectiveShareLimitAction()` asks the
torrent, then its **category**, then the global — and a qBittorrent 5.x category
carries its own `share_limit_action`. A category that names an action decides
for its own torrents, and the global never runs for them.

So a category set to `EnableSuperSeeding` beside a global `Stop` was a PASS on a
stack where that category's seeders hold their slots forever.
`orphan-inevitability` (lintarr#58) already modelled this through
`share_limits.effective_action`; the flagship check could not reach that
function, because it lived *downstream* of `queue_liveness`. This change moves
it up and has the `ACTION` route ask it once per route — uncategorised torrents,
plus each named category.

Everything below runs against a stub arr stack serving the bodies
`tests/fixtures/homelab_paths.py` measured on Tom's homelab on 2026-11-19:
qBittorrent 5.2.3 with `tv` and `movies` categories, and one Sonarr with a
torrent indexer that sets real seed goals. Only two settings change between
runs — the global action, and `tv`'s own.

```bash
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"
DEMO=/tmp/lintarr61-demo
mkdir -p "$DEMO"

# A checkout of the commit this branch was cut from, for the "before" column.
[ -d "$DEMO/origin-main" ] || git worktree add -f --detach "$DEMO/origin-main" 6c436c3 >/dev/null 2>&1
git -C "$DEMO/origin-main" log --oneline -1

# One scenario, end to end: start the stub stack on a free port, point that
# checkout's lintarr at it over real HTTP, print what it reported, stop the stub.
cat > "$DEMO/case.sh" <<'RUNNER'
set -uo pipefail
STUB_PY=$1; CHECKOUT=$2; GLOBAL_ACTION=$3; TV_ACTION=$4; BRIEF=${5:-}
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
python3 "$STUB_PY" "$PORT" "$GLOBAL_ACTION" "$TV_ACTION" >/dev/null 2>&1 &
STUB=$!
trap 'kill $STUB 2>/dev/null' EXIT
until curl -fs "http://127.0.0.1:$PORT/api/v2/app/version" >/dev/null; do sleep 0.1; done
cd "$CHECKOUT"
OUT=$(DOWNLOAD_CLIENT_KIND=qbittorrent DOWNLOAD_CLIENT_URL="http://127.0.0.1:$PORT" \
  DOWNLOAD_CLIENT_USER=admin DOWNLOAD_CLIENT_PASS=hunter2 \
  ARR_URL="http://127.0.0.1:$PORT" ARR_API_KEY=sonarrkey \
  uv run --quiet lintarr check 2>/dev/null)
RC=$?
[ "$BRIEF" = "--brief" ] && OUT=$(printf '%s\n' "$OUT" | awk '/^(PASS|FAIL|SKIP|N\/A|ERROR)  /{n++} n<2')
printf '%s\n' "$OUT"
echo "lintarr check exit code: $RC"
RUNNER
echo "runner written to $DEMO/case.sh"
```

```output
6c436c3 Merge pull request #71 from tclancy/claude/70-pre-push-gate-is-red-on-python-3-14-10-t
runner written to /tmp/lintarr61-demo/case.sh
```

## Scenario 1: the false PASS

Global share-limit action **Stop**, which frees the slot. Category `tv`
overridden to **EnableSuperSeeding**, which does not. Every torrent Sonarr
grabs lands in `tv`, reaches its ratio goal, and keeps its active slot forever
— homelab#393's shape, arriving through the one setting the `ACTION` route did
not read.

On `origin/main`, `queue-liveness` says the queue is fine. Read it next to the
`orphan-inevitability` finding directly underneath, which names the exact
setting on the exact category:

```bash
bash /tmp/lintarr61-demo/case.sh "$(git rev-parse --show-toplevel)/docs/61-stub-stack.py" /tmp/lintarr61-demo/origin-main 0 EnableSuperSeeding
```

```output
PASS  queue-liveness  [qbittorrent[main]]

FAIL  orphan-inevitability  [qbittorrent[main]]

  What lintarr read from your stack — check these yourself:
    arr.limit_fires_and_nothing_reclaims holds
  sonarr[main] (category tv): a limit can fire; action is EnableSuperSeeding, which removes nothing

  Therefore: torrents reach their seed limit and then keep their files forever. qBittorrent stops or super-seeds them, and the arr either isn't set to remove completed downloads, can't see them after a post-import category, or doesn't recognise the limit that stopped them. Use 'Remove torrent and its files', or the Stop action with Remove Completed on, global or indexer seed limits, and no post-import category.

PASS  hardlink-futility  [qbittorrent[main]]
  downloads and library folders share a mount for sonarr[main]

3 checked: 1 FAIL, 2 PASS
lintarr check exit code: 1
```

On this branch, same stub, same two settings:

```bash
bash /tmp/lintarr61-demo/case.sh "$(git rev-parse --show-toplevel)/docs/61-stub-stack.py" "$(git rev-parse --show-toplevel)" 0 EnableSuperSeeding
```

```output
FAIL  queue-liveness  [qbittorrent[main]]

  What lintarr read from your stack — check these yourself:
    qbt.queueing_enabled                 holds
    qbt.max_active_torrents_binds        holds
    qbt.slow_exempt_off                  holds
    qbt.share_limit_action_frees_no_slot holds
  the share-limit action frees no slot for category tv

  Therefore: a share-limit action is set to something that does not free
  the slot. qBittorrent consults the action only AFTER a limit is
  reached, so this defeats the ratio, seeding-time and
  inactive-seeding-time limits at once and every limit you have set is
  decorative. Super seeding keeps the torrent running; so does any
  value matching no action branch. Set it to Stop, Remove, or
  Remove-with-content — in Options > BitTorrent for the global one,
  and on the category itself for any category named above, which
  overrides the global for its own torrents.

FAIL  orphan-inevitability  [qbittorrent[main]]

  What lintarr read from your stack — check these yourself:
    arr.limit_fires_and_nothing_reclaims holds
  sonarr[main] (category tv): a limit can fire; action is EnableSuperSeeding, which removes nothing

  Therefore: torrents reach their seed limit and then keep their files forever. qBittorrent stops or super-seeds them, and the arr either isn't set to remove completed downloads, can't see them after a post-import category, or doesn't recognise the limit that stopped them. Use 'Remove torrent and its files', or the Stop action with Remove Completed on, global or indexer seed limits, and no post-import category.

PASS  hardlink-futility  [qbittorrent[main]]
  downloads and library folders share a mount for sonarr[main]

3 checked: 2 FAIL, 1 PASS
lintarr check exit code: 1
```

## Scenario 2: the mirror, which stays a FAIL

Now the other way round — global **EnableSuperSeeding**, category `tv` set to
**Stop**. The issue calls this a possible false FAIL, and the verdict does not
move: a torrent with *no* category always takes the global route, so the stack
can still wedge however well `tv` is configured. What was wrong was the
sentence, not the verdict. `origin/main` named no route at all, so an operator
who had already fixed `tv` had nothing to tell them which torrents the FAIL was
about:

```bash
bash /tmp/lintarr61-demo/case.sh "$(git rev-parse --show-toplevel)/docs/61-stub-stack.py" /tmp/lintarr61-demo/origin-main 2 Stop --brief
```

```output
FAIL  queue-liveness  [qbittorrent[main]]

  What lintarr read from your stack — check these yourself:
    qbt.queueing_enabled                 holds
    qbt.max_active_torrents_binds        holds
    qbt.slow_exempt_off                  holds
    qbt.share_limit_action_frees_no_slot holds

  Therefore: your share-limit action is set to something that does not free
  the slot. qBittorrent consults the action only AFTER a limit is
  reached, so this defeats the ratio, seeding-time and
  inactive-seeding-time limits at once and every limit you have set is
  decorative. Super seeding keeps the torrent running; so does any
  value matching no action branch. Set qBittorrent's share-limit
  action to Stop, Remove, or Remove-with-content.
lintarr check exit code: 1
```

## Scenario 2: the mirror, which stays a FAIL

Now the other way round — global **EnableSuperSeeding**, category `tv` set to
**Stop**. The issue calls this a possible false FAIL, and the verdict does not
move: a torrent with *no* category always takes the global route, so the stack
can still wedge however well `tv` is configured. What was wrong was the
sentence, not the verdict. `origin/main` named no route at all, so an operator
who had already fixed `tv` had nothing to tell them which torrents the FAIL was
about:

```bash
bash /tmp/lintarr61-demo/case.sh "$(git rev-parse --show-toplevel)/docs/61-stub-stack.py" /tmp/lintarr61-demo/origin-main 2 Stop --brief
```

```output
FAIL  queue-liveness  [qbittorrent[main]]

  What lintarr read from your stack — check these yourself:
    qbt.queueing_enabled                 holds
    qbt.max_active_torrents_binds        holds
    qbt.slow_exempt_off                  holds
    qbt.share_limit_action_frees_no_slot holds

  Therefore: your share-limit action is set to something that does not free
  the slot. qBittorrent consults the action only AFTER a limit is
  reached, so this defeats the ratio, seeding-time and
  inactive-seeding-time limits at once and every limit you have set is
  decorative. Super seeding keeps the torrent running; so does any
  value matching no action branch. Set qBittorrent's share-limit
  action to Stop, Remove, or Remove-with-content.
lintarr check exit code: 1
```

On this branch the same FAIL says which torrents it is about, and the remedy tells the global setting apart from a category's own. `movies` is on `Default`, so it is covered by the global route rather than accused separately:

```bash
bash /tmp/lintarr61-demo/case.sh "$(git rev-parse --show-toplevel)/docs/61-stub-stack.py" "$(git rev-parse --show-toplevel)" 2 Stop --brief
```

```output
FAIL  queue-liveness  [qbittorrent[main]]

  What lintarr read from your stack — check these yourself:
    qbt.queueing_enabled                 holds
    qbt.max_active_torrents_binds        holds
    qbt.slow_exempt_off                  holds
    qbt.share_limit_action_frees_no_slot holds
  the share-limit action frees no slot for uncategorised torrents and every category that defers to the global

  Therefore: a share-limit action is set to something that does not free
  the slot. qBittorrent consults the action only AFTER a limit is
  reached, so this defeats the ratio, seeding-time and
  inactive-seeding-time limits at once and every limit you have set is
  decorative. Super seeding keeps the torrent running; so does any
  value matching no action branch. Set it to Stop, Remove, or
  Remove-with-content — in Options > BitTorrent for the global one,
  and on the category itself for any category named above, which
  overrides the global for its own torrents.
lintarr check exit code: 1
```

## Every cell, before and after

The whole truth table: the global action down the side, `tv`'s own action
across the top, on preferences every other route leaves at PASS so each cell is
the `ACTION` route's own verdict. `docs/61-action-matrix.py` builds its facts
from `lintarr.models` and not from a test fixture, so the same file runs
unchanged against the older checkout.

```bash
REPO="$(git rev-parse --show-toplevel)"
echo "===== BEFORE: origin/main 6c436c3 ====="
(cd /tmp/lintarr61-demo/origin-main && uv run --quiet python "$REPO/docs/61-action-matrix.py" 2>/dev/null)
echo
echo "===== AFTER: this branch ====="
(cd "$REPO" && uv run --quiet python docs/61-action-matrix.py 2>/dev/null)
```

```output
===== BEFORE: origin/main 6c436c3 =====
global action                        Default                Stop              Remove   RemoveWithContent  EnableSuperSeeding
----------------------------------------------------------------------------------------------------------------------------
Stop (0)                                PASS                PASS                PASS                PASS                PASS
Remove (1)                              PASS                PASS                PASS                PASS                PASS
RemoveWithContent (3)                   PASS                PASS                PASS                PASS                PASS
EnableSuperSeeding (2)                  FAIL                FAIL                FAIL                FAIL                FAIL

===== AFTER: this branch =====
global action                        Default                Stop              Remove   RemoveWithContent  EnableSuperSeeding
----------------------------------------------------------------------------------------------------------------------------
Stop (0)                                PASS                PASS                PASS                PASS                FAIL
Remove (1)                              PASS                PASS                PASS                PASS                FAIL
RemoveWithContent (3)                   PASS                PASS                PASS                PASS                FAIL
EnableSuperSeeding (2)                  FAIL                FAIL                FAIL                FAIL                FAIL
```

Three cells move, all of them PASS to FAIL, and all three in the
`EnableSuperSeeding` column. The `Default` column — every stack that has never
touched a category's action, which is the common case and the measured homelab
one — is untouched, and so is the bottom row the route already caught.

## The pin

`queue-liveness` is the flagship check, so the behaviour that must *not* move is
pinned before the behaviour that must. `_CATEGORY_DEFERS` crosses four ways a
category can decline to decide — the map is empty, the key is absent, the value
is the literal `Default`, or the category carries other keys and not this one —
with the whole global action truth table, and asserts the verdict against the
same `_ACTIONS` table the global-only test drives, so the two cannot drift.

```bash
uv run --quiet pytest tests/invariants/test_share_limit_action.py -q --no-header -p no:randomly 2>/dev/null | tail -3; uv run --quiet pytest tests/invariants/test_share_limit_action.py --collect-only -q 2>/dev/null | grep -c "test_a_category_that_names_no_action_still_follows_the_global" | xargs -I{} echo "{} pinned (global action x category-defers) combinations"
```

```output
........................................................................ [ 98%]
.                                                                        [100%]
73 passed in 0.09s
24 pinned (global action x category-defers) combinations
```

## The mutation round

Green tests prove nothing until the thing they guard is broken and they go red.
Five mutants, run against the whole suite with the baseline restored between
each: the pre-#61 body, the two naming filters, the route-set, and the null-map
reading. Every one is killed, and the pin tests stay green under the first —
which is what makes them a pin and not a restatement.

```bash
cd "$(git rev-parse --show-toplevel)"
TARGET=src/lintarr/clients/qbittorrent/queue_liveness.py
cp "$TARGET" /tmp/lintarr61-demo/baseline.py
mutate() {
  python3 - "$1" "$2" <<'PY'
import sys
from pathlib import Path
p = Path("src/lintarr/clients/qbittorrent/queue_liveness.py")
t = p.read_text()
assert sys.argv[1] in t, "mutation target not found"
p.write_text(t.replace(sys.argv[1], sys.argv[2], 1))
PY
  printf '%-52s ' "$3"
  uv run --quiet pytest -q -x 2>/dev/null | tail -1 | sed 's/ in .*//'
  cp /tmp/lintarr61-demo/baseline.py "$TARGET"
}
mutate 'routes = _category_routes(qbt)
    if routes is None:
        # Which categories exist is unknown, so one of them may free nothing —
        # unless the global route has already settled it.
        return _any_of((_frees_no_slot_in(qbt, UNCATEGORISED), None))
    return _any_of(_frees_no_slot_in(qbt, c) for c in (UNCATEGORISED, *routes))' \
       'return _frees_no_slot_in(qbt, UNCATEGORISED)' \
       'the pre-#61 body: read only the global'
mutate 'return _any_of((_frees_no_slot_in(qbt, UNCATEGORISED), None))' \
       'return _frees_no_slot_in(qbt, UNCATEGORISED)' \
       'an unread category map adds no routes'
mutate 'for c in (UNCATEGORISED, *routes))' 'for c in routes or (UNCATEGORISED,))' \
       'drop the global route when categories exist'
mutate 'own = tuple(c for c in _category_routes(qbt) or () if _names_its_own_action(qbt, c))' \
       'own = tuple(c for c in _category_routes(qbt) or ())' \
       'name categories that only defer to the global'
mutate 'if _frees_no_slot_in(qbt, c) is True' 'if _frees_no_slot_in(qbt, c) is not False' \
       'name routes nobody could read'
echo
echo "control (baseline restored):"
printf '%-52s ' ''
uv run --quiet pytest -q 2>/dev/null | tail -1 | sed 's/ in .*//'
```

```output
the pre-#61 body: read only the global               1 failed, 705 passed, 40 deselected
an unread category map adds no routes                1 failed, 793 passed, 40 deselected
drop the global route when categories exist          1 failed, 761 passed, 40 deselected
name categories that only defer to the global        1 failed, 789 passed, 40 deselected
name routes nobody could read                        2901 passed, 40 deselected

control (baseline restored):
                                                     2901 passed, 40 deselected
```

Four killed, and the fifth is reported as it is rather than quietly dropped.
**"name routes nobody could read" survives, and it is an equivalent mutant**:
after `_names_its_own_action` filters the list, every category left in it has
an action that maps to an integer, so `_frees_no_slot_in` cannot return `None`
for one — and the global route can only be unknown on a verdict that is
already a SKIP, where `_note_action_routes` returns before reaching this line.
So no input distinguishes `is True` from `is not False` here. The `is True`
spelling stays because it is the correct one for a three-valued function and
the next filter added above it may well make the difference reachable; it is
recorded as unpinned rather than claimed as tested.

The same round found a sixth mutant that the suite *did* let through at first:
resolving an unreadable category map to "no extra routes" changed no verdict,
because `SEEDING` also SKIPs on that input and is earlier in `_worst_of`. That
one is now pinned on the predicate directly, in
`test_an_unreadable_category_map_leaves_the_action_premise_unknown`, with the
reason in its docstring.

## The gate

```bash
cd "$(git rev-parse --show-toplevel)"
TARGETS="src tests docs/61-stub-stack.py docs/61-action-matrix.py"
uv run --quiet ruff check $TARGETS 2>/dev/null
uv run --quiet ruff format --check $TARGETS 2>/dev/null | tail -1
uv run --quiet pytest -q 2>/dev/null | tail -1
```

```output
All checks passed!
132 files already formatted
2901 passed, 40 deselected in 32.94s
```

2839 tests before this change, 2901 after.

## What is left open

- **A global action that frees no slot is still a FAIL on the whole client**,
  even when every category in use overrides it to Stop. Narrowing that needs
  the arr-side fact of which categories torrents actually land in —
  `orphan-inevitability` has it, through `arr_clients.clients_that_are`, but
  `queue_liveness.check` takes a bare `QbtInstance` and no `ClientInstance`,
  so it cannot ask. Scoping the route to arr categories would also change the
  verdict on a stack with no arrs at all, which is a wider blast radius than
  this issue asked for. Left as the next slice.
- **A category action name this release does not know** — anything outside
  `ACTION_BY_NAME` — makes the route SKIP rather than FAIL, which is the
  opposite reading from the one an unmapped *integer* gets. The asymmetry is
  deliberate and is argued in
  `test_an_action_name_this_release_does_not_know_is_unknown_not_a_release`.
- **Parent categories are not followed.** `categoryShareLimitAction()`
  recurses through `parentCategoryName()`; `_category_body` reads a flat map.
  Same known limit `orphan-inevitability` carries, unchanged here.

## Re-running this

    uvx showboat verify docs/61-category-share-limit-action.md

The first block recreates everything it needs under `/tmp/lintarr61-demo`,
including the `origin/main` worktree pinned at 6c436c3, so a clean machine can
reproduce every number above.
