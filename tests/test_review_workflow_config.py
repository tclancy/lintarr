"""Guards on `.github/workflows/claude-code-review.yml` -- the reviewer's settings.

Companion to `tests/test_require_review_workflow.py` (the plumbing and the
cross-file agreements) and `tests/test_review_gate_decision.py` (the jq
verdict). This module guards the reviewer's *configuration*: the five values
that each fail quietly, and the trigger list.

Ported from `tclancy/parsons-remote`'s `tests/test_review_workflow_config.py`,
which came from itguy's, which was written after that repo's copy of this
config had been wrong three times. Each was found by reading a failed run
rather than by anything in the repo, and each cost a red gate and ~$0.50 --
three of the five measured runs produced a complete review and had it
discarded by the action's post-check, which is worse than producing nothing.
This repo's copy carries those settings, so it inherits the guards before it
inherits the failures.

THIS MODULE WAS DROPPED FROM THE FIRST DRAFT OF THE PORT, and the comment in
the workflow was rewritten to cite `test_require_review_workflow.py` instead --
a module containing zero occurrences of `track_progress`, `--max-turns`,
`allowed_bots` or the inline-comment tool name. Measured in review:
`track_progress: true` -> `false` survived all 136 tests, and that setting is
load-bearing twice over by the workflow's own account (it registers the
inline-comment MCP server, and it is the reason `require-review.yml` listens
for `issue_comment: edited`). The citation is now true.

WHAT IS NOT PORTED, declared rather than left to omission. parsons-remote's
module is 509 lines; roughly half is about **that repo's own run history** --
a table of its three `workflow validation skip` runs with their durations and
PR numbers, cross-checked against prose in its `CLAUDE.md` and a row in its
`GLOSSARY.md`. None of it transfers: this repo has no `CLAUDE.md`, no
`GLOSSARY.md`, and **zero recorded runs of this workflow**, so there is no run
history to keep prose in step with. If a validation skip ever happens here,
that half is what to port.

Deliberately parsed as text rather than YAML, for the reason given at length in
`test_require_review_workflow.py`: these are claims about the file's text, and
`yaml.safe_load` drops comments and normalises what a block scalar looked like.

The cost of that choice is that `claude_args` must stay a single-line scalar.
Reformatted to the block form upstream's own template uses (`claude_args: >-`
over several lines) these go RED even with correct values -- the safe
direction, but the message would read as "someone deleted TodoWrite".
`extract_claude_args` detects that case and says so instead.
"""

import re
import textwrap
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "claude-code-review.yml"


def extract_claude_args(text: str) -> str:
    """Return the `claude_args:` line from a workflow document.

    Takes the document rather than reading `WORKFLOW` so its own behaviour is
    testable against synthetic input -- the comment-skipping below is the whole
    reason this file does not just grep, and a guard on it that can only be fed
    the real workflow is true by construction rather than by test.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("claude_args:"):
            continue
        if line[:1] not in {" ", "\t"}:
            pytest.fail(
                "claude_args: is at column 0 -- de-indented out of the step's `with:` "
                "block, which leaves the action with no allow-list and no turn cap. "
                "YAML still parses."
            )
        value = stripped[len("claude_args:") :].strip()
        # Any `>`/`|` form, not an enumerated set: `>-`, `|+`, `>2` and
        # `|- # note` are all block scalars, and a set-membership check waves
        # through every spelling it forgot.
        if not value or value[0] in {">", "|"}:
            spelling = value or "(empty)"
            pytest.fail(
                f"claude_args: was reformatted to a multi-line block scalar ({spelling!r}). "
                "These guards read a single-line scalar; fold it back, or teach "
                "extract_claude_args to join continuation lines."
            )
        return stripped
    pytest.fail("no claude_args: line in the review workflow")


def pull_request_types(text: str) -> set[str]:
    """The `types:` list on the `pull_request` trigger, and only that one.

    Scoped deliberately. This file has two `types:` keys -- `pull_request`'s
    and `issue_comment: types: [created]` -- and a bare `^\\s*types:` regex
    takes whichever appears first, which is `pull_request`'s today only because
    of the order the two blocks happen to be written in. Under that regex,
    *deleting* the `types:` line passes the "synchronize is off" guard while
    GitHub falls back to its default `[opened, synchronize, reopened]`, which
    is the once-per-push burn metaframework#320 §5 cut. Returning an empty set
    for an absent list is what makes that case fail the `reopened` guard.
    """
    block = re.search(r"^\s*pull_request:\s*$(.*?)^\s{0,2}\w", text, re.MULTILINE | re.DOTALL)
    assert block, "no `pull_request:` trigger block in the review workflow"
    types = re.search(r"^\s*types:\s*\[([^\]]*)\]", block.group(1), re.MULTILINE)
    if not types:
        return set()
    return {t.strip() for t in types.group(1).split(",") if t.strip()}


@pytest.fixture(scope="module")
def body() -> str:
    """The reviewer workflow's text."""
    assert WORKFLOW.is_file(), f"workflow not found: {WORKFLOW}"
    return WORKFLOW.read_text()


@pytest.fixture(scope="module")
def claude_args() -> str:
    """The `claude_args:` value, with comment lines excluded.

    The measurements justifying the current settings live in a comment block
    directly above this line and mention both `TodoWrite` and `--max-turns`.
    A naive grep of the whole file matches that prose and passes no matter what
    the setting says.
    """
    assert WORKFLOW.is_file(), f"workflow not found: {WORKFLOW}"
    return extract_claude_args(WORKFLOW.read_text())


class TestTheReviewerIsAGreenNoOpUntilItIsProvisioned:
    """The guard whose absence was this port's one Critical finding.

    `claude-code-action` validates its environment before doing any work and
    fails the job when no token is present. This repo has no
    `CLAUDE_CODE_OAUTH_TOKEN` and no Claude GitHub App, and neither is
    settable by an agent -- so without the `Is the reviewer token configured?`
    step every PR here would carry a red X with a remedy only Tom can apply.

    THE OMISSION WAS INVISIBLE TO CI, which is why it gets a guard rather than
    a comment. Until this workflow is on the default branch, the action's token
    exchange fails with `workflow_not_found_on_default_branch`, which it
    converts into a green **workflow validation skip**. So the PR introducing
    the file goes green either way; the red arrives on the next PR.
    """

    def test_the_token_gate_step_exists(self, body: str):
        assert re.search(r"^\s*- name: Is the reviewer token configured\?\s*$", body, re.MULTILINE)
        assert re.search(r"^\s*id: gate\s*$", body, re.MULTILINE)

    def test_the_gate_reads_the_secret_through_env(self, body: str):
        # A job-level `if:` cannot see `secrets`, which is the whole reason
        # this is a step. Reading it through `env:` is also what keeps the
        # expression out of the `run:` body.
        assert re.search(
            r"^\s*REVIEWER_TOKEN: \$\{\{ secrets\.CLAUDE_CODE_OAUTH_TOKEN \}\}\s*$",
            body,
            re.MULTILINE,
        )

    def test_the_gate_sets_both_outcomes(self, body: str):
        step = self._gate_step(body)
        assert 'if [ -n "${REVIEWER_TOKEN}" ]; then' in step, (
            f"the gate does not test the token at all; step is:\n{step}"
        )
        assert 'echo "ready=yes" >> "$GITHUB_OUTPUT"' in step
        assert 'echo "ready=no" >> "$GITHUB_OUTPUT"' in step

    def test_the_gate_branches_are_not_invertible_silently(self, body: str):
        """Swapping `ready=yes` and `ready=no` makes the reviewer skip forever.

        A one-token edit, and the one that would be hardest to notice: on an
        unprovisioned repo the result is identical to the correct behaviour
        today, and it only diverges the moment Tom provisions -- at which point
        the gate silently never runs again while reading as installed.

        Asserted as a LINE SEQUENCE for the same reason the two inversion
        guards in `test_require_review_workflow.py` are: the claim is about
        which arm each assignment sits in.
        """
        lines = [ln.strip() for ln in self._gate_step(body).splitlines() if ln.strip()]
        anchor = 'if [ -n "${REVIEWER_TOKEN}" ]; then'
        assert anchor in lines
        i = lines.index(anchor)
        assert lines[i + 1] == 'echo "ready=yes" >> "$GITHUB_OUTPUT"', (
            f"a PRESENT token must enable the reviewer; then-arm is {lines[i + 1]!r}"
        )
        assert lines[i + 2] == "else", f"expected an else, got {lines[i + 2]!r}"
        assert lines[i + 3] == 'echo "ready=no" >> "$GITHUB_OUTPUT"', (
            f"an ABSENT token must skip the reviewer; else-arm is {lines[i + 3]!r}"
        )

    def test_the_unprovisioned_notice_names_the_remedy(self, body: str):
        step = self._gate_step(body)
        assert "::notice::" in step, "a silent skip is indistinguishable from a broken run"
        assert "CLAUDE_CODE_OAUTH_TOKEN" in step

    #: Every step that must not run without a token. The checkout is on the
    #: list because it resolves `steps.pr.outputs.sha`, which is empty when the
    #: resolve step was skipped -- and an empty `ref:` makes `actions/checkout`
    #: silently take the triggering ref.
    GUARDED_STEPS = ("Resolve PR head SHA", "Run Claude Code Review", "uses: actions/checkout@")

    @pytest.mark.parametrize("marker", GUARDED_STEPS)
    def test_every_working_step_is_gated_on_the_token(self, body: str, marker: str):
        block = self._step_containing(body, marker)
        assert "if: steps.gate.outputs.ready == 'yes'" in block, (
            f"the step containing {marker!r} runs regardless of provisioning:\n{block}"
        )

    def test_the_guarded_step_count_matches_the_steps_in_the_job(self, body: str):
        """Completeness, not a floor.

        `GUARDED_STEPS` is a hand-written list, and a hand-written list cannot
        see a NEW step that also needs the guard. So count the conditions
        against the steps: every step in the job except the gate itself must
        carry one.
        """
        # `(?:\s+#.*)?` because the checkout step carries a ` # v7.0.1`
        # version comment; without it the `uses:` arm matched nothing and the
        # count came back 3, which would have hidden a fourth unguarded step
        # rather than reported it. Caught by this test's own first run.
        steps = re.findall(
            r"^      - (?:name: (.+?)|uses: (\S+))(?:\s+#.*)?\s*$", body, re.MULTILINE
        )
        assert len(steps) == 4, f"the job no longer has four steps, it has {len(steps)}: {steps}"
        guards = re.findall(r"^        if: steps\.gate\.outputs\.ready == 'yes'\s*$", body, re.M)
        assert len(guards) == len(steps) - 1, (
            f"{len(steps)} steps but {len(guards)} token guards -- every step but the "
            "gate itself needs one, and a new step added without one runs unprovisioned"
        )

    @staticmethod
    def _gate_step(body: str) -> str:
        start = body.index("- name: Is the reviewer token configured?")
        nxt = body.index("- name: Resolve PR head SHA", start)
        return body[start:nxt]

    @staticmethod
    def _step_containing(body: str, marker: str) -> str:
        """The `      - ` step block holding *marker*.

        Sliced on the job's step indent, so an assertion about one step cannot
        be satisfied by a neighbour's `if:` -- which is the defect shape this
        whole port kept finding.
        """
        # Anchored on the step indent, NOT on the bare marker. Every step
        # name in this file also appears in the header prose -- the WHAT
        # DIFFERS block names "the `Resolve PR head SHA` step" -- and a
        # comment at column 0 has no preceding `\n      - `, so searching for
        # the bare marker found the paragraph and `rindex` raised. Measured
        # when this guard was first run.
        for prefix in (f"      - name: {marker}", f"      - {marker}"):
            start = body.find(prefix)
            if start != -1:
                break
        else:
            raise AssertionError(f"no step at the job's step indent holding {marker!r}")
        nxt = body.find("\n      - ", start + 1)
        return body[start:] if nxt == -1 else body[start : nxt + 1]

    def test_the_step_slice_is_one_step(self, body: str):
        # Control for `_step_containing`: a slice spanning the whole job would
        # satisfy every guard above off the first `if:` it found.
        block = self._step_containing(body, "Run Claude Code Review")
        assert "Resolve PR head SHA" not in block
        assert "Is the reviewer token configured?" not in block
        assert len(block) < len(body) / 2


class TestTheReviewGateIsBudgetedForRealReviews:
    def test_todowrite_is_allow_listed(self, claude_args: str):
        """Allow-listed for fleet consistency, and because it is harmless -- an
        in-session scratchpad with no filesystem or GitHub write surface.

        Deliberately NOT justified as the fix for the per-run denials. Three of
        itguy's five measured runs ticked their checklists completely with
        `TodoWrite` absent from this list, which refutes that mechanism; the
        streamed log records a denial *count* and never names the tool. What is
        being denied 6-10 times a run is still unidentified.
        """
        assert "TodoWrite" in claude_args

    def test_max_turns_is_at_least_40(self, claude_args: str):
        """40 is itguy's measurement, not this repo's: under a cap of 25, its
        PR #216 review ran 32 turns, produced a complete review, and was failed
        anyway by the action's own post-check. 32 is the measured requirement
        and 40 is that plus headroom. No review here has ever reached the cap --
        no review here has ever *started*.
        """
        match = re.search(r"--max-turns\s+(\d+)", claude_args)
        assert match, f"no --max-turns in: {claude_args}"
        assert int(match.group(1)) >= 40

    def test_the_inline_comment_tool_is_still_allow_listed(self, claude_args: str):
        """Allow-listed AND registered by track_progress, or inline comments
        silently no-op. Pinned so a future edit to this line cannot drop it."""
        assert "mcp__github_inline_comment__create_inline_comment" in claude_args

    def test_bot_authored_prs_are_still_reviewable(self):
        """The action blocks bot actors by default. Every PR in this repo so far
        was opened by `metaframework-dispatch-bot`, so losing this line makes
        the gate silently skip almost everything while staying green.

        Anchored to the quoted *value*: `.*metaframework-dispatch-bot` also
        matches `allowed_bots: 'nobody'  # was metaframework-dispatch-bot`.
        """
        body = WORKFLOW.read_text()
        assert re.search(
            r"^\s*allowed_bots:\s*['\"]metaframework-dispatch-bot['\"]\s*$", body, re.MULTILINE
        )

    def test_track_progress_is_still_on(self):
        """`--allowed-tools` only FILTERS registered MCP servers; it does not
        register them. Without this the inline-comment tool is denied even
        though the test above passes -- and `require-review.yml`'s
        `issue_comment: edited` trigger exists because of the in-flight comment
        this setting produces, so dropping it breaks the gate's self-heal path
        too. It survived all 136 tests before this module was ported.
        """
        body = WORKFLOW.read_text()
        assert re.search(r"^\s*track_progress:\s*true\s*$", body, re.MULTILINE)

    def test_classify_inline_comments_is_pinned_off(self):
        """Defaults to TRUE upstream and has documented buffering bugs.
        Upstream's own example omits it, which reads as "the default is fine"
        and leaves the buggy path on -- so it is pinned rather than omitted,
        and the pin needs a guard or the next tidy-up drops it as redundant."""
        body = WORKFLOW.read_text()
        assert re.search(r"^\s*classify_inline_comments:\s*false\s*$", body, re.MULTILINE)


class TestTheGateCanStillFireAtAll:
    """Every trigger this gate has.

    This workflow has **zero** recorded runs in this repo -- it is new here --
    so the settings the class above pins have never been exercised against live
    data, and these guards cannot be validated by "the gate went green once".
    Pin the triggers instead: a dropped trigger is the one failure mode that
    would keep that record at zero forever while looking exactly like it does
    now.
    """

    def test_reopened_is_a_trigger(self):
        """`opened` alone cannot re-gate a fix-up push, and `/review` cannot
        either -- a check-run attaches to the SHA of the *event*, so an
        `issue_comment` run lands its verdict on the default branch tip. An
        absent verdict on the real head renders as green, not as unreviewed.
        `tests.yml` calls the same trigger load-bearing for the same reason."""
        assert "reopened" in pull_request_types(WORKFLOW.read_text())

    def test_synchronize_is_still_off(self):
        """Deliberately absent: it fires once per push, which is the exact burn
        metaframework#320 §5 cut. Re-gating is a deliberate close/reopen."""
        types = pull_request_types(WORKFLOW.read_text())
        assert types, (
            "no `types:` list on the pull_request trigger -- GitHub then defaults "
            "to [opened, synchronize, reopened], which turns synchronize back on"
        )
        assert "synchronize" not in types

    def test_cancel_in_progress_is_off(self):
        """MUST stay false. Cancellation happens when a run is *created*, before
        job-level `if:` filtering -- so with it on, any comment on the PR
        (including the review's own `gh pr comment`) spawned an `issue_comment`
        run in this group and cancelled the in-flight review."""
        body = WORKFLOW.read_text()
        assert re.search(r"^\s*cancel-in-progress:\s*false\s*$", body, re.MULTILINE)


class TestTheGuardsReadTheSettingNotTheComment:
    """A guard aimed at the wrong text reads exactly like a passing one."""

    def test_a_commented_out_claude_args_is_not_returned(self):
        """The property `extract_claude_args` exists for, tested against input
        that can actually express the failure. Asserting it on the real
        workflow instead is true by construction -- the function only ever
        returns a line that already passed the `claude_args:` prefix test -- so
        it would pass for any workflow content whatsoever.
        """
        document = textwrap.dedent("""\
            with:
              # claude_args: '--allowed-tools "TodoWrite"' --max-turns 999
              claude_args: '--allowed-tools "Real"' --max-turns 40
        """)
        assert (
            extract_claude_args(document)
            == "claude_args: '--allowed-tools \"Real\"' --max-turns 40"
        )

    def test_a_block_scalar_claude_args_fails_loudly(self):
        # The other refusal, driven. Without this the branch is unreachable
        # from any test and the failure message it carries is never read.
        with pytest.raises(BaseException, match="block scalar"):
            extract_claude_args("with:\n  claude_args: >-\n    --max-turns 40\n")

    def test_a_dedented_claude_args_fails_loudly(self):
        with pytest.raises(BaseException, match="column 0"):
            extract_claude_args("claude_args: '--max-turns 40'\n")

    def test_the_comment_block_alone_would_satisfy_a_naive_grep(self):
        """If this fails, the prose moved and the fixture's reason for existing
        should be re-checked -- not the assertion loosened."""
        comments = [
            line for line in WORKFLOW.read_text().splitlines() if line.strip().startswith("#")
        ]
        assert any("TodoWrite" in line for line in comments)
        assert any("--max-turns" in line for line in comments)

    def test_the_pull_request_type_slice_ignores_the_issue_comment_block(self):
        # Control for `pull_request_types`: a regex taking the FIRST `types:`
        # in the file reads `[created]` here and the synchronize guard passes
        # vacuously.
        synthetic = (
            "on:\n"
            "  issue_comment:\n"
            "    types: [created]\n"
            "  pull_request:\n"
            "    types: [opened, reopened]\n"
            "jobs:\n"
        )
        assert pull_request_types(synthetic) == {"opened", "reopened"}

    def test_a_deleted_types_line_reads_as_empty_rather_than_as_safe(self):
        # The case the docstring names: GitHub's default turns `synchronize`
        # back on, so an absent list must not satisfy "synchronize is off".
        synthetic = "on:\n  pull_request:\n  issue_comment:\n    types: [created]\njobs:\n"
        assert pull_request_types(synthetic) == set()
