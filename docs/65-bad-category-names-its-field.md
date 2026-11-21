# arr collector: a bad category names the field it read (lintarr#65)

*2026-11-21T14:52:25Z by Showboat 0.6.1*
<!-- showboat-id: 991cb304-1bb2-4411-9b35-a7c5acd181fd -->

The download-client reader accepts one category field per arr flavour: a Sonarr
carries `tvCategory`, a Radarr carries `movieCategory`. It looks for them in
that order and takes the first one the client's `fields` list has. When the
value is not a string it refuses the whole response as `bad-response` — but it
formatted the complaint from `names[0]`, the *first candidate*, not the field
it actually read. So every Radarr operator was told to go fix `tvCategory`: a
field their app does not have.

The fix carries the matching field's name back out of the lookup alongside its
value, so the message names what was read.

Everything below runs against a stub stack serving the bodies
`tests/fixtures/homelab_paths.py` measured on Tom's homelab on 2026-11-19 —
qBittorrent 5.2.3 plus one arr — with two knobs on argv: which arr flavour it
claims to be, and the download client's category field and value.

```bash
set -euo pipefail
DEMO=/tmp/lintarr65-demo
mkdir -p "$DEMO"

# A checkout of the commit this branch was cut from, for the "before" column.
[ -d "$DEMO/base" ] || git worktree add -f --detach "$DEMO/base" e3383d3 >/dev/null 2>&1
git -C "$DEMO/base" log --oneline -1

# One scenario, end to end: start the stub stack on a free port, point that
# checkout's lintarr at it over real HTTP, print the error line it reported,
# stop the stub.
cat > "$DEMO/case.sh" <<'RUNNER'
set -uo pipefail
STUB_PY=$1; CHECKOUT=$2; ARR_KIND=$3; FIELD=$4; VALUE=$5
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
python3 "$STUB_PY" "$PORT" "$ARR_KIND" "$FIELD" "$VALUE" >/dev/null 2>&1 &
STUB=$!
trap 'kill $STUB 2>/dev/null' EXIT
until curl -fs "http://127.0.0.1:$PORT/api/v2/app/version" >/dev/null; do sleep 0.1; done
cd "$CHECKOUT"
OUT=$(DOWNLOAD_CLIENT_KIND=qbittorrent DOWNLOAD_CLIENT_URL="http://127.0.0.1:$PORT" \
  DOWNLOAD_CLIENT_USER=admin DOWNLOAD_CLIENT_PASS=hunter2 \
  ARR_URL="http://127.0.0.1:$PORT" ARR_API_KEY=arrkey \
  uv run --quiet lintarr check 2>/dev/null)
RC=$?
printf '%s\n' "$OUT" | grep -A1 '^ERROR' || printf '%s\n' "$OUT" | tail -4
echo "lintarr check exit code: $RC"
RUNNER
echo "runner written to $DEMO/case.sh"
```

```output
e3383d3 Merge pull request #74 from tclancy/claude/61-queue-liveness-action-route-ignores-per
runner written to /tmp/lintarr65-demo/case.sh
```

## Before: a Radarr is told to fix a Sonarr field

A Radarr whose qBittorrent client reports `movieCategory: []` — the junk value
the reader is right to refuse. This is the base commit, before the change.

```bash
bash /tmp/lintarr65-demo/case.sh "$(git rev-parse --show-toplevel)/docs/65-stub-stack.py" /tmp/lintarr65-demo/base radarr movieCategory "[]"
```

```output
ERROR collect  [arr[main]]
  could not read this service: bad-response. /api/v3/downloadclient: 'tvCategory' is not a string
lintarr check exit code: 2
```

## After: it names `movieCategory`

Same stub, same request, this branch's checkout.

```bash
bash /tmp/lintarr65-demo/case.sh "$(git rev-parse --show-toplevel)/docs/65-stub-stack.py" "$(git rev-parse --show-toplevel)" radarr movieCategory "[]"
```

```output
ERROR collect  [arr[main]]
  could not read this service: bad-response. /api/v3/downloadclient: 'movieCategory' is not a string
lintarr check exit code: 2
```

## The Sonarr case still says `tvCategory`

The field that *was* read, in both directions — this is the half a fix that
simply swapped `names[0]` for `names[-1]` would have broken, and the half the
new test pins. (Measured: that mutation turns the Sonarr case red.)

```bash
bash /tmp/lintarr65-demo/case.sh "$(git rev-parse --show-toplevel)/docs/65-stub-stack.py" "$(git rev-parse --show-toplevel)" sonarr tvCategory "[]"
```

```output
ERROR collect  [arr[main]]
  could not read this service: bad-response. /api/v3/downloadclient: 'tvCategory' is not a string
lintarr check exit code: 2
```

## A healthy category is still read, not refused

Only the wording of the refusal changed: a real `movieCategory: "movies"`
collects and the checks run as before.

```bash
bash /tmp/lintarr65-demo/case.sh "$(git rev-parse --show-toplevel)/docs/65-stub-stack.py" "$(git rev-parse --show-toplevel)" radarr movieCategory "\"movies\""
```

```output
PASS  hardlink-futility  [qbittorrent[main]]
  downloads and library folders share a mount for radarr[main]

3 checked: 3 PASS
lintarr check exit code: 0
```

## The test that pins it

Parametrised over both flavours, and it asserts the *other* field's name is
absent from the message as well as the right one's being present — so a message
that hedged by naming both candidates would not pass.

```bash
cd "$(git rev-parse --show-toplevel)" && uv run --quiet pytest tests/collect/test_arr_paths.py -k bad_category -v 2>&1 | grep -E "bad_category.*(PASSED|FAILED)|deselected" | sed "s/ in [0-9.]*s//"
```

```output
collecting ... collected 51 items / 49 deselected / 2 selected
tests/collect/test_arr_paths.py::test_bad_category_names_the_field_it_read[sonarr-tvCategory] PASSED [ 50%]
tests/collect/test_arr_paths.py::test_bad_category_names_the_field_it_read[radarr-movieCategory] PASSED [100%]
======================= 2 passed, 49 deselected =======================
```
