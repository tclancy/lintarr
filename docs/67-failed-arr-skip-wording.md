# The failed-arr SKIP now says what each check could not do (lintarr#67)

*2026-11-21T15:21:34Z by Showboat 0.6.1*
<!-- showboat-id: 87ec20d2-6ca7-4fe6-b12b-07adf2342d6c -->

When an arr fails to collect, no check that needs arr data can be trusted, so
`run._resolve_failed_arr` turns that check's PASS or N/A into a SKIP and writes
the sentence an operator reads. It picked that sentence by asking which *table*
the invariant id came out of: in `checks.SHARED_ARR_DEPENDENT` meant "an arr
could not be read, so its **imports** could not be checked" — which is
`hardlink-futility`'s sentence — and anything else got "...its **downloads**
could not be checked", which is `orphan-inevitability`'s.

That is a property of the registry, not of the check. There is one shared
arr-dependent check today, so the guess has been right by accident; the second
one inherits a sentence about imports whether or not it checks imports.

Separately, the two tables were merged with `backend.arr_dependent |
SHARED_ARR_DEPENDENT`, so a backend naming an id the shared checks already own
lost its own premise and wording without a word.

The fix: the mapping value is an `ArrDependency(premise, detail)`, so each entry
carries its own sentence and `run` stops guessing — and the merge refuses a
clash instead of picking a winner. Both shipped checks keep their exact bytes.

## Setup

Everything below runs against the stub arr stack from lintarr#61
(`docs/61-stub-stack.py`): one qBittorrent 5.2.3 serving the bodies
`tests/fixtures/homelab_paths.py` measured on Tom's homelab on 2026-11-19. The
one change for this issue is that `ARR_URL` points at a closed port, so the arr
fails to collect and every arr-dependent check has to say so.

`before/` is a clean extract of the commit this branch was cut from, so the two
columns are the same stack read by two versions of lintarr.

```bash
REPO="$(git rev-parse --show-toplevel)"
DEMO=/tmp/lintarr67-demo
rm -rf "$DEMO" && mkdir -p "$DEMO/before"
BASE=e3383d3
git -C "$REPO" log --oneline -1 "$BASE"
git -C "$REPO" archive "$BASE" | tar -x -C "$DEMO/before"

# One run, end to end: start the stub on a free port, point a checkout's lintarr
# at it over real HTTP with a dead ARR_URL, print what it reported, stop the stub.
cat > "$DEMO/case.sh" <<'RUNNER'
set -uo pipefail
STUB_PY=$1; CHECKOUT=$2
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
DEAD=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
python3 "$STUB_PY" "$PORT" 3 Default >/dev/null 2>&1 &
STUB=$!
trap 'kill $STUB 2>/dev/null' EXIT
until curl -fs "http://127.0.0.1:$PORT/api/v2/app/version" >/dev/null; do sleep 0.1; done
cd "$CHECKOUT"
OUT=$(DOWNLOAD_CLIENT_KIND=qbittorrent DOWNLOAD_CLIENT_URL="http://127.0.0.1:$PORT" \
  DOWNLOAD_CLIENT_USER=admin DOWNLOAD_CLIENT_PASS=hunter2 \
  ARR_URL="http://127.0.0.1:$DEAD" ARR_API_KEY=sonarrkey \
  uv run --quiet lintarr check 2>/dev/null)
RC=$?
printf '%s\n' "$OUT"
echo "lintarr check exit code: $RC"
RUNNER
echo "runner written to $DEMO/case.sh"

```

```output
e3383d3 Merge pull request #74 from tclancy/claude/61-queue-liveness-action-route-ignores-per
runner written to /tmp/lintarr67-demo/case.sh
```

## What an operator sees, before and after

The two shipped arr-dependent checks each name what they could not do. This is
the output the fix has to leave alone, byte for byte: these sentences are what a
nightly run pages on, and `hardlink-futility`'s is the one the old code got
right by accident.

```bash
# before: the commit this branch was cut from
bash /tmp/lintarr67-demo/case.sh "$(git rev-parse --show-toplevel)/docs/61-stub-stack.py" /tmp/lintarr67-demo/before

```

```output
SKIP  queue-liveness  [qbittorrent[main]]

  Could not read:
    arr.indexer_without_seed_criteria    unknown
  required inputs could not be read

SKIP  orphan-inevitability  [qbittorrent[main]]

  Could not read:
    arr.limit_fires_and_nothing_reclaims unknown
  an arr could not be read, so its downloads could not be checked

SKIP  hardlink-futility  [qbittorrent[main]]

  Could not read:
    arr.hardlink_import_crosses_a_mount  unknown
  an arr could not be read, so its imports could not be checked

ERROR collect  [arr[main]]
  could not read this service: unreachable. /api/v3/system/status: ConnectError

4 checked: 1 ERROR, 3 SKIP
lintarr check exit code: 2
```

```bash
# after: this branch
bash /tmp/lintarr67-demo/case.sh "$(git rev-parse --show-toplevel)/docs/61-stub-stack.py" "$(git rev-parse --show-toplevel)"

```

```output
SKIP  queue-liveness  [qbittorrent[main]]

  Could not read:
    arr.indexer_without_seed_criteria    unknown
  required inputs could not be read

SKIP  orphan-inevitability  [qbittorrent[main]]

  Could not read:
    arr.limit_fires_and_nothing_reclaims unknown
  an arr could not be read, so its downloads could not be checked

SKIP  hardlink-futility  [qbittorrent[main]]

  Could not read:
    arr.hardlink_import_crosses_a_mount  unknown
  an arr could not be read, so its imports could not be checked

ERROR collect  [arr[main]]
  could not read this service: unreachable. /api/v3/system/status: ConnectError

4 checked: 1 ERROR, 3 SKIP
lintarr check exit code: 2
```

```bash
# the same two runs, diffed: no byte of the shipped output moved
REPO="$(git rev-parse --show-toplevel)"
bash /tmp/lintarr67-demo/case.sh "$REPO/docs/61-stub-stack.py" /tmp/lintarr67-demo/before > /tmp/lintarr67-demo/out.before
bash /tmp/lintarr67-demo/case.sh "$REPO/docs/61-stub-stack.py" "$REPO" > /tmp/lintarr67-demo/out.after
diff /tmp/lintarr67-demo/out.before /tmp/lintarr67-demo/out.after && echo "identical: $(wc -l < /tmp/lintarr67-demo/out.after) lines, no diff"

```

```output
identical:       23 lines, no diff
```

## The bug: a second shared check, and a clashing id

Nothing in the tree can show either defect, because there is exactly one shared
arr-dependent check and no backend reuses its id. `docs/67-arr-dependent-probe.py`
registers both: a second shared check called `ratio-sum`, declared with a
sentence about **seeding ratios**, and a backend whose `arr_dependent` names
`hardlink-futility`. It builds whichever mapping value the checkout it runs in
understands, so the same probe runs on both.

```bash
# before: the commit this branch was cut from
REPO="$(git rev-parse --show-toplevel)"
cd /tmp/lintarr67-demo/before && uv run --quiet python "$REPO/docs/67-arr-dependent-probe.py"

```

```output
A. a second shared arr-dependent check, beside the one that shipped
  SKIP  hardlink-futility     arr.hardlink_import_crosses_a_mount    an arr could not be read, so its imports could not be checked
  SKIP  ratio-sum             arr.ratio_sum_is_reachable             an arr could not be read, so its imports could not be checked
  ratio-sum registered with: 'an arr could not be read, so its seeding ratios could not be checked'

B. a backend whose arr_dependent reuses the shared id 'hardlink-futility'
  SKIP  hardlink-futility     arr.hardlink_import_crosses_a_mount    an arr could not be read, so its imports could not be checked
  no error: the clash was merged silently
```

`ratio-sum` was registered with a sentence about seeding ratios and reported one
about imports — the wording came from the table, not from the check. And the
clashing backend's own premise (`qbt.backend_premise`) and sentence never
reached the finding at all: the merge took the shared entry and said nothing.

On this branch, each entry's own sentence survives, and the clash is refused
where it is made rather than papered over in the output:

```bash
# after: this branch
uv run --quiet python docs/67-arr-dependent-probe.py

```

```output
A. a second shared arr-dependent check, beside the one that shipped
  SKIP  hardlink-futility     arr.hardlink_import_crosses_a_mount    an arr could not be read, so its imports could not be checked
  SKIP  ratio-sum             arr.ratio_sum_is_reachable             an arr could not be read, so its seeding ratios could not be checked
  ratio-sum registered with: 'an arr could not be read, so its seeding ratios could not be checked'

B. a backend whose arr_dependent reuses the shared id 'hardlink-futility'
  ValueError: backend 'qbittorrent' reuses shared invariant id(s) in arr_dependent: hardlink-futility
```

## The tests that hold it there

Three new tests: the second shared check keeps its own wording (and the two
shipped sentences stay put beside it), a backend that reuses a shared id is
refused, and the registry's two tables are disjoint. The fourth below is the
existing backend test, which now registers a sentence of its own and asserts it
comes back.

```bash
uv run --frozen pytest --no-header -v \
  -k "second_shared or redeclare or reuses_a_shared or arr_dependent_check" 2>&1 \
  | grep -E "PASSED|FAILED|ERROR|passed|failed" \
  | sed -E 's/ \[ *[0-9]+%\]//; s/ in [0-9.]+s//'

```

```output
tests/clients/test_registry.py::test_no_backend_reuses_a_shared_arr_dependent_invariant_id PASSED
tests/test_run.py::test_a_backend_arr_dependent_check_gets_the_failed_arr_skip PASSED
tests/test_run.py::test_a_second_shared_arr_dependent_check_keeps_its_own_wording PASSED
tests/test_run.py::test_a_backend_may_not_redeclare_a_shared_arr_dependent_invariant PASSED
====================== 4 passed, 2956 deselected ======================
```

## Mutation round

Watching those four pass proves nothing on its own: all four assert on prose,
and prose assertions are the easiest kind to write so they can never fail. So
break the guarded thing five ways and check the suite goes red every time.
`docs/67-mutation-round.py` runs the control between every mutant and restores
each file in a `finally`.

```bash
uv run --quiet --frozen python docs/67-mutation-round.py "$(git rev-parse --show-toplevel)"

```

```output
CONTROL (no mutation)                            GREEN  110 passed
SKIP detail hardcoded back to the shared wording KILLED 3 failed, 107 passed
SKIP premise taken from the detail instead       KILLED 4 failed, 106 passed
clash merged silently again (raise removed)      KILLED 1 failed, 109 passed
hardlink-futility's sentence reworded            KILLED 2 failed, 108 passed
orphan-inevitability's sentence reworded         KILLED 3 failed, 107 passed

survivors: none
```

## The whole suite

Nothing else moved: 2917 tests before, 2920 after, and the 40 deselected are the
container conformance tests that need Docker.

```bash
uv run --quiet --frozen pytest -q 2>&1 | tail -1 | sed -E 's/ in [0-9.]+s//'

```

```output
2920 passed, 40 deselected
```

## What this leaves for the next check

`Backend.arr_dependent` and `checks.SHARED_ARR_DEPENDENT` now take an
`ArrDependency(premise, detail)`, and `docs/adding-a-backend.md` says so. A new
arr-dependent check writes its own sentence at its registration site, beside the
premise its SKIP carries, and may not reuse an id a shared check already owns.
