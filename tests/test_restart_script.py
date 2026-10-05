"""restart.sh — what itguy's deploy runs after Ansible pulls this checkout.

Run for real against stub ``uv`` and ``systemctl`` that record their argv and
working directory, so the assertions are about the commands issued, not the
script's text. PATH holds only the stubs and the handful of real tools the
script needs: a ``/usr/bin`` on PATH would let a distro-packaged uv run a real
``uv sync`` against this checkout mid-test.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "restart.sh"
UNIT = "lintarr.service"

# Each stub logs "<cwd>|<name> <args>". Failure is per-call, chosen by env:
# UV_EXIT for `uv sync`, START_EXIT for `systemctl start`, SHOW_EXIT for
# `systemctl show` (a bus failure). `show` reports LOAD_STATE for lintarr's
# unit only, so asking about any other unit reads as not-found.
_STUB = (
    """#!/usr/bin/env bash
name="$(basename "$0")"
echo "$PWD|$name $*" >> "$CALLS"
case "$name $1 $2" in
    "uv sync "*) exit "${UV_EXIT:-0}" ;;
    "systemctl --user start") exit "${START_EXIT:-0}" ;;
    "systemctl --user show")
        [ "${SHOW_EXIT:-0}" = 0 ] || { echo "Failed to connect to bus" >&2; exit "$SHOW_EXIT"; }
        if [ "${!#}" = "%s" ]; then echo "${LOAD_STATE:-loaded}"; else echo not-found; fi
        ;;
esac
exit 0
"""
    % UNIT
)


def _tools_dir(tmp_path: Path) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    for tool in ("bash", "env", "dirname", "basename"):
        (tools / tool).symlink_to(shutil.which(tool))
    return tools


def _write_stub(bin_dir: Path, name: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text(_STUB)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(
    tmp_path: Path,
    *,
    stub_dir: Path | None = None,
    stubs: tuple[str, ...] = ("uv", "systemctl"),
    **env_overrides: str,
) -> tuple[subprocess.CompletedProcess, list[tuple[str, str]]]:
    """Run restart.sh from an unrelated cwd; return the result and (cwd, call) pairs."""
    stub_dir = stub_dir or tmp_path / "bin"
    for name in stubs:
        _write_stub(stub_dir, name)
    calls = tmp_path / "calls"
    calls.touch()
    tools = _tools_dir(tmp_path)
    env = {
        "PATH": f"{stub_dir}:{tools}",
        "HOME": str(tmp_path),
        "CALLS": str(calls),
        **env_overrides,
    }
    result = subprocess.run(
        [str(tools / "bash"), str(SCRIPT)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    pairs = [tuple(line.split("|", 1)) for line in calls.read_text().splitlines()]
    return result, pairs


def _commands(pairs) -> list[str]:
    return [call for _, call in pairs]


def _starts(pairs) -> list[str]:
    return [c for c in _commands(pairs) if c.startswith("systemctl --user start")]


def test_script_is_executable():
    # itguy's handler invokes ./restart.sh, not `bash restart.sh`.
    assert os.access(SCRIPT, os.X_OK)


def test_syncs_the_locked_dependencies_in_the_checkout(tmp_path):
    result, pairs = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    syncs = [(cwd, c) for cwd, c in pairs if c.startswith("uv sync")]
    assert len(syncs) == 1
    cwd, call = syncs[0]
    # Run from tmp_path, so this holds only if the script cds to its own dir.
    assert Path(cwd) == REPO
    # --frozen: never rewrite the tracked uv.lock (a dirty lock breaks the
    # Ansible git task's idempotency).
    assert "--frozen" in call.split()
    # No --no-dev: the unit's `uv run` syncs the default groups, so stripping
    # dev here only makes the next tick reinstall it.
    assert "--no-dev" not in call.split()


def test_kicks_one_check_after_the_sync_without_waiting_for_it(tmp_path):
    result, pairs = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    # --no-block keeps a FAIL finding from failing the deploy: the finding
    # pages through OnFailure=, the deploy reports only whether it deployed.
    assert _starts(pairs) == [f"systemctl --user start --no-block {UNIT}"]
    commands = _commands(pairs)
    sync_at = next(i for i, c in enumerate(commands) if c.startswith("uv sync"))
    assert sync_at < commands.index(_starts(pairs)[0])


def test_never_restarts_the_oneshot(tmp_path):
    # `restart` on a Type=oneshot unit blocks until the check finishes and
    # returns its exit code, coupling deploy success to the stack's findings.
    _, pairs = _run(tmp_path)
    assert not [c for c in _commands(pairs) if c.split()[:3] == ["systemctl", "--user", "restart"]]


def test_unit_not_installed_yet_is_not_a_failure(tmp_path):
    # First deploy: Ansible may pull the source before it installs the unit.
    result, pairs = _run(tmp_path, LOAD_STATE="not-found")
    assert result.returncode == 0, result.stderr
    assert _starts(pairs) == []
    assert "not installed" in result.stdout


def test_no_user_bus_fails_rather_than_passing_as_not_installed(tmp_path):
    # `systemctl --user cat` exits non-zero for a missing unit AND for no
    # bus (ssh without XDG_RUNTIME_DIR, linger off); the deploy must not go
    # green having kicked nothing.
    result, pairs = _run(tmp_path, SHOW_EXIT="1")
    assert result.returncode != 0
    assert _starts(pairs) == []
    assert "not installed" not in result.stdout


def test_failed_sync_fails_the_deploy_and_kicks_nothing(tmp_path):
    result, pairs = _run(tmp_path, UV_EXIT="1")
    assert result.returncode != 0
    assert _starts(pairs) == []


def test_failed_start_fails_the_deploy(tmp_path):
    # --no-block still reports enqueue failures (bad unit file) synchronously.
    result, _ = _run(tmp_path, START_EXIT="1")
    assert result.returncode != 0


def test_finds_uv_in_home_local_bin_when_path_lacks_it(tmp_path):
    # Non-interactive ssh shells don't source the profile, so ~/.local/bin
    # is often missing from PATH.
    local_bin = tmp_path / ".local" / "bin"
    _write_stub(local_bin, "uv")
    result, pairs = _run(tmp_path, stubs=("systemctl",))
    assert result.returncode == 0, result.stderr
    assert [c for c in _commands(pairs) if c.startswith("uv sync")]


def test_missing_uv_fails_loudly(tmp_path):
    result, pairs = _run(tmp_path, stubs=("systemctl",))
    assert result.returncode != 0
    assert "'uv' not found" in result.stderr
    assert _starts(pairs) == []
