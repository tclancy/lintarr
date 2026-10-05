#!/usr/bin/env bash
# restart.sh — sync deps and kick one check, after a deploy updates this checkout.
#
# The itguy systemd-shape convention (see sandy/restart.sh): Ansible's
# `Clone or pull lintarr source` task notifies the handler that runs this.
# Safe to run multiple times.
#
# lintarr has no long-running process. lintarr.service is Type=oneshot, fired
# hourly by lintarr.timer, and re-execs from this checkout every tick, so new
# code lands on the next tick with nothing to restart. The one start below is
# the spec's check-on-start, so a deploy gets a verdict now rather than within
# the hour.
#
# Requirements:
#   - uv installed at ~/.local/bin/uv
#   - lintarr.service / lintarr.timer under ~/.config/systemd/user/
#   - loginctl enable-linger has been run for the current user

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT=lintarr.service

# Non-interactive SSH shells don't source the login profile, so uv's install
# dir may be missing from PATH. Appended, not prepended: a uv already on PATH
# wins, and these are only the fallback.
for bin_dir in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    case ":$PATH:" in
        *":$bin_dir:"*) ;;
        *) [ -d "$bin_dir" ] && PATH="$PATH:$bin_dir" ;;
    esac
done
export PATH

if ! command -v uv >/dev/null 2>&1; then
    echo "error: 'uv' not found on PATH (also looked in ~/.local/bin, ~/.cargo/bin)." >&2
    exit 1
fi

cd "$SCRIPT_DIR"

echo "Syncing dependencies..."
# --frozen: run locked versions, never rewrite the tracked uv.lock (a dirty
# lock breaks the Ansible git task's idempotency — see sandy/restart.sh).
# --no-dev: the box runs checks, not the test suite.
uv sync --frozen --no-dev --quiet

# On a first deploy Ansible may pull the source before it installs the unit.
if ! systemctl --user cat "$UNIT" >/dev/null 2>&1; then
    echo "$UNIT not installed yet — the timer will run the first check once Ansible installs it."
    exit 0
fi

# start, never restart, and --no-block: on a oneshot either of the other
# forms waits for the check and returns its exit code, so a stack with a
# FAIL finding would fail the deploy. The finding pages through OnFailure=;
# the deploy reports only whether it deployed.
echo "Kicking one check..."
systemctl --user start --no-block "$UNIT"

echo "Done. Follow it with: journalctl --user -u $UNIT -f"
