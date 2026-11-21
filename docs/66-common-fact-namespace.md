# dump-facts: the neutral facts get their own namespace (lintarr#66)

*2026-11-21T15:02:41Z by Showboat 0.6.1*
<!-- showboat-id: ef5aa1a3-bb52-40db-ab39-6d342dcec820 -->

`dump-facts` collects two kinds of fact per download client: the
client-neutral ones every backend reports (`ClientFacts`, today just
`save_paths`), which the shared checks read, and the backend's own
(`QbtInstance`), which only its own checks read. The JSON payload keeps them
apart — the neutral half is nested under `common` — but human mode flattened
both into one dict, `{**common, **instance}`, and printed whatever survived
that merge.

Nothing clashes today, which is exactly why the merge order was never visible.
`ClientFacts.save_paths` against qBittorrent's `save_path` is one letter from
a clash. The fix is to namespace the neutral half in human output too, so the
output says which side of the model each fact came from and no future backend
can take a neutral fact's place.

Everything below runs a real `lintarr dump-facts` over HTTP against the stub
stack in `docs/61-stub-stack.py`, serving the qBittorrent 5.2.3 bodies
measured on Tom's homelab on 2026-11-19.

```bash
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"
DEMO=/tmp/lintarr66-demo
mkdir -p "$DEMO"

# A checkout of the commit this branch was cut from, for the "before" column.
[ -d "$DEMO/origin-main" ] || git worktree add -f --detach "$DEMO/origin-main" e3383d3 >/dev/null 2>&1
git -C "$DEMO/origin-main" log --oneline -1

# One dump-facts run, end to end: start the stub stack on a free port, point
# the given checkout's lintarr at it over real HTTP, print what it reported.
cat > "$DEMO/case.sh" <<'RUNNER'
set -uo pipefail
STUB_PY=$1; CHECKOUT=$2
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
python3 "$STUB_PY" "$PORT" 0 Default >/dev/null 2>&1 &
STUB=$!
trap 'kill $STUB 2>/dev/null' EXIT
until curl -fs "http://127.0.0.1:$PORT/api/v2/app/version" >/dev/null; do sleep 0.1; done
cd "$CHECKOUT"
DOWNLOAD_CLIENT_KIND=qbittorrent DOWNLOAD_CLIENT_URL="http://127.0.0.1:$PORT" \
  DOWNLOAD_CLIENT_USER=admin DOWNLOAD_CLIENT_PASS=hunter2 \
  uv run --quiet lintarr dump-facts 2>/dev/null
RUNNER
echo "runner written to $DEMO/case.sh"
```

```output
e3383d3 Merge pull request #74 from tclancy/claude/61-queue-liveness-action-route-ignores-per
runner written to /tmp/lintarr66-demo/case.sh
```

## Before: two save-path facts, one namespace

On the commit this branch was cut from, the neutral `save_paths` lands in the
same flat list as qBittorrent's own `save_path` and reads as another
qBittorrent preference. The two lines are adjacent and nothing says one of
them is the shared fact the cross-backend checks consume.

```bash
bash /tmp/lintarr66-demo/case.sh "$(git rev-parse --show-toplevel)/docs/61-stub-stack.py" /tmp/lintarr66-demo/origin-main | grep -n "save_path"
```

```output
16:    save_path                    = '/data/torrents' GET /api/v2/app/preferences
17:    save_paths                   = {'': '/data/torrents', 'movies': '/data/torrents/movies', 'tv': '/data/torrents/tv'} GET /api/v2/app/preferences; GET /api/v2/torrents/categories
```

## After: the neutral fact is labelled as one

Same stack, same command, this branch. `common.save_paths` is the fact every
backend reports and the shared checks read; the bare names below it are
qBittorrent's own. The whole client is shown so the one changed line can be
read in context — nothing else about the output moved.

```bash
bash /tmp/lintarr66-demo/case.sh "$(git rev-parse --show-toplevel)/docs/61-stub-stack.py" "$(git rev-parse --show-toplevel)"
```

```output
qbittorrent[main] v5.2.3
    auto_tmm_enabled             = True         GET /api/v2/app/preferences
    categories                   = {'movies': {'download_path': None, 'inactive_seeding_time_limit': -2, 'name': 'movies', 'ratio_limit': -2, 'savePath': '/data/torrents/movies', 'seeding_time_limit': -2, 'share_limit_action': 'Default'}, 'tv': {'download_path': None, 'inactive_seeding_time_limit': -2, 'name': 'tv', 'ratio_limit': -2, 'savePath': '/data/torrents/tv', 'seeding_time_limit': -2, 'share_limit_action': 'Default'}} GET /api/v2/torrents/categories
    common.save_paths            = {'': '/data/torrents', 'movies': '/data/torrents/movies', 'tv': '/data/torrents/tv'} GET /api/v2/app/preferences; GET /api/v2/torrents/categories
    dont_count_slow_torrents     = False        GET /api/v2/app/preferences
    max_active_downloads         = 6            GET /api/v2/app/preferences
    max_active_torrents          = 5            GET /api/v2/app/preferences
    max_active_uploads           = 3            GET /api/v2/app/preferences
    max_inactive_seeding_time    ? UNKNOWN (field-absent)
    max_inactive_seeding_time_enabled = False        GET /api/v2/app/preferences
    max_ratio                    = 1.5          GET /api/v2/app/preferences
    max_ratio_act                = 0            GET /api/v2/app/preferences
    max_ratio_enabled            = True         GET /api/v2/app/preferences
    max_seeding_time             = 20160        GET /api/v2/app/preferences
    max_seeding_time_enabled     = True         GET /api/v2/app/preferences
    queueing_enabled             = True         GET /api/v2/app/preferences
    save_path                    = '/data/torrents' GET /api/v2/app/preferences
    use_category_paths_in_manual_mode = False        GET /api/v2/app/preferences

```

## The defect itself: a fact that was collected and never printed

The label is the visible half. The reportable half needs a *clash*, and no
registered backend has one — qBittorrent's `save_path` is singular. So
`docs/66-collision-probe.py` builds one client by hand whose backend reports
its own `save_paths`, hands it to `_render_human`, and counts the lines that
came back. Two facts went in. The checkout decides how many come out.

```bash
PROBE="$(git rev-parse --show-toplevel)/docs/66-collision-probe.py"
for checkout in /tmp/lintarr66-demo/origin-main "$(git rev-parse --show-toplevel)"; do
  echo "== $(git -C "$checkout" log --oneline -1 --format=%h) $(basename "$checkout") =="
  (cd "$checkout" && uv run --quiet python3 "$PROBE")
done
```

```output
== e3383d3 origin-main ==
fake[main] v1.0.0
    save_paths                   = {'': '/backend'} GET /backend
---
facts collected: 2    fact lines printed: 1
== 3876055 dispatch-job-lintarr-66-1795273010 ==
fake[main] v1.0.0
    common.save_paths            = {'': '/neutral'} GET /neutral
    save_paths                   = {'': '/backend'} GET /backend
---
facts collected: 2    fact lines printed: 2
```

## The tests that hold it there

```bash
uv run --frozen -q pytest -v tests/test_render_human.py tests/test_cli.py -k "neutral or namespace or common or save_paths" --no-header -p no:randomly 2>&1 | grep -E "PASSED|passed"
```

```output
tests/test_render_human.py::test_a_backend_fact_sharing_a_neutral_name_does_not_hide_it PASSED [ 25%]
tests/test_render_human.py::test_the_neutral_fact_is_the_one_carrying_the_namespace PASSED [ 50%]
tests/test_render_human.py::test_the_common_container_is_not_offered_to_the_renderers_as_a_fact PASSED [ 75%]
tests/test_cli.py::test_dump_facts_shows_the_client_save_paths PASSED    [100%]
======================= 4 passed, 27 deselected in 0.10s =======================
```

## Do those tests actually hold the namespace?

Five mutants of `_renderable_facts`, in a throwaway worktree at the fix
commit, with a control run before and after. Each line under a mutant is a
test it killed. M1 is the code this branch replaces; M5 is the other way to
make a clash impossible — drop the neutral facts from human output altogether.

```bash
mut=/tmp/lintarr66-demo/mut
git worktree add -q --detach "$mut" 3876055
src="$mut/src/lintarr/cli.py"
suite() { (cd "$mut" && uv run --frozen -q pytest -q --tb=no 2>&1 \
  | grep -E '^FAILED|passed' | sed -e 's/^FAILED /  killed /' -e 's/^/  /'); }
mutant() {  # name, old, new
  printf '%s\n' "$1"
  python3 -c 'import sys,pathlib
p=pathlib.Path(sys.argv[1]); t=p.read_text(); assert sys.argv[2] in t, "anchor missing"
p.write_text(t.replace(sys.argv[2], sys.argv[3], 1))' "$src" "$2" "$3"
  suite
  (cd "$mut" && git checkout -q -- src/lintarr/cli.py)
}
BODY='    common = {f"{_COMMON_PREFIX}{k}": v for k, v in instance.get("common", {}).items()}
    return common | {k: v for k, v in instance.items() if k != "common"}'
echo 'control, unmutated'; suite
mutant 'M1: the flat merge this branch replaces' "$BODY" \
  '    return {**instance.get("common", {}), **instance}'
mutant 'M2: namespace the backend half instead of the neutral half' "$BODY" \
  '    specific = {f"{_COMMON_PREFIX}{k}": v for k, v in instance.items() if k != "common"}
    return dict(instance.get("common", {})) | specific'
mutant 'M3: spell the namespace differently' \
  '_COMMON_PREFIX = "common."' '_COMMON_PREFIX = "neutral."'
mutant 'M4: leave the common container in the rendered mapping' "$BODY" \
  '    common = {f"{_COMMON_PREFIX}{k}": v for k, v in instance.get("common", {}).items()}
    return common | dict(instance)'
mutant 'M5: drop the neutral facts from human output entirely' "$BODY" \
  '    return {k: v for k, v in instance.items() if k != "common"}'
echo 'control again'; suite
git worktree remove --force "$mut"
```

```output
control, unmutated
  2920 passed, 40 deselected in 37.45s
M1: the flat merge this branch replaces
    killed tests/test_cli.py::test_dump_facts_shows_the_client_save_paths - Asser...
    killed tests/test_render_human.py::test_a_backend_fact_sharing_a_neutral_name_does_not_hide_it
    killed tests/test_render_human.py::test_the_neutral_fact_is_the_one_carrying_the_namespace
    killed tests/test_render_human.py::test_the_common_container_is_not_offered_to_the_renderers_as_a_fact
  4 failed, 2916 passed, 40 deselected in 35.26s
M2: namespace the backend half instead of the neutral half
    killed tests/test_cli.py::test_dump_facts_shows_the_client_save_paths - asser...
    killed tests/test_render_human.py::test_the_neutral_fact_is_the_one_carrying_the_namespace
    killed tests/test_render_human.py::test_the_common_container_is_not_offered_to_the_renderers_as_a_fact
  3 failed, 2917 passed, 40 deselected in 35.89s
M3: spell the namespace differently
    killed tests/test_cli.py::test_dump_facts_shows_the_client_save_paths - asser...
    killed tests/test_render_human.py::test_the_neutral_fact_is_the_one_carrying_the_namespace
    killed tests/test_render_human.py::test_the_common_container_is_not_offered_to_the_renderers_as_a_fact
  3 failed, 2917 passed, 40 deselected in 36.92s
M4: leave the common container in the rendered mapping
    killed tests/test_render_human.py::test_the_common_container_is_not_offered_to_the_renderers_as_a_fact
  1 failed, 2919 passed, 40 deselected in 35.89s
M5: drop the neutral facts from human output entirely
    killed tests/test_cli.py::test_dump_facts_shows_the_client_save_paths - asser...
    killed tests/test_render_human.py::test_a_backend_fact_sharing_a_neutral_name_does_not_hide_it
    killed tests/test_render_human.py::test_the_neutral_fact_is_the_one_carrying_the_namespace
    killed tests/test_render_human.py::test_the_common_container_is_not_offered_to_the_renderers_as_a_fact
  4 failed, 2916 passed, 40 deselected in 39.27s
control again
  2920 passed, 40 deselected in 34.25s
```

Every mutant dies and the control is green either side. The two that would
look identical in a passing suite are separated: M3 changes only the spelling
of the prefix and dies on the end-to-end `dump-facts` assertion, and M4
changes nothing visible in the output at all — it dies only on the unit test
that asserts the `common` container never reaches the renderers, which is why
that test asserts on the mapping and not on the printed lines.

## The gate

```bash
uv run --frozen -q ruff check src tests docs/66-collision-probe.py && uv run --frozen -q ruff format --check src tests docs/66-collision-probe.py && uv run --frozen -q pytest -q 2>&1 | tail -1
rm -rf /tmp/lintarr66-demo
```

```output
All checks passed!
131 files already formatted
2920 passed, 40 deselected in 37.88s
```
