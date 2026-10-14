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


_PUSH = "pre-push"

# pre-commit's own HOOK_TYPES (clientlib.py). Pinned as a bounded set so a value
# pre-commit would REJECT -- `manual`, or a typo like `pre_push` -- fails here
# rather than passing a membership check and leaving the config unloadable.
_HOOK_TYPES = frozenset(
    {
        "commit-msg",
        "post-checkout",
        "post-commit",
        "post-merge",
        "post-rewrite",
        "pre-commit",
        "pre-merge-commit",
        "pre-push",
        "pre-rebase",
        "prepare-commit-msg",
    }
)

# pre-commit still accepts the pre-3.x stage spellings and maps them forward, so
# the raw YAML must be normalised before any stage comparison. Without this a
# `stages: [push]` hook -- which IS a push hook -- reads as narrowed, and the
# failure message sends the next reader after the wrong thing.
_LEGACY_STAGES = {
    "commit": "pre-commit",
    "merge-commit": "pre-merge-commit",
    "push": "pre-push",
}


# The hooks this battery is: pinned as a SET, because the derived `- id:` count
# below cannot see a deletion -- removing a hook removes its `- id:` line too,
# so both sides of that comparison move together and it stays green. Measured:
# deleting `check-merge-conflict` survived the count. Pinning the set is what
# makes a removal a deliberate edit to this file rather than a silent one.
_EXPECTED_HOOKS = frozenset(
    {
        "trailing-whitespace",
        "end-of-file-fixer",
        "check-yaml",
        "check-merge-conflict",
        "ruff-check",
        "ruff-format",
        "pytest",
    }
)


def _config():
    return yaml.safe_load(_CONFIG.read_text())


def _stages(declared):
    return {_LEGACY_STAGES.get(stage, stage) for stage in declared}


def test_the_config_arms_the_push_path_as_well_as_the_commit_path():
    """``default_install_hook_types`` defaults to ``pre-commit`` alone.

    Without this key, ``pre-commit install`` arms only the commit path, so the
    pre-push battery every hook below is already declared for never runs --
    which is what made ``dispatch gates`` grade this repo ``INERT`` (#45). The
    key is the half that survives a fresh clone; ``pre-commit install`` is
    local machine state and no test can see it.
    """
    declared = _config().get("default_install_hook_types")
    assert declared is not None, (
        "no default_install_hook_types: pre-commit install will arm only the "
        "commit path and the declared pre-push battery will not run"
    )
    # Superset, not equality: adding commit-msg or post-checkout later is fine.
    # Asserting the required set this way also fails an empty list, which a
    # ``not in`` check on a single spelling would pass.
    assert set(declared) >= {"pre-commit", _PUSH}, declared

    # And bounded above, because this key takes no legacy spellings and gets no
    # forward-mapping: pre-commit validates it against HOOK_TYPES verbatim, so
    # `manual` or `pre_push` here makes the config unloadable while every
    # assertion above still passes.
    assert set(declared) <= _HOOK_TYPES, sorted(set(declared) - _HOOK_TYPES)


def test_every_declared_hook_still_runs_at_the_push_stage():
    """The battery is only as wide as the stages its hooks actually run at.

    ``default_stages`` defaults to every stage, so the hooks are push hooks by
    omission rather than by declaration -- and a later ``default_stages`` or a
    per-hook ``stages:`` could narrow that back out without touching the key
    the test above reads. Both narrowing routes are checked here.
    """
    config = _config()
    hook_ids = [h["id"] for r in config["repos"] for h in r["hooks"]]

    # Two reachability controls, because they fail on different things and
    # neither subsumes the other. Every assertion below is of the form "nothing
    # excludes pre-push", and an empty hook list satisfies all of them
    # vacuously.
    #
    # First: the parse reached every hook the file writes down. This one cannot
    # see a DELETION -- removing a hook removes its ``- id:`` line too, so both
    # sides move together and it stays green (measured). It catches the other
    # thing: a parse that stops early while the text still declares seven.
    declared_ids = _CONFIG.read_text().count("- id:")
    assert len(hook_ids) == declared_ids, (
        f"parsed {len(hook_ids)} hooks {hook_ids} but the file writes down "
        f"{declared_ids} — the parse is not reaching every hook, so the stage "
        "checks below prove nothing"
    )

    # Second: the battery is still the battery. This is the half that sees a
    # deletion, and it makes removing a hook a deliberate edit here.
    assert set(hook_ids) == _EXPECTED_HOOKS, (
        f"hook set changed — missing {sorted(_EXPECTED_HOOKS - set(hook_ids))}, "
        f"unexpected {sorted(set(hook_ids) - _EXPECTED_HOOKS)}; update "
        "_EXPECTED_HOOKS if the change is intended"
    )

    default_stages = config.get("default_stages")
    assert default_stages is None or _PUSH in _stages(default_stages), (
        f"default_stages is {default_stages}, which drops the push stage for "
        "every hook that does not override it"
    )

    narrowed = [
        h["id"]
        for r in config["repos"]
        for h in r["hooks"]
        if "stages" in h and _PUSH not in _stages(h["stages"])
    ]
    assert not narrowed, (
        f"hooks declaring stages that exclude {_PUSH}: {narrowed} — they are "
        "installed on push but will be skipped there"
    )


def test_the_suite_hook_runs_even_when_the_range_holds_no_python():
    """Otherwise this file cannot catch the regression it exists to catch.

    pre-commit skips a hook with no matching files *before* it consults
    ``pass_filenames``, and at the push stage the file list is the pushed range.
    Under ``types: [python]`` a commit touching only ``.pre-commit-config.yaml``
    pushed GREEN with ``pytest ...(no files to check)Skipped`` -- measured --
    so deleting ``default_install_hook_types`` was a clean push. Every gate in
    this file is about a non-Python file, so every one of them had the hole.

    This is also the assertion that keeps a future narrowing honest: swapping
    ``always_run`` back for any file matcher (``types``, ``files``, ``exclude``)
    re-opens it, and no other test here can see that.
    """
    pytest_hooks = [h for r in _config()["repos"] for h in r["hooks"] if h["id"] == "pytest"]
    assert len(pytest_hooks) == 1, pytest_hooks
    hook = pytest_hooks[0]

    assert hook.get("always_run") is True, (
        "the pytest hook is not always_run, so a commit whose range holds no "
        f"matching file skips the suite entirely: {hook}"
    )
    # always_run is necessary but not sufficient -- a `files`/`exclude` matcher
    # alongside it still filters what the hook sees, and `types` defaults to
    # [file] which an always_run hook with pass_filenames: false does not need.
    for narrowing in ("files", "exclude", "types", "types_or"):
        assert narrowing not in hook, (
            f"the pytest hook declares {narrowing!r}, which narrows what it "
            f"matches and can empty it again: {hook}"
        )


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
