#!/usr/bin/env bash
# Push an ntfy alert for a failed nightly conformance run.
# Reads the failure kind and failed scenario ids from conformance-result.json
# (written by tests/conformance/conftest.py). Needs NTFY_TOKEN and RUN_URL.
set -euo pipefail

if [[ -z "${NTFY_TOKEN:-}" ]]; then
  echo "NTFY_TOKEN secret is not set; no alert was sent" >&2
  exit 1
fi

RESULT_FILE="${CONFORMANCE_RESULT_FILE:-conformance-result.json}"
RUN_URL="${RUN_URL:-}"

# python3, not jq: jq is not guaranteed on every machine this runs on.
read_result() {
  python3 - "$RESULT_FILE" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path) as fh:
        data = json.load(fh)
    kind = data["kind"]
    if kind not in ("disagreement", "unmapped-state", "harness-error"):
        kind = "harness-error"
    failed = ",".join(str(i) for i in data.get("failed", []))
except FileNotFoundError:
    kind, failed = "harness-error", f"{path} was missing"
except Exception as exc:  # unparseable or wrong shape
    kind, failed = "harness-error", f"{path} was unreadable ({type(exc).__name__})"
print(kind)
print(failed[:300])
PY
}

result="$(read_result)"
kind="$(printf '%s\n' "$result" | sed -n 1p)"
failed="$(printf '%s\n' "$result" | sed -n 2p)"

# The token goes to curl on stdin (--config -), never argv, so it is invisible
# to `ps` and to xtrace. printf is a builtin, so it forks no argv either.
printf 'header = "Authorization: Bearer %s"\n' "$NTFY_TOKEN" | curl --fail-with-body -sS \
  --config - \
  -H "Title: lintarr conformance: $kind" \
  -H "Priority: high" \
  -H "Click: $RUN_URL" \
  -d "$kind: $failed — $RUN_URL" \
  https://notifications.tomclancy.info/homelab-alerts
