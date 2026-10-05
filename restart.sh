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
# dir may be missing from PATH. Prepended, as in the siblings: the unit runs
# ~/.local/bin/uv by absolute path, and a manual run should sync with that
# same uv rather than whichever one a distro put earlier on PATH.
for bin_dir in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    case ":$PATH:" in
        *":$bin_dir:"*) ;;
        *) [ -d "$bin_dir" ] && PATH="$bin_dir:$PATH" ;;
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
# No --no-dev: the unit's `uv run --frozen` syncs the default groups, so
# stripping dev here would only have the next tick reinstall it, fetching
# packages inside a run that should be pure checking.
uv sync --frozen --quiet

# On a first deploy Ansible may pull the source before it installs the unit.
# LoadState, not `systemctl --user cat`: cat exits non-zero for "no such unit"
# and for "no user bus" alike, so a run without XDG_RUNTIME_DIR would report
# "not installed" and exit 0. `show` fails outright on a bus error and set -e
# stops the deploy there.
load_state="$(systemctl --user show -p LoadState --value "$UNIT")"
if [ "$load_state" = not-found ]; then
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
