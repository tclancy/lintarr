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
# Prints two lines: the overall kind, then the failed ids as "id (kind)" pairs,
# capped at 300 chars on a pair boundary with a trailing "…" when trimmed.
read_result() {
  python3 - "$RESULT_FILE" <<'PY'
import json
import sys

KINDS = ("disagreement", "unmapped-state", "harness-error")
CAP = 300
MORE = "…"


def valid(kind):
    """An unknown kind (wrong value or type, or a header injection) is a harness error."""
    return kind if kind in KINDS else "harness-error"


def capped(pairs):
    listed = ""
    for pair in pairs:
        candidate = f"{listed},{pair}" if listed else pair
        if len(candidate) > CAP - len("," + MORE):
            if not listed:  # a single pair longer than the cap: cut it
                return pair[: CAP - len(MORE)] + MORE
            return f"{listed},{MORE}"
        listed = candidate
    return listed


path = sys.argv[1]
try:
    with open(path) as fh:
        data = json.load(fh)
    kind = valid(data["kind"])
    kinds = data.get("kinds")
    if not isinstance(kinds, dict):
        kinds = {}
    failed = capped(f"{i} ({valid(kinds.get(i))})" for i in data.get("failed", []))
except FileNotFoundError:
    kind, failed = "harness-error", f"{path} was missing"
except Exception as exc:  # unparseable or wrong shape
    kind, failed = "harness-error", f"{path} was unreadable ({type(exc).__name__})"
print(kind)
print(failed.replace("\n", " "))
PY
}

result="$(read_result)"
kind="$(printf '%s\n' "$result" | sed -n 1p)"
failed="$(printf '%s\n' "$result" | sed -n 2p)"

# The token goes to curl on stdin (--config -), never argv, so `ps` cannot see
# it; printf is a builtin, so it forks no argv either. That is not xtrace-proof:
# under `set -x` bash would still print printf's expanded arguments, token
# included. So this script never enables xtrace; do not add `set -x` here.
printf 'header = "Authorization: Bearer %s"\n' "$NTFY_TOKEN" | curl --fail-with-body -sS \
  --config - \
  -H "Title: lintarr conformance: $kind" \
  -H "Priority: high" \
  -H "Click: $RUN_URL" \
  -d "$kind: $failed — $RUN_URL" \
  https://notifications.tomclancy.info/homelab-alerts
