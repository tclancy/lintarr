"""The CI workflow has to be the thing that actually grades a pull request.

Until #47 this repo shipped no ``.github/`` at all, so GitHub could post no
check-run on any head and both open PRs read ``ABSENT`` in ``dispatch
pr-gates`` — no verdict, which in the PR UI is indistinguishable from a
passing one. ``.pre-commit-config.yaml`` was the only gate, and it is local
machine state: it grades whatever the committer had installed and is invisible
to anyone reading the PR.

The guards below are the ones whose absence would leave the workflow present
and inert rather than missing. A workflow that exists, runs, and grades the
wrong thing is worse than no workflow, because the green is believed.
"""

import re
import tomllib
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW = _ROOT / ".github" / "workflows" / "tests.yml"

#: A git SHA, which is the only form of `uses:` pin that cannot be moved under
#: us. A tag is a mutable pointer; `@v7` and `@v7.0.1` are both re-pointable by
#: the action's owner at any time.
#:
#: No arm for the trailing `# v7.0.1` comment, deliberately: YAML strips it
#: before the value reaches `step["uses"]`, so such an arm would be dead code
#: that reads as though the tag claim were being checked. **It is not checked
#: here and cannot be** — confirming that a tag points at a SHA needs the
#: network. The three pins in this workflow were verified by hand against
#: `gh api repos/<owner>/<repo>/git/ref/tags/<tag>` on 2026-10-14, and a
#: future bump owes the same check.
_SHA_PIN = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def _workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text())


def _triggers(doc: dict) -> dict:
    """The `on:` block, read past YAML 1.1's boolean coercion.

    PyYAML follows YAML 1.1, where bare ``on`` is the boolean ``True`` — so
    ``doc["on"]`` is a ``KeyError`` and ``doc[True]`` is the block. Looking up
    the string and falling back to ``{}`` would make every trigger assertion
    below vacuously pass, which is the specific failure this helper exists to
    prevent. Both spellings are accepted; finding neither is an error.
    """
    for key in (True, "on"):
        if key in doc:
            return doc[key]
    raise AssertionError(f"no `on:` block in {_WORKFLOW}; top-level keys were {list(doc)}")


def _steps(doc: dict) -> list[dict]:
    return [step for job in doc["jobs"].values() for step in job["steps"]]


#: Shell separators that begin a new command inside one `run:` block.
_COMMAND_SPLIT = re.compile(r"\n|&&|\|\||;")


def _raw_runs(doc: dict) -> list[str]:
    """Every `run:` block UNSPLIT.

    `_commands` splits on `||`, which destroys the token the swallow check
    below is looking for: `pytest -q || true` becomes `pytest -q` and `true`,
    and a search for `|| true` then finds nothing. The 14-mutant round caught
    exactly that — the `|| true` mutant was the one survivor, and it survived
    against a guard written to catch it. Two readers, because the two
    questions are different: "is this call locked" is about each command,
    "is this failure swallowed" is about the separator between them.
    """
    return [step["run"] for step in _steps(doc) if "run" in step]


def _commands(doc: dict) -> list[str]:
    """Every shell command, with multi-line `run:` blocks split apart.

    A `run: |` block is ONE string, so a step holding `uv sync --frozen` and
    `uv run pytest -q` would satisfy a per-step "this line contains --frozen"
    test while the second call is unlocked. Measured: collapsing this
    workflow's four steps into one block defeated
    `test_dependency_installs_are_locked` before this split existed, and that
    is an ordinary refactor rather than an adversarial one. Splitting makes
    each guard answer about the call rather than about the step holding it.
    """
    out = []
    for step in _steps(doc):
        if "run" not in step:
            continue
        out.extend(part.strip() for part in _COMMAND_SPLIT.split(step["run"]) if part.strip())
    return out


def test_the_workflow_exists_where_github_reads_it():
    """Path, not just presence. A workflow one directory out never runs."""
    assert _WORKFLOW.is_file(), (
        f"{_WORKFLOW.relative_to(_ROOT)} is missing — without it no check-run "
        "can be posted on any head and every PR here reads ABSENT (#47)"
    )
    # Reachability for every assertion below: a file that parses to None or to
    # a scalar would satisfy a bare `is_file()` and then silently supply empty
    # sequences to all of them.
    doc = _workflow()
    assert isinstance(doc, dict) and doc.get("jobs"), f"workflow parsed to {doc!r}"


def test_the_pull_request_trigger_carries_reopened():
    """`close && reopen` is the only re-gate an agent can raise on a fixed head.

    `gh run rerun` is not a substitute: it replays the merge commit recorded
    when the run first triggered, so a PR whose failure was since fixed on
    `main` re-runs red against a stale base. Only a fresh `pull_request` event
    recomputes `refs/pull/N/merge`.

    In an `opened`-only repo the close/reopen raises nothing at all, and the
    PR comes back carrying no check-run on its head — ABSENT, which renders as
    green. That is a worse state than the red it was meant to clear.
    """
    pull_request = _triggers(_workflow())["pull_request"]
    types = pull_request["types"]
    assert "reopened" in types, (
        f"pull_request types are {types} — `gh pr close && gh pr reopen` "
        "cannot re-gate a PR without `reopened`, and the PR returns with no "
        "check-run rather than with a fresh verdict"
    )
    # The other two are what make it a gate on new work at all, and dropping
    # either is the same class of silent hole.
    assert {"opened", "synchronize"} <= set(types), types


def test_the_workflow_runs_the_test_suite():
    """Not only the linter.

    A linter cannot see a broken invariant, which is all this project is.
    Issue #24 is the worked example: a three-way merge dropped a regression
    test's entire assertion block, pytest stayed green on the survivor, and
    ruff noticed only because that particular deletion orphaned a bound name.
    """
    runs = _commands(_workflow())
    # A command word inside a `uv run`, not a substring. `"pytest" in line`
    # also passes on `echo pytest` and on `pytest --collect-only`, neither of
    # which grades anything.
    invoked = [r for r in runs if re.search(r"\buv run\b.*\bpytest\b(?!\s*--collect-only)", r)]
    assert invoked, f"no step invokes pytest through uv; the commands are {runs}"


def test_the_workflow_runs_both_ruff_gates_without_fixing():
    """`--fix` would make the gate edit the tree until it passes.

    The pre-commit hooks DO use `--fix`, correctly: there, a human is about to
    look at the result. In CI there is nobody to look, so a self-healing gate
    reports green on a tree that was never green.
    """
    runs = _commands(_workflow())
    ruff = [line for line in runs if "ruff" in line]
    assert any("ruff check" in line for line in ruff), ruff
    assert any("ruff format --check" in line for line in ruff), ruff
    assert not any("--fix" in line for line in ruff), (
        f"a ruff step passes --fix, so the gate rewrites the tree to pass: {ruff}"
    )


def test_dependency_installs_are_locked():
    """A CI run that re-resolves grades a dependency set nobody reviewed.

    `uv.lock` is committed. Without `--frozen` a `uv sync` (or any `uv run`)
    may silently move it, which is both an ungraded dependency change and the
    source of a lockfile diff appearing in a PR its author did not write.
    """
    runs = _commands(_workflow())
    # `\buv\b.*\b(sync|run)\b`, not `\buv (sync|run)\b`: uv accepts global
    # options before the subcommand, so `uv --directory . sync` is a sync and
    # the tighter pattern cannot see it. An unfrozen call the pattern misses
    # is invisible while the floor below still clears on the ones it sees.
    uv_calls = [line for line in runs if re.search(r"\buv\b.*\b(sync|run)\b", line)]
    # Reachability: an empty list would pass the loop below trivially, and an
    # empty list is exactly what a renamed step or a `uses:`-based install
    # produces.
    assert len(uv_calls) >= 2, f"expected several uv invocations, found {uv_calls}"
    unlocked = [line for line in uv_calls if "--frozen" not in line]
    assert not unlocked, f"uv invocations that may re-resolve uv.lock: {unlocked}"


def test_every_action_is_pinned_to_a_sha():
    """A tag is a mutable pointer the action's owner can re-aim at any time.

    Third-party action code runs in this workflow with the job's token. A
    `@v7` pin is a promise from someone outside this repo; a 40-hex SHA is
    not a promise.
    """
    uses = [step["uses"] for step in _steps(_workflow()) if "uses" in step]
    # Reachability. Zero `uses:` entries satisfies "all of them are pinned".
    assert len(uses) >= 3, f"expected at least three actions, found {uses}"
    floating = [ref for ref in uses if not _SHA_PIN.match(ref)]
    assert not floating, f"actions pinned to a mutable ref rather than a SHA: {floating}"


def test_the_ci_interpreter_satisfies_requires_python():
    """So the gate cannot grade an interpreter the package refuses to install on.

    Only the `>=X.Y` form is understood, and an unrecognised form FAILS rather
    than being skipped — a `requires-python` nobody here can parse is exactly
    where a mismatch would hide.
    """
    requires = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    match = re.fullmatch(r">=\s*(\d+)\.(\d+)", requires.strip())
    assert match, (
        f"requires-python is {requires!r}, which this gate cannot read — "
        "teach it the new form rather than deleting the check"
    )
    floor = (int(match[1]), int(match[2]))

    versions = [
        step["with"]["python-version"]
        for step in _steps(_workflow())
        if "setup-python" in step.get("uses", "") and "with" in step
    ]
    assert versions, "no setup-python step declares a python-version"
    for raw in versions:
        parts = tuple(int(p) for p in str(raw).split(".")[:2])
        assert parts >= floor, f"CI runs python {raw} but pyproject requires-python is {requires}"


def test_the_pull_request_trigger_has_no_path_filter():
    """A `paths:` filter re-creates #47's original bug exactly.

    When a filter excludes a PR's files GitHub does not run the workflow and
    posts no check-run at all — the head is ABSENT, which is what this repo
    looked like before #47 and which renders in the PR UI as an absence of
    red rather than as an absence of verdict. Of every way this workflow can
    regress, that is the quietest: the PR looks fine and `dispatch pr-gates`
    is the only thing that would say otherwise.

    `paths-ignore` is the same hazard in the other spelling, so both are
    named rather than one — a guard banning a single spelling passes every
    synonym.
    """
    pull_request = _triggers(_workflow())["pull_request"]
    filters = {key for key in ("paths", "paths-ignore") if key in pull_request}
    assert not filters, (
        f"pull_request carries {sorted(filters)} — a PR whose files it "
        "excludes gets NO check-run, which reads as green (#47)"
    )


def test_nothing_lets_a_failing_step_report_success():
    """`continue-on-error` and `|| true` turn a red suite into a green job.

    This is the other silent-green path, and it is quieter than a broken
    test: the check-run exists, it is named, and it says `success`. A reader
    with no reason to open the log has nothing to notice.
    """
    doc = _workflow()
    tolerant = [job_name for job_name, job in doc["jobs"].items() if job.get("continue-on-error")]
    tolerant += [
        step.get("name", step.get("uses", step.get("run", "?")))
        for step in _steps(doc)
        if step.get("continue-on-error")
    ]
    assert not tolerant, f"continue-on-error is set on: {tolerant}"

    # Reachability: an empty command list satisfies the sweep below, and an
    # empty command list is what a `uses:`-only rewrite of this job produces.
    runs = _commands(doc)
    assert len(runs) >= 4, f"expected the four gate commands, found {runs}"
    # Raw, not split -- see _raw_runs. Multiline so `$` matches at the end of
    # each line inside a `run: |` block rather than only at the end of it.
    swallowed = [
        block for block in _raw_runs(doc) if re.search(r"\|\|\s*(true|:)\s*$", block, re.M)
    ]
    assert not swallowed, f"run blocks whose failure is swallowed: {swallowed}"


def test_the_job_pins_the_interpreter_uv_will_actually_use():
    """`setup-python` only puts an interpreter on PATH; uv picks its own.

    With no `.python-version` in the repo and no `UV_PYTHON`, `uv sync` is
    constrained only by `requires-python = ">=3.13"` and is free to fetch a
    newer interpreter than the one `setup-python` installed. That would make
    the `python-version` pin decorative and
    `test_the_ci_interpreter_satisfies_requires_python` an assertion about a
    declaration rather than about what runs.
    """
    doc = _workflow()
    declared = [
        step["with"]["python-version"]
        for step in _steps(doc)
        if "setup-python" in step.get("uses", "") and "with" in step
    ]
    assert declared, "no setup-python step declares a python-version"

    pinned = {str(job.get("env", {}).get("UV_PYTHON")) for job in doc["jobs"].values()}
    assert pinned == {str(v) for v in declared}, (
        f"UV_PYTHON is {pinned} but setup-python installs {declared} — uv "
        "would be free to run the suite on a different interpreter"
    )
