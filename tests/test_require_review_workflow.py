"""Guards on `.github/workflows/require-review.yml` and its wiring.

Companion to three others: `tests/test_ci_workflow.py` (the TEST gate,
`tests.yml`), `tests/test_review_gate_decision.py` (the *decision logic*), and
`tests/test_review_workflow_config.py` (the *reviewer's settings*, including
the token guard that keeps it a green no-op until Tom provisions it). This
module guards the fourth thing: the plumbing that connects the reviewer to the
status, the input document the gate is handed, and the cross-file agreements
that nothing else can see.

Deliberately parsed as TEXT rather than as YAML, and the reason here is not the
one the source copy gives. parsons-remote's copy says its repo declares no yaml
dependency; this one does (`pyyaml` is in the `dev` group and
`tests/test_ci_workflow.py` uses it). The reason is that most of the claims
below are claims about the file's *text* -- which arm a shell assignment sits
in, whether a comment is present, whether a `run:` body is valid bash -- and
`yaml.safe_load` destroys every one of them: it drops comments, normalises
ordering and hands back a block scalar with no indication of where in the file
it came from.

Several assertions here are *absence* assertions -- "this token does not appear
in that block". An absence assertion over a file that happens to be clean is
indistinguishable from a tautology, so each detector used below is also driven
to a failure on a synthetic input in ``TestTheDetectorsCanFail``. Without that,
a future refactor could break the extractor and every guard in this module
would go green over whatever it was supposed to catch.

TWO GUARDS FROM THE SOURCE COPY ARE DELIBERATELY NOT HERE, said out loud rather
than silently dropped:

* **The GLOSSARY.md guards.** This repo has no `GLOSSARY.md`, so it is
  pre-pilot for the vocabulary convention and there is no row to pin. If one is
  ever added, `dispatch/require-review` and the `re-gate` / `/review`
  distinction are the two entries it owes.
* **The reviewer's expression-in-`run:` exemption.** parsons-remote carries a
  test asserting that `claude-code-review.yml` *does* interpolate `${{ }}` into
  a shell script, because fixing it there trips the action's anti-tamper
  validation. That reasoning is about a file already on a default branch. This
  repo's copy is new, so it was written with the values passed through `env:`
  and the ban below applies to BOTH workflows with no exemption. See that
  file's WHAT DIFFERS header.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
GATE_WORKFLOW = WORKFLOWS / "require-review.yml"
REVIEWER_WORKFLOW = WORKFLOWS / "claude-code-review.yml"
#: lintarr's test gate is `tests.yml`; the source copy's was `ci.yml`. It is
#: read here only as a third real file for the `active_lines` bounds check and
#: for the shared action pin -- its own behaviour is guarded by
#: `tests/test_ci_workflow.py`, and nothing in this module may assert about its
#: contents, because this PR does not touch it.
CI_WORKFLOW = WORKFLOWS / "tests.yml"
GATE_LOGIC = REPO_ROOT / ".github" / "review-gate.jq"

#: The commit-status context the gate publishes under. Hard-coded in the
#: workflow and referenced in prose; pinned here so a rename cannot leave a PR
#: carrying two contexts, one of them permanently stale and red.
STATUS_CONTEXT = "dispatch/require-review"

#: The PR authors the gate exempts, and the same set the reviewer's `if:`
#: declines. Shared rather than written twice -- a divergence here is how a
#: gate starts demanding a review the reviewer refuses to perform.
EXEMPT_AUTHORS = ("dependabot[bot]",)

#: The reviewer additionally declines PRs whose title starts with this, and the
#: gate deliberately does NOT exempt them. Asserted to be exactly one prefix:
#: if a `docs` prefix is ever added over there, this goes red and the decision
#: gets made on purpose instead of by omission.
REVIEWER_TITLE_PREFIXES = ("chore",)

#: The GitHub expression opening token. Spelled by concatenation so this
#: constant is not itself an expression when a tool scans this file.
EXPR_OPEN = "${" + "{"


def run_blocks(text: str) -> list[str]:
    """Every `run:` block body in a workflow, as text.

    Handles the block-scalar form (`run: |`) and the one-line form. Returns the
    bodies only, so an assertion about a `run:` body cannot accidentally be
    satisfied or violated by an `env:` value on a neighbouring line -- which is
    the whole point, since passing values through `env:` is the *fix* this
    module asserts, not the defect.
    """
    blocks: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = re.match(r"^(\s*)-?\s*run:\s*(\|-?|>-?)?\s*(.*)$", lines[i])
        if not match:
            i += 1
            continue
        indent, scalar, inline = match.group(1), match.group(2), match.group(3)
        if scalar is None:
            blocks.append(inline)
            i += 1
            continue
        body: list[str] = []
        i += 1
        while i < len(lines):
            line = lines[i]
            if line.strip() and not line.startswith(indent + " "):
                break
            body.append(line)
            i += 1
        blocks.append("\n".join(body))
    return blocks


def active_lines(text: str) -> str:
    """``text`` with comments stripped, for assertions about what the file DOES.

    An absence assertion over raw text cannot tell a setting from a paragraph
    explaining why that setting is wrong -- and these workflows carry long
    explanatory comments that name the wrong spellings on purpose. Stripping
    comments is what makes "this file does not key on X" mean it.

    Driven to a failure in ``TestTheDetectorsCanFail``.
    """
    out = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        if "#" in stripped and not line.lstrip().startswith("#"):
            # An end-of-line comment (` # v7.0.1`). Cuts on whitespace-then-#.
            #
            # This is NOT comment-aware: `echo "see #23"` would be cut to
            # `echo "see`. Bounded instead by
            # `test_active_lines_alters_nothing_but_comments` below, which
            # checks over all three real workflows that the only non-comment
            # text it touches is the ` # vN.N.N` version comment on a `uses:`
            # line -- which is the intent. Keep that test green or this helper
            # stops meaning what the guards assume.
            stripped = re.split(r"\s#", stripped, maxsplit=1)[0]
        out.append(stripped)
    return "\n".join(out)


def trigger_note(text: str, event: str) -> str:
    """The commentary inside one `on:` trigger's block, as collapsed prose.

    Slices from `^  <event>:` to that block's own `types:` line, strips the `#`
    markers and collapses whitespace, so a phrase spanning several comment
    lines can be matched -- and, the load-bearing half, so a phrase that lives
    somewhere ELSE in the file cannot satisfy an assertion about this block.

    That is not a hypothetical. The first version of
    ``test_the_issue_comment_default_branch_limitation_is_recorded`` asserted
    three substrings against the whole file and all three were already in it
    before the note existed, so deleting the note left the test green
    (`claude[bot]`, parsons-remote PR #26). Driven to a failure in
    ``TestTheDetectorsCanFail``.
    """
    # The capture is bounded to BLANK AND COMMENT lines, not to `.*`. With
    # `(?:.*\n)*?` a trigger whose own `types:` line went missing did not
    # raise: the non-greedy group walked past this block, past the next
    # trigger's commentary, and bound to *that* trigger's `types:` -- so the
    # slice silently answered about the wrong block and the assertion below was
    # unreachable for every trigger but the last. Only comments and blanks
    # legitimately sit between an `on:` key and its `types:`, so requiring that
    # is also the tighter claim.
    match = re.search(
        rf"^  {re.escape(event)}:\n((?:[ \t]*(?:#.*)?\n)*?)[ \t]*types: \[",
        text,
        re.MULTILINE,
    )
    assert match, f"no `{event}:` trigger with a `types:` list directly below its commentary"
    uncommented = [re.sub(r"^\s*#\s?", "", line) for line in match.group(1).splitlines()]
    return re.sub(r"\s+", " ", " ".join(uncommented)).strip()


def step_body(text: str, name: str) -> str:
    """One named step's text, from its `- name:` to the next step's.

    **The `if: failure()` fallback step re-spells lines the earlier steps
    already have** -- the status context and the `gh api -X POST` from the
    publish step, the head-SHA lookup from the resolve step, the `|| true` from
    the trap. So a file-wide `assert "..." in gate` is satisfied by whichever
    copy a mutation did not touch, and says nothing about the other one. Four
    mutants survived 89 green tests in bluelinky's copy for exactly this
    reason, two of them silently restoring the stale-verdict failure the
    fallback exists to prevent. (Not *every* line: `set -uo pipefail` and the
    `::warning::` are the fallback's alone.)

    **Pass `active_lines(text)`, not raw text** -- that is the convention
    `_fallback_step` documents and this helper does not enforce. Measured in
    review of parsons-remote PR #26: with raw text, commenting out the publish
    step's `-f context=` line left its slice assertion green. The slice also
    runs to the *next* `- name:`, so it carries the following step's leading
    comment block; stripping comments is what makes that harmless.

    Slice first, then assert inside the slice. Driven to a failure in
    ``TestTheDetectorsCanFail``.
    """
    assert f"- name: {name}" in text, f"no step named {name!r}"
    start = text.index(f"- name: {name}")
    # `rfind`, not `rindex`: a `- name:` on the very first line has no preceding
    # newline and `rindex` raises there, which would make this helper blow up
    # rather than slice. Caught by its own control
    # (`test_step_body_is_not_fooled_by_a_name_at_a_deeper_indent`).
    column = start - (text.rfind("\n", 0, start) + 1)
    nxt = text.find("\n" + " " * column + "- name: ", start + 1)
    return text[start:] if nxt == -1 else text[start:nxt]


@pytest.fixture(scope="module")
def gate() -> str:
    return GATE_WORKFLOW.read_text()


class TestTheGateFilesExist:
    def test_the_workflow_exists(self):
        assert GATE_WORKFLOW.is_file()

    def test_the_decision_logic_exists_where_the_workflow_looks_for_it(self, gate):
        assert GATE_LOGIC.is_file()
        # The path appears twice: once in `sparse-checkout`, once in the `jq -f`
        # invocation. A sparse checkout of the wrong path yields a job that
        # cannot find its own gate, which the ERR trap turns into `error`.
        rel = ".github/review-gate.jq"
        assert gate.count(rel) >= 2, "the workflow must both check out and run the gate file"
        assert f"sparse-checkout: {rel}" in gate
        assert f"jq -f {rel}" in gate

    def test_the_reviewer_half_exists_too(self):
        # Option (a) is both halves. A status with no reviewer is a red X whose
        # only remedy nobody can perform, which is the shape #35 rejected.
        assert REVIEWER_WORKFLOW.is_file()

    def test_the_pre_existing_test_gate_is_still_there(self):
        # This PR adds two workflows beside `tests.yml` (#47/#48) and must not
        # displace it. Its own settings are guarded by
        # `tests/test_ci_workflow.py`; this is only the "all three coexist"
        # claim, which neither of the other modules makes.
        assert CI_WORKFLOW.is_file()


class TestTheTriggersCoverEveryWayAVerdictCanChange:
    @pytest.mark.parametrize(
        "event,types",
        [
            ("pull_request", ["opened", "synchronize", "reopened"]),
            ("issue_comment", ["created", "edited"]),
            ("pull_request_review", ["submitted"]),
        ],
    )
    def test_each_trigger_and_its_types_are_declared(self, gate, event, types):
        assert re.search(rf"^  {re.escape(event)}:", gate, re.MULTILINE), f"{event} trigger missing"
        declared = re.search(
            rf"^  {re.escape(event)}:\n(?:.*\n)*?\s+types: \[([^\]]+)\]", gate, re.MULTILINE
        )
        assert declared, f"{event} declares no types"
        assert {t.strip() for t in declared.group(1).split(",")} == set(types)

    def test_synchronize_is_on_here_even_though_the_reviewer_drops_it(self, gate):
        # The asymmetry is the design: a fix-up push moves the head, the
        # reviewer does not re-run (metaframework#320 §5 burn cut), and this
        # status therefore goes red on the new head. Asserting both halves so
        # the pair cannot drift into agreeing by accident.
        assert "synchronize" in gate
        reviewer = REVIEWER_WORKFLOW.read_text()
        pr_types = re.search(
            r"^  pull_request:\n(?:.*\n)*?\s+types: \[([^\]]+)\]", reviewer, re.MULTILINE
        )
        assert pr_types, "the reviewer declares no pull_request types"
        assert "synchronize" not in pr_types.group(1)

    def test_the_reviewer_can_still_be_re_gated(self, gate):
        # `reopened` on the reviewer is the only on-demand trigger an agent can
        # fire that carries the PR head, and it is also what `tests.yml`'s own
        # note calls load-bearing for the same reason. Dropping it here would
        # leave a fix-up push with no path to a reviewed head at all.
        reviewer = REVIEWER_WORKFLOW.read_text()
        pr_types = re.search(
            r"^  pull_request:\n(?:.*\n)*?\s+types: \[([^\]]+)\]", reviewer, re.MULTILINE
        )
        assert "reopened" in pr_types.group(1), (
            "without `reopened` the reviewer cannot be re-run on a fixed head without "
            "moving the head SHA, and `gh run rerun` replays a stale merge commit"
        )

    #: A phrase only the `issue_comment` caveat carries. Pinned as a constant
    #: so the control in ``TestTheDetectorsCanFail`` asserts against the same
    #: text the guard does, rather than against a hand-copied duplicate that
    #: could drift into agreeing with a broken slice.
    ISSUE_COMMENT_CAVEAT = "ALWAYS executes the copy of the workflow on the **default branch**"

    def test_the_issue_comment_default_branch_limitation_is_recorded(self, gate):
        """The `/review` remedy is inert until this file is on the default branch.

        An `issue_comment` run always executes the default branch's copy of a
        workflow, because the event is attached to an issue rather than a PR
        ref. Observed on parsons-remote PR #26, this gate's first PR anywhere:
        the reviewer posted after the head commit and the status stayed red
        because no run fired.

        Guarded as *documentation* rather than behaviour, because there is
        nothing in this file that can change it -- but a future reader debugging
        a red status that `/review` will not clear needs to find this, and
        `review-gate.jq`'s refusal text points them at `/review` unqualified.

        Asserted against the `issue_comment` block's OWN commentary, not
        against the file. The first version of this test asked whether
        `"default branch"`, `"issue_comment"` and `"/review"` appeared anywhere
        in `require-review.yml` -- and all three already did before the caveat
        was written: `issue_comment` is the trigger key, `/review` is in the
        job's `if:`, and "default branch" is in the pre-existing self-grading
        note. **Deleting the whole caveat left all three true.**
        """
        note = trigger_note(gate, "issue_comment")
        assert self.ISSUE_COMMENT_CAVEAT in note, (
            "the issue_comment trigger must carry the default-branch caveat in its own block"
        )
        # The refusal text names `/review`, so the caveat has to say what
        # happens to `/review` -- and say it where a reader lands.
        assert "/review" in note
        # The remedy that DOES work before this file is on `main`. Without it
        # the caveat states a problem and leaves the reader stuck.
        assert "re-gate" in note

    def test_edited_is_declared_because_the_progress_comment_is_edited_in_place(self, gate):
        issue_comment = re.search(
            r"^  issue_comment:\n(?:.*\n)*?\s+types: \[([^\]]+)\]", gate, re.MULTILINE
        )
        assert "edited" in issue_comment.group(1)


class TestTheStatusLandsOnThePrHead:
    PUBLISH_STEP = "Evaluate the gate and publish the status to the PR head"

    def test_it_posts_a_commit_status_rather_than_relying_on_its_own_check(self, gate):
        # `active_lines` first, then slice: raw text leaves a commented-out
        # `-f context=` line's substring in place and this assertion read it as
        # present (review of parsons-remote PR #26).
        step = step_body(active_lines(gate), self.PUBLISH_STEP)
        assert "statuses/${SHA}" in step, "the status must be POSTed to the resolved head SHA"
        # Sliced to the publishing step, not asserted against the file. The
        # `if: failure()` fallback carries its own `context="..."` line, so the
        # file-wide version of this assertion was satisfied by that copy --
        # renaming the context HERE left it green, which publishes the real
        # verdict under a new context and leaves whatever `dispatch/
        # require-review` the PR already had standing. From bluelinky PR #53,
        # where that mutant survived 89 green tests.
        assert f'context="{STATUS_CONTEXT}"' in step

    def test_every_status_post_site_names_the_same_context(self, gate):
        # The other half of the slice above, and the one that scales: each
        # status POST must name the SAME context, so count them rather than
        # slicing once per site.
        #
        # Counted on `statuses/` POSTs specifically, NOT on every
        # `gh api -X POST`. The looser count was right at two today and would
        # have gone red on an unrelated future POST -- a comment, a label --
        # reporting "expected two status POSTs" about a line that posts no
        # status (review of parsons-remote PR #26).
        active = active_lines(gate)
        sites = re.findall(r'gh api -X POST "repos/\$\{REPO\}/statuses/\$\{(?i:sha)\}"', active)
        assert len(sites) == 2, (
            f"expected two status POSTs (publish + failure fallback), found {len(sites)}"
        )
        assert active.count(f'context="{STATUS_CONTEXT}"') == len(sites), (
            "every status POST must publish under the same context; a disagreement "
            "leaves one of them writing a context nothing reads"
        )

    def test_it_requests_the_permission_that_post_needs(self, gate):
        assert "statuses: write" in gate

    def test_concurrency_does_not_cancel(self, gate):
        # A *cancelled* run paints the PR red even when the authoritative
        # status is green (metaframework#511) -- and `cancelled` scores NOT_RUN
        # in `dispatch pr-gates`, which is the same reason `tests.yml` leaves
        # cancellation off for a `pull_request`.
        assert "cancel-in-progress: false" in gate

    def test_the_reviewer_does_not_cancel_either(self):
        # Its own history, not an inherited style: cancellation happens when a
        # run is CREATED, before job-level `if:` filtering, so any comment on
        # the PR -- including the review's own `gh pr comment` -- cancelled the
        # in-flight review job.
        assert "cancel-in-progress: false" in REVIEWER_WORKFLOW.read_text()

    def test_the_issue_comment_path_resolves_a_head_sha(self, gate):
        # `issue_comment` carries no pull_request object; without the lookup the
        # status would be posted to an empty SHA.
        assert 'sha="$(gh api "repos/${REPO}/pulls/${number}" --jq .head.sha)"' in gate


class TestFailureModesPublishSomethingRatherThanNothing:
    def test_an_unset_reviewer_secret_publishes_pending(self, gate):
        # On THIS repo this is the expected state, not a corner: there is no
        # `CLAUDE_CODE_OAUTH_TOKEN` and no Claude GitHub App, so `pending` with
        # a named remedy is what the gate reports until Tom provisions both.
        assert "publish pending" in gate
        # The literal, not the whole `if`: the condition carries a `!=
        # "success"` conjunct as well, asserted by
        # `test_the_pending_arm_only_fires_on_a_non_success_verdict`. Pinning
        # the full line here made this test the thing that broke when that
        # conjunct was added, which is the wrong guard failing.
        assert '[ -z "${REVIEWER_TOKEN}" ]' in active_lines(gate)
        assert "CLAUDE_CODE_OAUTH_TOKEN" in gate

    def test_an_internal_failure_publishes_error_via_a_trap(self, gate):
        # Without the trap the step dies under `set -e` and NO status is posted,
        # so the PR keeps its previous verdict -- which on a re-evaluation of an
        # approved-then-pushed PR is a stale GREEN.
        # `active_lines`, not the raw text: commenting the trap out with a
        # leading `#` leaves the substring in place, and the raw-text version of
        # this assertion reported the disabled trap as present (mutation round,
        # 2026-11-01).
        trap = re.search(r"trap '(.*)' ERR", active_lines(gate))
        assert trap, "no ERR trap publishing a status"
        assert trap.group(1).startswith("publish error"), (
            f"the ERR trap must publish `error`; it runs {trap.group(1)[:60]!r}"
        )
        # Read out of the TRAP's own command, not out of the file. There are
        # three `|| true` in this workflow -- this one, and two in the
        # `if: failure()` fallback -- so the file-wide version of this
        # assertion was satisfied by the fallback's copies and said nothing
        # about recursion here. From bluelinky PR #53.
        assert trap.group(1).rstrip().endswith("|| true"), (
            "a failing publish inside the trap must not recurse"
        )

    def test_the_shell_is_strict_in_every_run_block(self, gate):
        # `active_lines` first: `# set -euo pipefail` leaves the substring in
        # place, and the raw-text version of this reported a commented-out
        # strict mode as present (mutation round, 2026-11-01). The last step is
        # `set -uo pipefail` on purpose -- it must keep going after a failure to
        # publish `error` -- so it is matched by the weaker prefix.
        for block in run_blocks(active_lines(gate)):
            assert re.search(r"set -e?uo pipefail", block), f"non-strict run block: {block[:60]!r}"

    def test_the_reviewers_run_blocks_are_strict_too(self):
        """A divergence from the source copy, pinned so it cannot be undone quietly.

        parsons-remote's `claude-code-review.yml` resolve step has no `set`
        line. That matters: a failing `gh api` on the `issue_comment` path
        leaves `sha` empty, `actions/checkout` with an empty `ref:` falls back
        to the triggering ref, and the reviewer then reads the WRONG CODE while
        reporting success. This copy was written strict, so this is a guard on
        a fix rather than on an inherited style.
        """
        blocks = run_blocks(active_lines(REVIEWER_WORKFLOW.read_text()))
        assert blocks, "the reviewer has no run: blocks at all, so this proves nothing"
        for block in blocks:
            assert re.search(r"set -e?uo pipefail", block), (
                f"non-strict run block in the reviewer: {block[:60]!r}"
            )

    def test_the_reviewer_refuses_an_empty_head_sha(self):
        # The other half of the same fix: strict mode alone does not catch a
        # `gh api` that succeeds and prints nothing.
        reviewer = active_lines(REVIEWER_WORKFLOW.read_text())
        assert 'if [ -z "$sha" ]; then' in reviewer, (
            "an empty `ref:` makes actions/checkout silently review the triggering ref"
        )


class TestTheForkDiscriminatorIsTheOneThatAnswersTheQuestion:
    def test_it_compares_head_repo_full_name(self, gate):
        assert "head.repo.full_name" in active_lines(gate)

    def test_it_does_not_key_on_head_repo_fork(self, gate):
        # `.fork` is a property of the REPO, so in a repo that is itself a fork
        # it is true for every same-repo branch and skips 100% of its PRs while
        # looking like a correctly-installed gate. True of bluelinky; not of
        # this repo today (`isFork` is false, 2026-11-01), which is exactly why
        # it is worth pinning before someone "simplifies" it. Read against the
        # active YAML, because the comment above that `if:` names `.fork` on
        # purpose in order to warn the next reader off it.
        assert "head.repo.fork" not in active_lines(gate)


class TestNoGitHubExpressionsInsideRunBlocks:
    """The trap that made bluelinky's copy of this file unparseable.

    GitHub evaluates `${{ … }}` before the shell sees the script, so an
    expression inside a `#` **comment** is still parsed -- and an invalid one
    makes the whole workflow file invalid. The tell was a run that FAILED with
    zero jobs, no annotation and no log, while `check-yaml`, `prettier` and
    `@action-validator/cli` all passed the file.

    Passing values through `env:` instead also removes the command-injection
    surface, which matters because one of the fields this gate reads is the PR
    title.

    **Applies to both workflows here, with no exemption** -- the divergence
    from parsons-remote described in this module's docstring.
    """

    @pytest.mark.parametrize("path", [GATE_WORKFLOW, REVIEWER_WORKFLOW], ids=lambda p: p.name)
    def test_neither_workflow_has_one(self, path):
        blocks = run_blocks(path.read_text())
        assert blocks, f"{path.name} has no run: blocks, so this proves nothing"
        for block in blocks:
            assert EXPR_OPEN not in block, (
                f"{path.name}: a GitHub expression inside a run: block -- pass it "
                f"through env: instead:\n{block[:200]}"
            )

    @pytest.mark.parametrize("path", [GATE_WORKFLOW, REVIEWER_WORKFLOW], ids=lambda p: p.name)
    def test_both_workflows_do_pass_values_through_env(self, path):
        # The positive half. Without it the test above is satisfied by a
        # workflow that reads no event data at all.
        text = path.read_text()
        assert "REPO: ${{ github.repository }}".replace("${{", EXPR_OPEN) in text
        assert "EVENT_NAME:" in text and "PR_HEAD_SHA:" in text


class TestTheTwoHalvesAgreeWithEachOther:
    def test_the_exempt_author_lists_agree(self, gate):
        logic = GATE_LOGIC.read_text()
        reviewer = REVIEWER_WORKFLOW.read_text()
        for author in EXEMPT_AUTHORS:
            assert author in logic, f"{author} not exempt in the decision logic"
            assert author in reviewer, f"{author} not declined by the reviewer"

    def test_the_reviewers_title_prefix_list_is_exactly_chore(self):
        # The gate deliberately does not exempt title prefixes. That choice is
        # only safe while the reviewer declines exactly one, so a new prefix
        # over there must surface as a decision here.
        reviewer = REVIEWER_WORKFLOW.read_text()
        found = set(
            re.findall(r"startsWith\(github\.event\.pull_request\.title, '([^']+)'\)", reviewer)
        )
        assert found == set(REVIEWER_TITLE_PREFIXES), f"reviewer title prefixes drifted: {found}"

    def test_the_gate_does_not_exempt_a_title_prefix(self):
        logic = GATE_LOGIC.read_text()
        exempt = logic[logic.index("def exempt_reason:") :]
        exempt = exempt[: exempt.index(";")]
        assert ".title" not in exempt, (
            "a title prefix is a one-word self-service bypass for any agent"
        )

    def test_both_remedy_spellings_the_reviewer_accepts_also_re_fire_the_gate(self, gate):
        reviewer = REVIEWER_WORKFLOW.read_text()
        spellings = set(
            re.findall(r"contains\(github\.event\.comment\.body, '([^']+)'\)", reviewer)
        )
        assert spellings, "the reviewer accepts no comment remedy at all"
        for spelling in spellings:
            assert f"'{spelling}'" in gate, (
                f"the reviewer runs on {spelling!r} but the gate ignores that comment, "
                "so asking for a review that way would not re-publish the status"
            )

    def test_the_bot_login_the_gate_trusts_is_the_one_the_reviewer_posts_as(self, gate):
        assert "claude[bot]" in GATE_LOGIC.read_text()
        assert "claude[bot]" in gate

    def test_all_three_workflows_pin_the_same_checkout(self):
        """`require-review.yml` claims the three pins move together. Checkable.

        A comment is the least-reviewed line in any diff, and this one makes a
        falsifiable claim -- so it gets a test rather than trust. Three
        different `actions/checkout` SHAs in one repo is how a security bump
        lands in one workflow and silently misses the other two.

        It does NOT assert that Dependabot does the bumping: this repo has no
        `.github/dependabot.yml` at this revision, which the comment says.
        """
        pins = {}
        for path in (GATE_WORKFLOW, REVIEWER_WORKFLOW, CI_WORKFLOW):
            found = re.findall(r"uses: (actions/checkout@[0-9a-f]{40})", path.read_text())
            assert found, f"{path.name} does not check out at a SHA-pinned action"
            pins[path.name] = set(found)
        merged = set().union(*pins.values())
        assert len(merged) == 1, f"actions/checkout is pinned to differing SHAs: {pins}"


class TestTheDependencyOnJqCannotGoQuiet:
    """`test_review_gate_decision.py` shells out to jq. Its control is here.

    parsons-remote solves this with a `jq --version` step in its CI workflow.
    This repo does not, deliberately: `tests.yml` carries three commented
    load-bearing choices and nothing in this PR touches it, and the Python
    control below is strictly broader anyway -- it runs in CI *and* in the
    `pytest` pre-commit hook, where a CI step cannot.

    The control is DRIVEN here rather than asserted as a string match. A test
    that greps another test file for `shutil.which` proves the characters are
    there; this one makes that module's assertion actually fail.
    """

    def test_the_jq_guard_fails_when_jq_is_missing(self, monkeypatch):
        from tests import test_review_gate_decision as decision

        monkeypatch.setattr(decision.shutil, "which", lambda _name: None)
        with pytest.raises(AssertionError, match="jq is required"):
            decision.TestTheBaselineRefuses().test_jq_is_available()

    def test_the_jq_guard_passes_on_this_machine(self):
        # The other direction, so the test above cannot be satisfied by a guard
        # that is simply always red. Also the honest statement of this module's
        # own precondition.
        assert shutil.which("jq") is not None

    def test_the_decision_tests_do_not_skip_themselves_away(self):
        decision_tests = (REPO_ROOT / "tests" / "test_review_gate_decision.py").read_text()
        assert "pytestmark" not in decision_tests, (
            "a module-level skipif would also cover the test asserting jq exists, so "
            "a missing jq would read as 59 skipped and exit 0"
        )


class TestTheGateCannotBeSilentlyDisabled:
    """The mutants that survived the first round, each now pinned.

    Every one of these is a one-token edit that leaves the gate reading as
    correctly installed while it grades nothing. They are the findings this
    port inherits: bluelinky added each guard after an incident, parsons-remote
    carried them forward, and the risk at every hop is carrying the *fixes*
    without the tests that keep them fixed.
    """

    def test_the_cross_repo_branches_are_not_invertible_silently(self, gate):
        # Swapping `fork=false` and `fork=true` makes the gate skip 100% of
        # same-repo PRs and publish nothing at all. It survived the first
        # mutation round with all 85 tests green, because the only assertion
        # about this step was that a string appeared somewhere in the file.
        step = step_body(active_lines(gate), "Is this a cross-repository PR?")
        # Parsed line-by-line rather than with a multiline regex: the thing
        # being asserted is WHICH ARM each assignment sits in, and a line
        # sequence says that more plainly than a pattern.
        body = [ln.strip() for ln in step.splitlines() if ln.strip()]
        anchor = 'if [ "$head_repo" = "$REPO" ]; then'
        assert anchor in body, "could not find the head-repo comparison at all"
        i = body.index(anchor)
        then_arm, else_kw, else_arm = body[i + 1], body[i + 2], body[i + 3]
        assert then_arm == "fork=false", (
            f"a same-repo PR must NOT be treated as cross-repo; then-arm is {then_arm!r}"
        )
        assert else_kw == "else", f"expected an else at this position, got {else_kw!r}"
        assert else_arm == "fork=true", f"a cross-repo PR must be skipped; else-arm is {else_arm!r}"

    def test_the_evaluate_step_only_runs_when_the_pr_is_same_repo(self, gate):
        assert "if: steps.fork.outputs.fork != 'true'" in gate

    @pytest.mark.parametrize("path", [GATE_WORKFLOW, REVIEWER_WORKFLOW], ids=lambda p: p.name)
    def test_every_run_block_is_valid_shell(self, path):
        # Nothing in this repo reads shell embedded in a workflow: there is no
        # shellcheck hook, and `check-yaml` only proves the YAML parses.
        # bluelinky added this after an apostrophe inside a single-quoted jq
        # program closed the quote while `check-yaml` still passed --
        # YAML-valid, shell-broken.
        blocks = run_blocks(path.read_text())
        assert blocks, f"{path.name} has no run: blocks, so this proves nothing"
        for i, block in enumerate(blocks, 1):
            # `check=False` on purpose: a syntax error is the finding, so the
            # non-zero exit must reach the assertion rather than raise.
            proc = subprocess.run(
                ["bash", "-n"], input=block, capture_output=True, text=True, check=False
            )
            assert proc.returncode == 0, (
                f"{path.name}: run block {i} is not valid shell:\n{proc.stderr}"
            )

    @pytest.mark.parametrize("path", [GATE_WORKFLOW, REVIEWER_WORKFLOW], ids=lambda p: p.name)
    def test_every_third_party_action_is_pinned_to_a_full_sha(self, path):
        uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", path.read_text(), re.MULTILINE)
        assert uses, f"{path.name}: the matcher found no `uses:` lines at all"
        for ref in uses:
            _, _, version = ref.partition("@")
            assert re.fullmatch(r"[0-9a-f]{40}", version), (
                f"{ref} is not pinned to a full commit SHA; a tag re-point would run in CI"
            )

    def test_the_action_pin_matcher_does_not_match_a_permission_line(self):
        # Control: `statuses: write` is a `<word>: <word>` line too, and a
        # sloppier matcher reads it as an action with no `@`, which would make
        # the test above fail for the wrong reason -- or, with a laxer
        # assertion, pass over an unpinned action.
        assert (
            re.findall(
                r"^\s*-?\s*uses:\s*(\S+)", "    permissions:\n      statuses: write\n", re.MULTILINE
            )
            == []
        )

    def test_the_paginated_fetches_are_reassembled_into_one_document(self, gate):
        # `--paginate` without `--jq` emits one array PER PAGE, which is not a
        # single JSON document; `--slurpfile` would then bind `$comments[0]` to
        # the first page only and the gate would go red on a PR with more than
        # 100 comments. A false red, but a silent one. Dropping the `| jq -s`
        # survived the first mutation round.
        pipeline = """--paginate --jq '.[]' | jq -s '.'"""
        for endpoint in ("issues/${NUMBER}/comments", "pulls/${NUMBER}/reviews"):
            line = next((ln for ln in gate.splitlines() if endpoint in ln and "gh api" in ln), None)
            assert line, f"no `gh api` fetch for {endpoint}"
            assert pipeline in line, (
                f"{endpoint} is not reassembled with `{pipeline}`:\n  {line.strip()}"
            )

    UNPROVISIONED_ARM = 'if [ "$state" != "success" ] && [ -z "${REVIEWER_TOKEN}" ]; then'

    def _arm(self, gate: str) -> list[str]:
        body = [ln.strip() for ln in active_lines(gate).splitlines() if ln.strip()]
        assert self.UNPROVISIONED_ARM in body, "no unprovisioned-secret arm at all"
        start = body.index(self.UNPROVISIONED_ARM)
        end = body.index("fi", start)
        return body[start + 1 : end]

    def test_the_pending_arm_stops_rather_than_falling_through(self, gate):
        """Without the `exit 0`, `pending` is immediately overwritten.

        Read as the `if ... fi` BLOCK, not as a non-greedy regex from
        `publish pending` onwards: there is a second `exit 0` further down, in
        the `failure()` fallback, so the lazy match found that one and the
        mutant removing this one survived (round 2, 2026-11-01).
        """
        arm = self._arm(gate)
        assert any(ln == "exit 0" for ln in arm), (
            f"the unprovisioned arm must exit rather than continue to evaluate; arm is {arm}"
        )
        assert any(ln.startswith("publish pending") for ln in arm)

    def test_the_pending_arm_slice_is_bounded_by_its_own_fi(self, gate):
        # Control: a slice running to the end of the file would find the
        # fallback step's `exit 0` and the assertion above could not fail.
        assert len(self._arm(gate)) < 6, "the pending arm slice is too wide to mean anything"

    def test_the_unprovisioned_check_runs_after_the_gate_not_before_it(self, gate):
        """An exemption must not depend on provisioning.

        Dependabot runs read a separate secret store, so
        `secrets.CLAUDE_CODE_OAUTH_TOKEN` is empty on a Dependabot event even
        when the Actions secret exists. With the token check first, every
        Dependabot PR published `pending` -- telling Tom to add a secret -- in
        place of the `success` `exempt_reason` grants it. Found in the bot
        review of parsons-remote PR #26.

        Asserted as an ORDERING, because that is the whole fix: the gate's
        verdict has to exist before the token is consulted.
        """
        active = active_lines(gate)
        gate_ran = active.index("jq -f .github/review-gate.jq")
        token_checked = active.index('[ -z "${REVIEWER_TOKEN}" ]')
        assert gate_ran < token_checked, (
            "the token check short-circuits before the gate runs, so the Dependabot "
            "exemption is unreachable on exactly the PRs that need it"
        )

    def test_the_pending_arm_only_fires_on_a_non_success_verdict(self, gate):
        # The other half of the ordering fix. Without the `!= "success"`
        # conjunct, a Dependabot PR would be re-graded `pending` even with the
        # gate already having exempted it.
        assert '[ "$state" != "success" ]' in active_lines(gate)

    def test_the_exemption_lives_in_exactly_one_file(self, gate):
        # The alternative fix -- test the author in shell before the token
        # check -- would put `dependabot[bot]` in both this file and
        # `review-gate.jq`, which is the drift
        # `test_the_exempt_author_lists_agree` exists to catch.
        for author in EXEMPT_AUTHORS:
            assert author not in active_lines(gate), (
                f"{author} is named in the workflow as well as the jq logic; the "
                "exemption must have one source of truth"
            )

    def test_the_issue_comment_arm_requires_the_comment_to_be_on_a_pr(self, gate):
        # Without this the job runs on plain issue comments, where
        # `pulls/${number}` is a 404 and the step dies with no status posted.
        assert "github.event.issue.pull_request" in active_lines(gate)

    @staticmethod
    def _fallback_step(gate: str) -> str:
        """The `if: failure()` step's text, read off the ACTIVE yaml.

        `active_lines` first, and not as belt-and-braces: the comment block
        introducing that step quotes both `if: failure()` and
        `steps.pr.outputs`, so slicing the raw text starts the slice inside the
        prose and the absence assertion below then fails against the paragraph
        explaining why it is there.
        """
        active = active_lines(gate)
        assert "if: failure()" in active, "no failure() fallback step"
        return active[active.index("if: failure()") :]

    def test_a_failure_before_the_publish_step_still_publishes(self, gate):
        # The ERR trap only covers the step it is declared in. A failure in the
        # two resolve steps left the PR holding its previous verdict, which on
        # an approved-then-pushed PR is a stale GREEN.
        tail = self._fallback_step(gate)
        assert "-f state=error" in tail
        assert f'context="{STATUS_CONTEXT}"' in tail
        assert "statuses/${sha}" in tail, "the fallback must post to a resolved head SHA"
        # The fourth of bluelinky PR #53's four surviving guard-step mutants.
        # Its blast radius is genuinely small -- this step runs `set -uo
        # pipefail` with no `-e`, the POST is its last command, and
        # `if: failure()` means the job is already red -- so the guard is for
        # the next editor who adds a command after it, at which point a failing
        # POST would take the step down and publish nothing.
        assert tail.rstrip().endswith("|| true"), (
            "the fallback's POST must not be able to fail the step it is rescuing"
        )

    def test_the_fallback_does_not_trust_the_outputs_of_the_step_that_may_have_failed(self, gate):
        tail = self._fallback_step(gate)
        assert "steps.pr.outputs" not in tail, (
            "those outputs are empty precisely when the resolve step is what broke"
        )

    def test_the_fallbacks_event_branches_are_not_invertible_silently(self, gate):
        """Swapping the fallback's `number=` arms makes it publish nothing.

        On an `issue_comment` event `PR_NUMBER` is empty, so the swapped arms
        set `number=""`, the `pulls/` lookup 404s into its `|| true`, `sha`
        stays empty and the step exits 0 after a `::warning::` -- leaving the
        stale verdict standing, which is the one thing this step exists to
        prevent. It is a two-line edit that no other assertion in this module
        can see, because both spellings already appear in the resolve step
        above. From bluelinky PR #53, where it survived 89 green tests.

        Asserted as a LINE SEQUENCE, matching
        ``test_the_cross_repo_branches_are_not_invertible_silently``.
        """
        body = [ln.strip() for ln in self._fallback_step(gate).splitlines() if ln.strip()]
        anchor = 'if [ "$EVENT_NAME" = "issue_comment" ]; then'
        assert anchor in body, "the fallback does not branch on the event name at all"
        i = body.index(anchor)
        assert body[i + 1] == 'number="$ISSUE_NUMBER"', (
            f"an issue_comment event carries no pull_request object; then-arm is {body[i + 1]!r}"
        )
        assert body[i + 2] == "else", f"expected an else at this position, got {body[i + 2]!r}"
        assert body[i + 3] == 'number="$PR_NUMBER"', (
            f"a pull_request event's number is on the PR object; else-arm is {body[i + 3]!r}"
        )
        # The SHA comes free on the PR events and only there, so it must be set
        # in the else-arm and nowhere else -- the lookup below is the
        # issue_comment path's only route to one.
        assert body[i + 4] == 'sha="$PR_HEAD_SHA"', (
            f"the PR events' head SHA must be taken from the event; got {body[i + 4]!r}"
        )

    def test_the_fallback_recovers_a_sha_rather_than_trusting_one_to_exist(self, gate):
        # The `issue_comment` arm above sets no `sha`, so without this lookup
        # the fallback has nowhere to post on exactly the event that needed the
        # resolve step most. `${sha:-}` because `set -u` is on and the variable
        # is genuinely unset on that path.
        tail = self._fallback_step(gate)
        assert 'if [ -z "${sha:-}" ]; then' in tail
        assert 'sha="$(gh api "repos/${REPO}/pulls/${number}" --jq .head.sha || true)"' in tail

    def test_the_fallback_slice_is_the_step_and_not_the_whole_file(self, gate):
        # Control for the slice: one that ran from byte 0 would satisfy the
        # presence assertions above off the main publish step and make the
        # absence assertion fail for the wrong reason.
        tail = self._fallback_step(gate)
        assert "Resolve PR number and head SHA" not in tail
        assert len(tail) < len(gate) / 2


class TestTheDetectorsCanFail:
    """Drive each detector to a failure, so no guard above is a tautology."""

    def test_run_blocks_finds_a_block_scalar_body(self):
        blocks = run_blocks(
            "jobs:\n  a:\n    steps:\n      - name: x\n        run: |\n"
            "          set -euo pipefail\n          echo hi\n      - uses: actions/checkout@v4\n"
        )
        assert len(blocks) == 1
        assert "echo hi" in blocks[0]
        assert "actions/checkout" not in blocks[0], "the extractor ran past the end of the block"

    def test_run_blocks_finds_an_inline_body(self):
        assert run_blocks("      - run: jq --version\n") == ["jq --version"]

    def test_run_blocks_excludes_env_values_above_the_body(self):
        blocks = run_blocks(
            "      - name: x\n        env:\n          REPO: "
            + EXPR_OPEN
            + " github.repository }}\n"
            '        run: |\n          echo "$REPO"\n'
        )
        assert len(blocks) == 1
        assert EXPR_OPEN not in blocks[0], (
            "the extractor swept in an env: line, which would make the expression ban "
            "fire on the correct fix"
        )

    def test_the_expression_ban_catches_an_expression_in_a_run_body(self):
        blocks = run_blocks(
            "      - run: |\n          echo " + EXPR_OPEN + " github.event.pull_request.title }}\n"
        )
        assert any(EXPR_OPEN in b for b in blocks), "the ban cannot see a real violation"

    def test_the_expression_ban_catches_one_inside_a_comment(self):
        # The bluelinky case exactly: a commented-out expression is still
        # parsed by GitHub, and an invalid one invalidates the file.
        blocks = run_blocks(
            "      - run: |\n          # see " + EXPR_OPEN + " }}\n          true\n"
        )
        assert any(EXPR_OPEN in b for b in blocks)

    def test_the_strict_shell_guard_catches_a_non_strict_block(self):
        blocks = run_blocks("      - run: |\n          echo hi\n")
        assert not any("set -euo pipefail" in b for b in blocks)

    def test_the_shell_syntax_check_catches_broken_shell(self):
        # Control for `test_every_run_block_is_valid_shell`: the bluelinky
        # shape, an apostrophe closing a single-quoted program.
        broken = "jq -r '.state # don't trust it\n'"
        proc = subprocess.run(
            ["bash", "-n"], input=broken, capture_output=True, text=True, check=False
        )
        assert proc.returncode != 0, "bash -n accepted an unterminated quote"

    def test_active_lines_strips_a_whole_line_comment(self):
        assert "head.repo.fork" not in active_lines("        # never use head.repo.fork\n")

    def test_active_lines_strips_an_end_of_line_comment(self):
        assert "v7.0.1" not in active_lines("      - uses: actions/checkout@abc # v7.0.1\n")

    def test_active_lines_keeps_the_setting_it_is_asked_about(self):
        # The half that makes the two above meaningful: a stripper that removed
        # everything would satisfy every absence assertion in this module.
        kept = active_lines('          head_repo="$HEAD_REPO"  # a comment\n')
        assert "head_repo=" in kept

    def test_active_lines_can_still_see_a_real_violation(self):
        assert "head.repo.fork" in active_lines(
            "          HEAD_FORK: " + EXPR_OPEN + " github.event.pull_request.head.repo.fork }}\n"
        )

    def test_active_lines_alters_nothing_but_comments(self):
        """Bounds what `active_lines` is allowed to damage, on the real files.

        The helper is not comment-aware, so this is the claim the guards using
        it actually rest on: across all three workflows, every line it changes
        is a comment or a ` # vN.N.N` version suffix.
        """
        for path in (GATE_WORKFLOW, REVIEWER_WORKFLOW, CI_WORKFLOW):
            original = path.read_text().splitlines()
            stripped = active_lines(path.read_text()).splitlines()
            assert len(original) == len(stripped)
            changed = [(a, b) for a, b in zip(original, stripped, strict=True) if a != b]
            assert changed, f"{path.name}: nothing changed, so the helper is a no-op here"
            for before, after in changed:
                assert before.lstrip().startswith("#") or "uses:" in before, (
                    f"{path.name}: active_lines altered a non-comment line:\n  {before!r}\n"
                    f"  -> {after!r}"
                )

    def test_the_title_prefix_detector_finds_more_than_one_when_there_is_more_than_one(self):
        synthetic = (
            "!startsWith(github.event.pull_request.title, 'chore') "
            "&& !startsWith(github.event.pull_request.title, 'docs')"
        )
        found = set(
            re.findall(r"startsWith\(github\.event\.pull_request\.title, '([^']+)'\)", synthetic)
        )
        assert found == {"chore", "docs"}, "the detector cannot see a second prefix"

    CAVEAT = TestTheTriggersCoverEveryWayAVerdictCanChange.ISSUE_COMMENT_CAVEAT

    def test_the_trigger_note_slice_reads_the_block_it_is_asked_for(self):
        # The positive half: a slice that always came back empty would satisfy
        # every absence assertion about a trigger block.
        note = trigger_note(
            "on:\n  issue_comment:\n    # a note spanning\n    # two comment lines\n"
            "    types: [created, edited]\n",
            "issue_comment",
        )
        assert note == "a note spanning two comment lines"

    def test_the_trigger_note_slice_cannot_be_satisfied_from_another_block(self):
        """The control for the caveat guard, and the defect it was fixed from.

        The caveat text is planted in the `pull_request` block instead. The
        file-wide version of that assertion passed on exactly this shape --
        which is how deleting the whole caveat left the test green on
        parsons-remote PR #26.
        """
        synthetic = (
            "on:\n"
            "  pull_request:\n"
            f"    # {self.CAVEAT}\n"
            "    types: [opened, synchronize, reopened]\n"
            "  issue_comment:\n"
            "    # a note of its own, so the assertion below is not satisfied by\n"
            "    # an empty slice\n"
            "    types: [created, edited]\n"
        )
        assert self.CAVEAT in synthetic, "the planted caveat is not in the synthetic file at all"
        note = trigger_note(synthetic, "issue_comment")
        # The reachability half. Without it this test passes on an
        # always-empty slice, which is the degenerate-input shape this
        # module's own preamble warns about.
        assert "a note of its own" in note, "the slice found nothing, so it proves nothing"
        assert self.CAVEAT not in note, "the slice reached outside the block it was asked for"

    def test_the_trigger_note_slice_refuses_a_block_with_no_types_line(self):
        """The run-on direction, which the first version could not see.

        With a `(?:.*\\n)*?` capture, a trigger whose `types:` line was
        deleted did not raise -- the group walked past its own block and bound
        to the NEXT trigger's `types:`, returning a slice about the wrong
        block. So the helper's own assertion was unreachable for every trigger
        but the last one in the file.
        """
        synthetic = (
            "on:\n"
            "  issue_comment:\n"
            "    # the caveat still here\n"
            "  pull_request_review:\n"
            "    types: [submitted]\n"
        )
        with pytest.raises(AssertionError, match="no `issue_comment:` trigger"):
            trigger_note(synthetic, "issue_comment")

    def test_the_trigger_note_slice_sees_the_caveat_go_missing(self):
        # The deletion the real guard has to catch: the block is still there,
        # the trigger still declares its types, and the commentary is gone.
        assert self.CAVEAT not in trigger_note(
            "on:\n  issue_comment:\n    types: [created, edited]\n", "issue_comment"
        )

    def test_step_body_stops_at_the_next_step(self):
        synthetic = (
            "    steps:\n"
            "      - name: first\n"
            '        run: echo context="a"\n'
            "      - name: second\n"
            '        run: echo context="b"\n'
        )
        first = step_body(synthetic, "first")
        assert 'context="a"' in first
        assert 'context="b"' not in first, "the slice ran into the following step"
        assert 'context="b"' in step_body(synthetic, "second")

    def test_step_body_is_not_fooled_by_a_name_at_a_deeper_indent(self):
        # Control on the indent arithmetic: a `- name:` nested deeper (a matrix
        # entry, a `with:` list) must not end the slice early.
        synthetic = (
            "      - name: outer\n"
            "        with:\n"
            "          items:\n"
            "            - name: inner\n"
            '        run: echo context="a"\n'
            "      - name: next\n"
        )
        body = step_body(synthetic, "outer")
        assert 'context="a"' in body, "a deeper `- name:` truncated the slice"
        assert "- name: next" not in body

    def test_the_remedy_spelling_detector_finds_both_spellings(self):
        synthetic = (
            "contains(github.event.comment.body, '/review') "
            "|| contains(github.event.comment.body, '@claude review')"
        )
        found = set(re.findall(r"contains\(github\.event\.comment\.body, '([^']+)'\)", synthetic))
        assert found == {"/review", "@claude review"}

    def test_the_checkout_pin_detector_sees_a_disagreement(self):
        # Control for `test_all_three_workflows_pin_the_same_checkout`: with
        # one file bumped and the others not, the union has two members.
        pattern = r"uses: (actions/checkout@[0-9a-f]{40})"
        a = set(re.findall(pattern, "      - uses: actions/checkout@" + "a" * 40 + "\n"))
        b = set(re.findall(pattern, "      - uses: actions/checkout@" + "b" * 40 + "\n"))
        assert a and b, "the matcher cannot see a SHA-pinned checkout at all"
        assert len(a | b) == 2, "the detector cannot distinguish two different pins"


class TestTheInputBuilderIsExecutedAndNotOnlyRead:
    """The gate's `jq -n --slurpfile ...` program, run rather than grepped.

    THIS WAS THE WIDEST HOLE IN THE PORT, and it is inherited -- the source
    copy has no guard here either. `test_review_gate_decision.py` feeds
    `review-gate.jq` a HAND-WRITTEN document; the real document is assembled by
    the program in `require-review.yml`'s publish step, and nothing ran it.
    Measured in review: five one-token mutations to that program survived all
    136 tests, and each is a production-visible failure --

      ``bot_logins: ["nobody[bot]"]``       every reviewed PR reads red forever
      ``reviewer_logins: ["someone-else"]`` the gate becomes unsatisfiable, and
                                            on this repo that arm is currently
                                            the ONLY one that can go green
      ``author: $pr[0].head.user.login``    `exempt_reason` stops matching, so
                                            every Dependabot PR reads red
      ``head_committed_at: ...author.date`` on a rebased head the author date is
                                            older, so a review that predates the
                                            push reads FRESH -- a false green
      ``number: $pr[0].id``                 the refusal names a PR that does not
                                            exist

    `test_the_bot_login_the_gate_trusts_is_the_one_the_reviewer_posts_as` was
    the guard nominally covering the first row. It asserts `"claude[bot]" in
    gate` against RAW text, and that literal appears seven times in
    `review-gate.jq` and five in `require-review.yml`, nearly all in prose. It
    cannot fail. That is exactly the defect class this port was told to expect.

    So: extract the program, run it against fixtures, and assert the MAPPING --
    with two differing rows, because a field asserted to hold *a* legal value
    survives both a hardcoded constant and a read of the wrong row.
    """

    #: Where each output key must come from. The whole point of the table: a
    #: guard that only checked the keys exist would survive every row above.
    EXPECTED_SOURCES = {
        "number": ("pr", "number"),
        "author": ("pr", "user.login"),
        "title": ("pr", "title"),
        "head_committed_at": ("head", "commit.committer.date"),
    }

    @staticmethod
    def _program(gate: str) -> str:
        """The `jq -n ... '<program>'` text from the publish step.

        Read off `active_lines` and sliced between the single quotes that
        delimit the jq program, so the `#`-comment INSIDE the program (the one
        explaining `reviewer_logins`) is gone before the program is handed to
        jq -- which is what `active_lines` is for, and is also how a
        commented-out line in it would show up as missing rather than as
        present.
        """
        active = active_lines(gate)
        anchor = "jq -n \\"
        assert anchor in active, "no `jq -n` input builder in the workflow"
        tail = active[active.index(anchor) :]
        start = tail.index("'{")
        end = tail.index("}'", start)
        return tail[start + 1 : end + 1]

    def test_the_program_slice_is_a_program_and_not_the_step(self, gate):
        # Control for the slice: one that over-reached would hand jq the
        # surrounding shell and every assertion below would fail for the wrong
        # reason, or -- worse, with a laxer slice -- succeed on a fragment.
        program = self._program(gate)
        assert program.startswith("{") and program.rstrip().endswith("}")
        assert "gh api" not in program, "the slice swept in the shell around the program"
        assert "slurpfile" not in program
        for key in self.EXPECTED_SOURCES:
            assert f"{key}:" in program, f"{key} missing from the extracted program"

    @staticmethod
    def _row(n: int) -> dict:
        """Two rows that differ in EVERY field the builder reads.

        `n` is 1 or 2. Nothing here is shared between the two, so a builder
        that hardcodes a value, or reads the right field off the wrong
        document, produces row 1's answer for row 2's input and is caught.
        """
        return {
            "pr": {
                "number": 100 + n,
                "user": {"login": f"author-{n}"},
                "title": f"feat: change {n}",
                # A decoy at `head.user.login`: the mutation that survived
                # review read the HEAD repo's owner instead of the PR author,
                # and with no such field present that mutant yields null and
                # could look like a different bug.
                "head": {"user": {"login": f"head-owner-{n}"}},
            },
            "head": {
                "commit": {
                    "committer": {"date": f"2026-10-2{n}T04:00:00Z"},
                    # The other surviving mutant's target. An author date that
                    # is OLDER than the committer date is the real shape after
                    # a rebase, which is the case where reading the wrong one
                    # is a false green rather than a wash.
                    "author": {"date": f"2026-10-0{n}T04:00:00Z"},
                }
            },
            "comments": [{"user": {"login": f"commenter-{n}"}, "body": f"c{n}"}],
            "reviews": [{"user": {"login": f"reviewer-{n}"}, "state": "APPROVED"}],
        }

    def _build(self, gate: str, row: dict, tmp_path: Path) -> dict:
        """Run the real program over `row`, exactly as the workflow does."""
        import json

        args = []
        for name in ("pr", "head", "comments", "reviews"):
            path = tmp_path / f"{name}.json"
            path.write_text(json.dumps(row[name]))
            args += ["--slurpfile", name, str(path)]
        proc = subprocess.run(
            ["jq", "-n", *args, self._program(gate)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"the input builder failed:\n{proc.stderr}"
        return json.loads(proc.stdout)

    def test_the_builder_produces_exactly_the_keys_the_gate_reads(self, gate, tmp_path):
        built = self._build(gate, self._row(1), tmp_path)
        assert set(built) == {
            "number",
            "author",
            "title",
            "head_committed_at",
            "bot_logins",
            "reviewer_logins",
            "comments",
            "reviews",
        }

    @pytest.mark.parametrize("key", sorted(EXPECTED_SOURCES))
    def test_each_field_is_read_from_the_document_and_path_it_names(self, gate, tmp_path, key):
        """Two rows, so membership cannot stand in for correspondence."""
        doc, path = self.EXPECTED_SOURCES[key]
        for n in (1, 2):
            row = self._row(n)
            want = row[doc]
            for part in path.split("."):
                want = want[part]
            got = self._build(gate, row, tmp_path)[key]
            assert got == want, (
                f"{key} came back {got!r} but {doc}.{path} is {want!r} on row {n} -- "
                "the builder is reading a different field, a different document, "
                "or a hardcoded value"
            )

    def test_the_head_timestamp_is_the_COMMITTER_date_and_not_the_author_date(self, gate, tmp_path):
        # Called out separately because it is the only one of the five whose
        # wrong answer is a FALSE GREEN rather than a false red: after a rebase
        # or an amend the author date is older than the push, so a stale review
        # would compare fresh against it.
        row = self._row(1)
        built = self._build(gate, row, tmp_path)
        assert built["head_committed_at"] == row["head"]["commit"]["committer"]["date"]
        assert built["head_committed_at"] != row["head"]["commit"]["author"]["date"]

    def test_the_author_is_the_pr_author_and_not_the_head_repo_owner(self, gate, tmp_path):
        # The decoy row exists so this can distinguish "reads the right field"
        # from "reads a field that happens to be absent".
        row = self._row(1)
        built = self._build(gate, row, tmp_path)
        assert built["author"] == row["pr"]["user"]["login"]
        assert built["author"] != row["pr"]["head"]["user"]["login"]

    def test_the_two_allow_lists_are_the_ones_the_gate_and_the_reviewer_agree_on(
        self, gate, tmp_path
    ):
        """The literals, read out of the built document rather than grepped.

        `bot_logins` must be the login the reviewer posts as, and
        `reviewer_logins` the human allow-list -- and a `"claude[bot]" in gate`
        text assertion cannot see either go wrong, because that literal is all
        over the prose in both files.
        """
        built = self._build(gate, self._row(1), tmp_path)
        assert built["bot_logins"] == ["claude[bot]"], (
            "the gate would trust a login the reviewer never posts as, so every "
            "reviewed PR would read red"
        )
        assert built["reviewer_logins"] == ["tclancy"], (
            "with no Claude App installed here this is the only arm that can go "
            "green, so a wrong login makes the gate unsatisfiable by anyone"
        )
        # And the bot login must be the one `claude-code-review.yml` actually
        # produces. Asserted against the built value, not the file's text.
        assert "claude[bot]" in built["bot_logins"]

    def test_the_collections_pass_through_unflattened(self, gate, tmp_path):
        # `--slurpfile` makes each `$name` an ARRAY of the file's documents,
        # so every reference needs `[0]`. Dropping it on `comments` yields a
        # list-of-lists, which `review-gate.jq`'s `.comments[]?` then iterates
        # into arrays and silently matches nothing.
        row = self._row(2)
        built = self._build(gate, row, tmp_path)
        assert built["comments"] == row["comments"]
        assert built["reviews"] == row["reviews"]

    def test_the_built_document_actually_satisfies_the_gate_end_to_end(self, gate, tmp_path):
        """The two halves joined: builder output straight into the real gate.

        Neither suite covered this seam. A builder that is individually correct
        and a gate that is individually correct can still disagree about a key
        name, and the symptom would be a permanently red status with no
        explanation in either file.
        """
        from tests.test_review_gate_decision import REAL_FINISHED_COMMENT, run_gate

        row = self._row(1)
        row["comments"] = [
            {
                "user": {"login": "claude[bot]"},
                "body": REAL_FINISHED_COMMENT,
                "updated_at": "2026-10-21T05:00:00Z",
                "created_at": "2026-10-21T05:00:00Z",
                "author_association": "NONE",
            }
        ]
        verdict = run_gate(self._build(gate, row, tmp_path))
        assert verdict["state"] == "success", verdict
        assert "claude[bot]" in verdict["description"]

        # And the refusal path, so the success above is not a tautology.
        row["comments"] = []
        row["reviews"] = []
        refusal = run_gate(self._build(gate, row, tmp_path))
        assert refusal["state"] == "failure"
        # The refusal names the number the builder supplied, which is the
        # fifth surviving mutant (`$pr[0].id` instead of `.number`).
        assert f"#{row['pr']['number']}" in refusal["description"]


class TestEveryPublishedDescriptionFitsTheStatusField:
    """GitHub 422s a status description over 140 characters.

    `review-gate.jq` has `truncate140` and two tests for it. The THREE
    descriptions published by the shell -- the unprovisioned arm, the ERR trap
    and the `failure()` fallback -- have no bound and had no test. The
    unprovisioned one is at 136 of 140 today, so the next edit to that sentence
    crosses the limit; and inside the trap a 422 is swallowed by `|| true`,
    which means NO status at all -- the stale-verdict outcome the trap exists
    to prevent.
    """

    LIMIT = 140

    #: Two spellings, because the shell publishes through two surfaces and a
    #: detector that knew only one found 1 of 3 on its first run -- which
    #: would have passed the loop below while saying nothing about the other
    #: two. `publish <state> "..."` is the helper (the unprovisioned arm and
    #: the ERR trap pass the text as its second argument); `-f description=`
    #: is the `failure()` fallback, which POSTs directly.
    _VIA_HELPER = re.compile(r'publish\s+(?:pending|error)\s*\\?\s*"([^"]+)"')
    _VIA_FLAG = re.compile(r'-f description="([^"$][^"]*)"')

    @classmethod
    def _descriptions(cls, gate: str) -> list[str]:
        """Every literal description the shell publishes.

        Read off `active_lines` so a commented-out example cannot be counted,
        and the `"$2"` / `"$description"` indirections are excluded -- those
        carry the jq verdict, which `truncate140` already bounds and tests.
        """
        active = active_lines(gate)
        return cls._VIA_HELPER.findall(active) + cls._VIA_FLAG.findall(active)

    def test_each_one_fits(self, gate):
        found = self._descriptions(gate)
        # Reachability: an empty list satisfies the loop, and an empty list is
        # what a rename of the flag produces.
        assert len(found) == 3, f"expected three shell-published descriptions, found {found}"
        for text in found:
            assert len(text) <= self.LIMIT, (
                f"{len(text)} chars, over GitHub's {self.LIMIT}-char limit, so the POST "
                f"422s -- and in the ERR trap that means no status at all:\n  {text}"
            )

    def test_the_detector_can_see_an_overlong_one(self):
        # Control: without this the test above passes on a detector that found
        # nothing in a file full of overlong strings.
        over = "x" * 200
        assert self._VIA_FLAG.findall(f'-f description="{over}" \\') == [over]
        assert self._VIA_HELPER.findall(f'publish pending \\\n  "{over}"') == [over], (
            "the helper-call detector cannot see a multi-line publish, which is "
            "the form the unprovisioned arm uses"
        )

    def test_the_detector_skips_the_jq_verdict_indirections(self, gate):
        # `-f description="$2"` and `description="$description"` must NOT be
        # measured as literals: their content is the jq verdict, bounded by
        # `truncate140` and tested there. Counting them would report a 2-char
        # pass and dilute the assertion above.
        assert '-f description="$2"' in gate
        assert not any(d.startswith("$") for d in self._descriptions(gate))
