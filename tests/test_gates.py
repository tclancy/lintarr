"""The gate has to grade what the developer actually runs.

lintarr ships no CI workflows, so ``.pre-commit-config.yaml`` is the only thing
standing between a change and ``main``. That makes its pins part of the
behaviour: a hook ``rev`` and a project dependency are two independent copies of
one version number, and nothing in a passing hook run can notice them drifting
apart. ``ruff>=0.6`` is a floor, so any ``uv lock --upgrade`` moves ``uv run
ruff`` while the ``rev`` stays put — and a formatter disagreeing with its own
gate is a failing commit that nobody can reproduce from the config.
"""

import tomllib
from importlib.metadata import version
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / ".pre-commit-config.yaml"
_RUFF_REPO = "https://github.com/astral-sh/ruff-pre-commit"


def _hooks():
    return yaml.safe_load(_CONFIG.read_text())["repos"]


def test_the_ruff_hook_pins_the_ruff_the_project_resolves():
    """One version number, two places it is written down."""
    pinned = next(r["rev"] for r in _hooks() if r["repo"] == _RUFF_REPO)
    assert pinned == f"v{version('ruff')}", (
        f"the ruff-pre-commit rev is {pinned} but this environment resolves "
        f"ruff {version('ruff')} — bump both or the gate grades a different "
        "formatter than you run"
    )


def test_the_gate_runs_the_test_suite():
    """A linter cannot see a broken invariant, which is all this project is.

    Without this hook the only gate on the way in checks style and says nothing
    about whether ``queue-liveness`` still reports homelab#393.
    """
    local = [r for r in _hooks() if r["repo"] == "local"]
    entries = [h["entry"] for r in local for h in r["hooks"]]
    assert any("pytest" in entry for entry in entries), (
        f"no local hook runs pytest; local hook entries are {entries}"
    )


def test_pre_commit_is_a_declared_dependency():
    """So ``uv sync`` is enough to reproduce the gate, not a second install step."""
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    dev = pyproject["dependency-groups"]["dev"]
    assert any(spec.startswith("pre-commit") for spec in dev), dev
