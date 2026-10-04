"""The gate has to grade what the developer actually runs.

lintarr ships no CI workflows, so ``.pre-commit-config.yaml`` is the only thing
standing between a change and ``main``. That makes its pins part of the
behaviour: a hook ``rev`` and a project dependency are two independent copies of
one version number, and nothing in a passing hook run can notice them drifting
apart. ``ruff>=0.6`` is a floor, so any ``uv lock --upgrade`` moves ``uv run
ruff`` while the ``rev`` stays put — and a formatter disagreeing with its own
gate is a failing commit that nobody can reproduce from the config.
"""

import ast
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


# ---------------------------------------------------------------------------
# Two gates about the suite and the messages, rather than about the pins.
# ---------------------------------------------------------------------------

_SRC = _ROOT / "src" / "lintarr"
_TESTS = _ROOT / "tests"

#: The one test that is assertion-free on purpose, with its reason. Its own
#: docstring says so outright: what it asserts is that ``authenticate``
#: *returns*, and an exception escaping is the failure. Anything else appearing
#: here is a dropped assertion, not a style choice.
_ASSERTION_FREE_BY_DESIGN = frozenset(
    {
        (
            "tests/collect/test_qbittorrent_ban.py",
            "test_a_2xx_carrying_the_ban_body_still_authenticates",
        ),
    }
)


def _holds_an_assertion(fn: ast.AST) -> bool:
    """Structurally — not by searching the unparsed source for "assert".

    A substring search over ``ast.unparse`` is the obvious implementation and
    it is wrong: the docstring is part of the output, so a test whose prose
    says "this asserts something different" passes while asserting nothing.
    The exempted test below is exactly that shape, which is how this was
    caught.
    """
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute) and target.attr in {"raises", "assertRaises"}:
                return True
            if isinstance(target, ast.Name) and target.id.startswith("_assert"):
                return True
    return False


def _test_functions():
    for path in sorted(_TESTS.rglob("test_*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test_"
            ):
                yield rel, node


def test_every_test_function_asserts_something():
    """Issue #24: a test that asserts nothing is a green test.

    Three PRs editing ``tests/collect/test_stack.py`` merged inside ten
    minutes, and the resolution dropped a regression test's whole assertion
    block. The survivor called ``collect_stack`` and asserted nothing, so the
    "must not abort the whole run" half of its docstring still held and the "is
    recorded" half did not. ``pytest`` stayed green, and ``ruff`` noticed only
    because that particular deletion orphaned a bound name — a dropped
    assertion after a bare call expression leaves a linter nothing to see.

    Counts as holding an assertion: a bare ``assert``, a ``raises`` /
    ``assertRaises`` context, or a call to a helper whose name starts with
    ``_assert``. The last is load-bearing — four tests in
    ``test_cli_config_errors.py`` route every assertion through
    ``_assert_clean_usage_error``.
    """
    checked = 0
    offenders = []
    for rel, fn in _test_functions():
        checked += 1
        if (rel, fn.name) in _ASSERTION_FREE_BY_DESIGN:
            continue
        if not _holds_an_assertion(fn):
            offenders.append(f"{rel}:{fn.lineno} {fn.name}")

    # Reachability control. A glob that stopped matching, or a parse that
    # silently yielded nothing, reports zero offenders out of zero tests and
    # reads exactly like a pass.
    assert checked >= 300, (
        f"only {checked} test functions were examined — the sweep is not "
        "reaching the suite, so a clean verdict from it means nothing"
    )
    assert not offenders, (
        "test functions with no assertion, no raises-context and no _assert "
        f"helper call (issue #24): {offenders}"
    )


def test_the_assertion_free_exemptions_still_name_real_tests():
    """So a renamed or deleted test cannot leave a permanent hole behind it."""
    defined = {(rel, fn.name) for rel, fn in _test_functions()}
    for entry in _ASSERTION_FREE_BY_DESIGN:
        assert entry in defined, (
            f"{entry[0]} no longer defines {entry[1]} — drop the exemption "
            "rather than leaving it to cover a future test that takes the name"
        )


def _module_level_strings(tree: ast.Module) -> dict[str, str]:
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out[t.id] = node.value.value
    return out


def test_no_service_error_detail_contains_a_newline():
    """Two docstrings rest on this and nothing held it.

    ``run._error_detail`` says the detail "stays one line because" the renderer
    indents it with a bare two spaces, and ``cli._render_error_row`` applies
    that indent to the first physical line only. A detail carrying ``\n``
    renders unindented in the human surface and leaks a raw newline into
    ``check --json``. Every one of the raise sites is implicit string
    concatenation today, so the hazard is unreachable — this is what keeps it
    that way.

    Resolved statically, and **unresolvable argument shapes fail** rather than
    being skipped: a detail built by an f-string or a call is exactly where a
    newline would arrive unnoticed, so it has to come and be looked at.
    """
    checked = 0
    newlines = []
    unresolved = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        tree = ast.parse(path.read_text())
        consts = _module_level_strings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "ServiceError"):
                continue
            if len(node.args) < 2:
                unresolved.append(f"{rel}:{node.lineno} fewer than two positional args")
                continue
            detail = node.args[1]
            if isinstance(detail, ast.Constant) and isinstance(detail.value, str):
                checked += 1
                if "\n" in detail.value:
                    newlines.append(f"{rel}:{node.lineno} literal detail")
            elif isinstance(detail, ast.Name) and detail.id in consts:
                checked += 1
                if "\n" in consts[detail.id]:
                    newlines.append(f"{rel}:{node.lineno} via {detail.id}")
            elif isinstance(detail, ast.JoinedStr):
                checked += 1
                for part in detail.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        if "\n" in part.value:
                            newlines.append(f"{rel}:{node.lineno} f-string segment")
            else:
                unresolved.append(f"{rel}:{node.lineno} {type(detail).__name__}")

    assert checked >= 10, (
        f"only {checked} ServiceError details were resolved — the walk is not "
        "reaching the raise sites, so its clean verdict means nothing"
    )
    assert not unresolved, (
        "ServiceError detail arguments this gate could not read, which is "
        f"where an unnoticed newline would arrive: {unresolved}"
    )
    assert not newlines, f"ServiceError details containing a newline: {newlines}"
