"""restart.sh — what itguy's deploy runs after Ansible pulls this checkout.

Run for real against stub ``uv`` and ``systemctl`` that record their argv, so
the assertions are about the commands issued, not the script's text.
"""

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "restart.sh"

_STUB = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALLS"
if [ "$(basename "$0")" = systemctl ] && [ "$2" = cat ]; then
    exit "${UNIT_CAT_EXIT:-0}"
fi
exit 0
"""


def _write_stub(bin_dir: Path, name: str) -> None:
    path = bin_dir / name
    path.write_text(_STUB)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(tmp_path: Path, *, unit_installed: bool) -> tuple[subprocess.CompletedProcess, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("uv", "systemctl"):
        _write_stub(bin_dir, name)
    calls = tmp_path / "calls"
    calls.touch()
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CALLS": str(calls),
        "UNIT_CAT_EXIT": "0" if unit_installed else "1",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
    )
    return result, calls.read_text().splitlines()


def test_script_is_executable():
    # itguy's handler invokes ./restart.sh, not `bash restart.sh`.
    assert os.access(SCRIPT, os.X_OK)


def test_syncs_the_locked_runtime_dependencies(tmp_path):
    result, calls = _run(tmp_path, unit_installed=True)
    assert result.returncode == 0, result.stderr
    syncs = [c for c in calls if c.startswith("uv sync")]
    assert len(syncs) == 1
    # --frozen: never rewrite the tracked uv.lock (a dirty lock breaks the
    # Ansible git task's idempotency). --no-dev: hypothesis and friends have
    # no business on the box.
    assert "--frozen" in syncs[0].split()
    assert "--no-dev" in syncs[0].split()


def test_kicks_one_check_without_waiting_for_it(tmp_path):
    result, calls = _run(tmp_path, unit_installed=True)
    assert result.returncode == 0, result.stderr
    starts = [c for c in calls if c.startswith("systemctl") and " start " in f" {c} "]
    # --no-block keeps a FAIL finding from failing the deploy: the finding
    # pages through OnFailure=, the deploy reports only whether it deployed.
    assert starts == ["systemctl --user start --no-block lintarr.service"]


def test_never_restarts_the_oneshot(tmp_path):
    # `restart` on a Type=oneshot unit blocks until the check finishes and
    # returns its exit code, coupling deploy success to the stack's findings.
    _, calls = _run(tmp_path, unit_installed=True)
    assert not [c for c in calls if c.split()[:3] == ["systemctl", "--user", "restart"]]


def test_unit_not_installed_yet_is_not_a_failure(tmp_path):
    # First deploy: Ansible may pull the source before it installs the unit.
    result, calls = _run(tmp_path, unit_installed=False)
    assert result.returncode == 0, result.stderr
    assert not [c for c in calls if " start " in f" {c} "]
    assert "not installed" in result.stdout


def test_missing_uv_fails_loudly(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "systemctl")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "CALLS": "/dev/null"}
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode != 0
    assert "'uv' not found" in result.stderr
