"""Behavioural guards on `.github/review-gate.jq`.

The gate's whole job is to answer one question -- "has a review happened for
*this* head?" -- and every way it can be wrong is silent. A gate that says
`success` when nothing reviewed anything is indistinguishable, on the PR page,
from a gate that is working.

So these are *behavioural* tests: each one feeds the real jq program a document
shaped like what `require-review.yml` actually builds and asserts the verdict.
They are not text guards. The workflow's text is guarded separately in
`tests/test_require_review_workflow.py`, which is also where the control lives
that keeps `jq`'s absence from quietly disabling this whole module (see
``TestTheDependencyOnJqCannotGoQuiet`` there).

The fixture set is the part with history. bluelinky's first cut of this logic
shipped with a rule -- "a bot comment counts unless it has an outstanding
``- [ ]`` item" -- that passed *both* shapes `claude-code-action` emits when it
has reviewed nothing, because neither carries a checklist at all. Three
all-killed mutation rounds did not find it: every fixture in that family was
hand-written and every one carried an unticked box, so the suite only ever
exercised the shapes the rule already handled. **The fixtures were the defect.**
Hence `NON_REVIEW_SHAPES` below, which names each no-checklist shape
explicitly, and `test_every_named_fixture_is_still_present`, which fails if a
future edit empties one of these families out from under the loop that reads it.

ONE THING ABOUT THIS COPY IS WEAKER THAN ITS SOURCE, and it is worth knowing
before reading a surprising result as a logic bug. In parsons-remote the
accepting fixture below is real observed data; **this repo has never had a
review of any kind.** `claude[bot]` has posted zero comments here (2026-11-01),
because the Claude GitHub App is not installed. So every accepting fixture here
is inherited from a sibling repo's measurements, and the first real review in
this repo is the first live test of any of them.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / ".github" / "review-gate.jq"

HEAD_TS = "2026-10-23T04:00:00Z"
BEFORE_HEAD = "2026-10-23T03:00:00Z"
AFTER_HEAD = "2026-10-23T05:00:00Z"

#: The one login on `reviewer_logins`, and `review-gate.jq`'s default for it.
#: Named once so the fixture default and the association derived from it cannot
#: drift apart -- the whole hazard `association_for()` below exists to close.
ALLOW_LISTED_REVIEWER = "tclancy"

#: The exact body prefix of a real `claude-code-action` review, taken verbatim
#: from one of the three parsons-remote PR #23 reviews on 2026-10-23.
#:
#: **Borrowed live data, not this repo's.** It is pinned rather than invented
#: because an invented shape is how bluelinky's original rule passed its own
#: suite while admitting two non-reviews; but this repo has produced no review
#: comment ever, so the claim this fixture supports is "the gate accepts the
#: shape the action really emits", measured elsewhere in the same fleet on the
#: same action version. The URL is parsons-remote's on purpose: faking a
#: lintarr one would make the fixture look measured here.
REAL_FINISHED_COMMENT = (
    "**Claude finished @metaframework-dispatch-bot[bot]'s task in 24s** "
    "—— [View job](https://github.com/tclancy/parsons-remote/actions/runs/39897838521)\n"
    "\n---\n\n### Review of PR #23\n\n"
    "This PR touches no workflow file, so this is a real review."
)

#: Bodies that must NEVER satisfy the gate. Each is a shape the action emits
#: when it has reviewed nothing. The first two are the ones that defeated the
#: original checklist-only rule: they have no checklist to be outstanding.
NON_REVIEW_SHAPES = {
    "in_flight": (
        'Claude Code is working… <img src="https://example.invalid/spin.gif" />\n'
        "\nI'll analyze this and get back to you.\n"
        "\n[View job run](https://example.invalid/run)"
    ),
    "errored_no_checklist": (
        "**Claude encountered an error** —— [View job](https://example.invalid/run)"
    ),
    "errored_with_elapsed_no_checklist": (
        "**Claude encountered an error after 2m 10s** —— [View job](https://example.invalid/run)"
    ),
    "errored_partial_checklist": (
        "**Claude encountered an error**\n\n- [x] Read the diff\n- [ ] Post findings"
    ),
    # No preceding newline before the marker. This is what makes the `(^|\n)`
    # anchor in `has_outstanding_item` a contested predicate rather than a
    # defensive one a mutation round is blind to: with the anchor reduced to
    # `\n` this body sails through.
    # EXACTLY ONE item, at byte offset 0, and both halves of that are load
    # bearing. The first draft of this fixture had two items -- and the second
    # one *was* preceded by a newline, so reducing the anchor to `\n` still
    # matched it and the mutation round reported SURVIVED. A fixture meant to
    # contest an anchor has to be the one shape the anchor is the only thing
    # catching.
    "body_opening_with_an_outstanding_item": "- [ ] Read the diff",
    # Contests the `^` anchor on `says_finished`: the body is an errored-run
    # status comment that happens to quote the finished line in prose. Without
    # the anchor this reads as a completed review.
    "errored_but_quoting_the_finished_line": (
        "**Claude encountered an error** —— [View job](https://example.invalid/run)\n"
        "\nThe previous attempt said **Claude finished in 2s** and it had not."
    ),
    "in_flight_partial_checklist": ("Claude Code is working…\n\n- [ ] Read the diff"),
}

#: Bodies that must satisfy the gate.
REVIEW_SHAPES = {
    "real_finished": REAL_FINISHED_COMMENT,
    "finished_bolded": "**Claude finished in 3m 48s**\n\n### Review\n\nNothing blocking.",
    "errored_but_completed": (
        "**Claude encountered an error**\n\n- [x] Read the diff\n- [x] Post findings\n\n"
        "Found two things before running out of budget."
    ),
    "plain_summary": ("### Review\n\nTwo findings, neither blocking. Posted inline."),
    # The marker appears mid-sentence in prose. A review that *discusses*
    # checklists must not be thrown away for quoting the syntax.
    "prose_quoting_the_marker": (
        "### Review\n\nThe progress comment left a `- [ ] ` item unticked, which is worth "
        "knowing but is not a finding."
    ),
}


def run_gate(doc: dict) -> dict:
    """Evaluate the real jq program against ``doc`` and return its verdict."""
    proc = subprocess.run(
        ["jq", "-f", str(GATE)],
        input=json.dumps(doc),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def pr(**overrides) -> dict:
    """A gate input with no reviews of any kind, plus ``overrides``."""
    doc = {
        "number": 23,
        "author": "metaframework-dispatch-bot[bot]",
        "title": "feat: a change",
        "head_committed_at": HEAD_TS,
        "bot_logins": ["claude[bot]"],
        "comments": [],
        "reviews": [],
    }
    doc.update(overrides)
    return doc


def association_for(login: str) -> str:
    """The `author_association` GitHub really sends for ``login`` on this repo.

    `OWNER` for Tom, `NONE` for everyone else -- a drive-by reviewer, a fleet
    bot, an app. Derived rather than defaulted to one literal, because the
    *adversarial* value and the *production-shaped* value are not the same
    value, and hardcoding either one gets the other wrong.

    That mattered immediately in parsons-remote PR #26: a first cut of this
    defaulted every fixture to `"NONE"`, which made six Tom-shaped success
    cases assert a payload GitHub cannot send -- an owner with `NONE`. A
    plausible NARROWING of the gate (`is_bot($reviewers) and
    (.author_association | IN("OWNER","MEMBER","COLLABORATOR"))`) would then
    have turned those six red while production worked fine, which is the mirror
    image of the defect the field was added to catch.
    """
    return "OWNER" if login == ALLOW_LISTED_REVIEWER else "NONE"


def comment(
    body: str,
    *,
    login: str = "claude[bot]",
    updated_at: str = AFTER_HEAD,
    created_at: str = BEFORE_HEAD,
    author_association: str | None = None,
) -> dict:
    """A GitHub issue-comment object.

    ``created_at`` defaults to a DIFFERENT value from ``updated_at``, and
    deliberately to a stale one. The gate must score the comment on when it was
    last edited, because the action posts its progress comment unticked and
    edits it into the finished one in place. A mutation round on 2026-11-01
    showed `.updated_at` -> `.created_at` being killed only because no fixture
    carried a ``created_at`` at all, so `ts` became null and
    ``is_fresh(null)`` returned true -- a kill for the wrong reason, which says
    nothing about the field.
    """
    return {
        "user": {"login": login},
        "body": body,
        "updated_at": updated_at,
        # Present for the same reason it is on `review()` below: a real issue
        # comment carries it, and a fixture that omits a field cannot contest a
        # rule that reads it. The bot-comment arm filters on `bot_logins`
        # today; this keeps a widening that reaches for the association
        # falsifiable here too -- verified against `select((.user.login |
        # is_bot($bots)) or (.author_association != null))`, which turns
        # `totally-unrelated` green with the field present and survives with it
        # absent.
        "author_association": author_association or association_for(login),
        "created_at": created_at,
    }


def review(
    state: str,
    *,
    login: str = ALLOW_LISTED_REVIEWER,
    submitted_at: str = AFTER_HEAD,
    author_association: str | None = None,
) -> dict:
    """A GitHub review object, carrying every field the real payload carries.

    ``author_association`` is derived from ``login`` by ``association_for()``
    unless overridden, because **a fixture that omits a field cannot contest a
    rule that reads it.**

    bluelinky's copy of this suite paid for that (bluelinky PR #53, still open
    as of 2026-11-01, which is why this port's source is parsons-remote). Its
    `review()` omitted `author_association`, so adding
    ``or (.author_association != null)`` to the formal-review arm -- the
    widening someone would plausibly write to admit any
    OWNER/MEMBER/COLLABORATOR -- **passed all 89 tests**, while a
    production-shaped payload went straight back to the false green the login
    allow-list had just closed. It was found by *review*, not by either
    mutation round -- and the reason is worth stating precisely, because the
    obvious reading ("mutation is blind to a fixture gap") would talk the next
    editor out of a round that works: neither round *included* this mutant.
    One that does kills it immediately. The lesson is about which mutants you
    choose to write, not about mutation.

    Override ``author_association`` to drive a value the login would not
    really carry -- that is what
    ``test_no_author_association_substitutes_for_the_login_allow_list`` does.
    """
    return {
        "user": {"login": login},
        "state": state,
        "submitted_at": submitted_at,
        "author_association": author_association or association_for(login),
    }


class TestTheBaselineRefuses:
    """Without this, every ``success`` below could be a tautology."""

    def test_jq_is_available(self):
        # Deliberately NOT a skipif: this module's value is that it runs. If jq
        # is missing the honest outcome is a red test naming the missing tool,
        # not a green run that proved nothing. `TestTheDependencyOnJqCannotGoQuiet`
        # in the workflow-guard module drives this very assertion to a failure,
        # so it is a control that has been shown to fire rather than one that
        # has only ever passed.
        assert shutil.which("jq") is not None, (
            "jq is required to evaluate .github/review-gate.jq. "
            "Install it (brew install jq); ubuntu-latest ships it."
        )

    def test_a_pr_with_nothing_is_refused(self):
        verdict = run_gate(pr())
        assert verdict["state"] == "failure"
        assert "No code review for this head" in verdict["description"]

    def test_the_refusal_names_the_remedy_and_the_pr(self):
        verdict = run_gate(pr(number=99))
        assert "/review" in verdict["description"]
        assert "#99" in verdict["description"]


class TestAReviewThatNeverHappenedIsNotAReview:
    """The family that defeated the first version of this rule."""

    @pytest.mark.parametrize("shape", sorted(NON_REVIEW_SHAPES))
    def test_non_review_shapes_are_refused(self, shape):
        verdict = run_gate(pr(comments=[comment(NON_REVIEW_SHAPES[shape])]))
        assert verdict["state"] == "failure", (
            f"{shape!r} satisfied the gate; it is a shape the action emits when "
            "it has reviewed nothing."
        )

    @pytest.mark.parametrize("shape", sorted(REVIEW_SHAPES))
    def test_review_shapes_are_accepted(self, shape):
        verdict = run_gate(pr(comments=[comment(REVIEW_SHAPES[shape])]))
        assert verdict["state"] == "success", f"{shape!r} was refused but is a real review"

    def test_an_empty_or_null_body_is_not_a_review(self):
        assert run_gate(pr(comments=[comment("")]))["state"] == "failure"
        assert run_gate(pr(comments=[comment(None)]))["state"] == "failure"

    def test_every_named_fixture_is_still_present(self):
        # A parametrize list scoped to an empty collection passes every time it
        # runs, so the two loops above would go green having asserted nothing.
        # `>= 5` was the first version of this and it is a FLOOR, not a
        # completeness control: over seven shapes it lets two be deleted
        # silently. Each key below is a distinct failure mode somebody paid for,
        # so they are named.
        assert set(NON_REVIEW_SHAPES) == {
            "in_flight",
            "errored_no_checklist",
            "errored_with_elapsed_no_checklist",
            "errored_partial_checklist",
            "errored_but_quoting_the_finished_line",
            "body_opening_with_an_outstanding_item",
            "in_flight_partial_checklist",
        }
        assert set(REVIEW_SHAPES) == {
            "real_finished",
            "finished_bolded",
            "errored_but_completed",
            "plain_summary",
            "prose_quoting_the_marker",
        }

    def test_both_families_are_disjoint(self):
        assert not set(NON_REVIEW_SHAPES.values()) & set(REVIEW_SHAPES.values())


class TestOnlyTheAllowListedBotCounts:
    def test_a_login_that_is_a_prefix_of_the_allow_listed_one_is_refused(self):
        # `["claude[bot]"] | inside/contains("claude")` is TRUE -- substring
        # matching on array elements. This case is why the gate uses `index`
        # with a scalar bound to a variable first.
        verdict = run_gate(pr(comments=[comment(REAL_FINISHED_COMMENT, login="claude")]))
        assert verdict["state"] == "failure"

    def test_an_unrelated_login_is_refused(self):
        verdict = run_gate(pr(comments=[comment(REAL_FINISHED_COMMENT, login="totally-unrelated")]))
        assert verdict["state"] == "failure"

    def test_a_human_review_comment_does_not_satisfy_the_bot_arm(self):
        verdict = run_gate(pr(comments=[comment("### Review\n\nLooks fine.", login="tclancy")]))
        assert verdict["state"] == "failure"

    def test_the_bot_list_is_configurable(self):
        verdict = run_gate(
            pr(
                bot_logins=["some-other-reviewer[bot]"],
                comments=[comment(REAL_FINISHED_COMMENT, login="some-other-reviewer[bot]")],
            )
        )
        assert verdict["state"] == "success"


class TestOnlyAnAllowListedHumanSatisfiesTheFormalArm:
    """The fix that made parsons-remote #26 the copy source; bluelinky lacks it.

    Reproduced against the real program before it was fixed: a
    `CHANGES_REQUESTED` from `random-passerby` returned `success`. This repo is
    public (`gh repo view tclancy/lintarr --json visibility` -> `PUBLIC`,
    2026-11-01), so any authenticated GitHub account can submit a review on an
    open PR -- and GitHub lets one fleet bot approve another's PR, which is the
    same one-command bypass the gate refuses `chore:` titles for.
    """

    def test_an_arbitrary_github_account_cannot_turn_the_gate_green(self):
        verdict = run_gate(pr(reviews=[review("CHANGES_REQUESTED", login="random-passerby")]))
        assert verdict["state"] == "failure"

    def test_a_fleet_bot_cannot_approve_another_bots_pr_into_a_pass(self):
        verdict = run_gate(
            pr(
                author="dispatch-bot[bot]",
                reviews=[review("APPROVED", login="metaframework-dispatch-bot[bot]")],
            )
        )
        assert verdict["state"] == "failure"

    def test_tom_can(self):
        # The half that makes the two above meaningful: an allow-list that
        # admitted nobody would satisfy them while breaking the escape hatch.
        # `association_for()` gives this fixture the `OWNER` GitHub really
        # sends for Tom on his own repo.
        #
        # On THIS repo this arm carries more weight than it does in the source:
        # with no Claude App installed, a formal review from Tom is currently
        # the only thing that can make this gate green at all.
        verdict = run_gate(pr(reviews=[review("APPROVED", login=ALLOW_LISTED_REVIEWER)]))
        assert verdict["state"] == "success"

    @pytest.mark.parametrize(
        "association", ["OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR", "NONE"]
    )
    def test_no_author_association_substitutes_for_the_login_allow_list(self, association):
        """The rule is the login list, and no payload field may stand in for it.

        This is the fixture-shaped hole from bluelinky PR #53, closed from the
        other side. Two plausible widenings of the formal-review arm read
        `author_association` -- `!= null`, and
        `IN("OWNER","MEMBER","COLLABORATOR")` -- and both reopen the false
        green this class exists to pin. With the field *absent* from the
        fixture neither could be contested: `null` fails every comparison, so
        the suite reported a pass for the wrong reason.

        It also pins the narrowing **as a decision**. A future collaborator's
        `APPROVED` does not satisfy this gate, which is deliberate while
        `["tclancy"]` is the whole human population of the repo. The
        alternative is one line, stated here rather than cited so the next
        reader needs nothing but this file::

            select((.user.login | is_bot($reviewers))
                   or (.author_association
                       | IN("OWNER", "MEMBER", "COLLABORATOR")))

        It reads the payload instead of a literal and so survives a new
        collaborator. Raised on bluelinky PR #53 as the trade-off Tom is
        calibrating; if he takes it, this test is what goes red and gets
        rewritten, which is the point of pinning it rather than leaving it to
        omission.
        """
        verdict = run_gate(
            pr(
                reviews=[
                    review("APPROVED", login="random-passerby", author_association=association)
                ]
            )
        )
        assert verdict["state"] == "failure", (
            f"an APPROVED carrying author_association={association!r} from a login that is "
            "not on `reviewer_logins` satisfied the gate; the allow-list is the rule"
        )

    def test_the_reviewer_list_is_configurable_and_defaults_without_one(self):
        assert run_gate(pr(reviewer_logins=["someone-else"]))["state"] == "failure"
        explicit = run_gate(
            pr(reviewer_logins=["someone-else"], reviews=[review("APPROVED", login="someone-else")])
        )
        assert explicit["state"] == "success"
        # Absent the key entirely, the default applies.
        implicit = run_gate(
            {
                "number": 1,
                "head_committed_at": HEAD_TS,
                "comments": [],
                "reviews": [review("APPROVED", login=ALLOW_LISTED_REVIEWER)],
            }
        )
        assert implicit["state"] == "success"

    def test_the_two_allow_lists_are_not_interchangeable(self):
        # The review bot's login must not satisfy the formal arm, and Tom's must
        # not satisfy the bot-comment arm. Merging the lists would pass both.
        bot_formal = run_gate(pr(reviews=[review("APPROVED", login="claude[bot]")]))
        assert bot_formal["state"] == "failure"
        human_comment = run_gate(pr(comments=[comment(REAL_FINISHED_COMMENT, login="tclancy")]))
        assert human_comment["state"] == "failure"


class TestTheCommentIsScoredOnWhenItWasEdited:
    def test_an_edit_after_the_head_counts_even_though_creation_predates_it(self):
        verdict = run_gate(
            pr(
                comments=[
                    comment(REAL_FINISHED_COMMENT, created_at=BEFORE_HEAD, updated_at=AFTER_HEAD)
                ]
            )
        )
        assert verdict["state"] == "success"
        assert AFTER_HEAD in verdict["description"], "scored on created_at, not updated_at"

    def test_creation_after_the_head_does_not_rescue_a_stale_edit(self):
        # The inverse, so the test above cannot pass by reading either field.
        verdict = run_gate(
            pr(
                comments=[
                    comment(REAL_FINISHED_COMMENT, created_at=AFTER_HEAD, updated_at=BEFORE_HEAD)
                ]
            )
        )
        assert verdict["state"] == "failure"


class TestStaleness:
    def test_a_review_older_than_the_head_is_refused(self):
        verdict = run_gate(pr(comments=[comment(REAL_FINISHED_COMMENT, updated_at=BEFORE_HEAD)]))
        assert verdict["state"] == "failure"
        assert "predate its head commit" in verdict["description"]
        assert "/review" in verdict["description"]

    def test_the_stale_refusal_differs_from_the_absent_refusal(self):
        stale = run_gate(pr(comments=[comment(REAL_FINISHED_COMMENT, updated_at=BEFORE_HEAD)]))
        absent = run_gate(pr())
        assert stale["description"] != absent["description"], (
            "the two refusals have different causes and must send the reader to different fixes"
        )

    def test_a_review_exactly_at_the_head_timestamp_counts(self):
        verdict = run_gate(pr(comments=[comment(REAL_FINISHED_COMMENT, updated_at=HEAD_TS)]))
        assert verdict["state"] == "success"

    def test_a_null_head_timestamp_cannot_make_anything_stale(self):
        """The behaviour is pinned; the CLAUSE that spells it cannot be.

        `is_fresh` reads `if . == null or $head == null then true else . >= $head`,
        and the two disjuncts are not alike. Measured on jq 1.8.1 during this
        port's mutation round:

        * Dropping `. == null` is **killed** here, by
          `test_a_null_submitted_at_is_not_treated_as_stale` -- `null >= "a"`
          is `false`, so a PENDING review with no `submitted_at` would be
          graded stale.
        * Dropping `$head == null` **survives**, and not because of a fixture
          gap. jq has a total order in which `null` sorts below every string,
          so `"2026-10-23T03:00:00Z" >= null` is already `true` and the `else`
          arm returns the same answer the disjunct does. No input can
          distinguish them, so no test here or anywhere can contest it.

        It stays in the source as a statement of intent -- relying on jq's
        ordering of `null` against a string is the kind of thing a reader
        would "simplify" in the wrong direction -- and it is recorded here so
        the next person running a round reads that survivor as equivalence
        rather than as a hole. This test pins the OUTCOME, which is what
        matters, and that outcome is the same either way.
        """
        verdict = run_gate(
            pr(
                head_committed_at=None,
                comments=[comment(REAL_FINISHED_COMMENT, updated_at=BEFORE_HEAD)],
            )
        )
        assert verdict["state"] == "success"

    def test_one_fresh_review_among_stale_ones_is_enough(self):
        verdict = run_gate(
            pr(
                comments=[
                    comment(REAL_FINISHED_COMMENT, updated_at=BEFORE_HEAD),
                    comment(REAL_FINISHED_COMMENT, updated_at=AFTER_HEAD),
                ]
            )
        )
        assert verdict["state"] == "success"
        assert AFTER_HEAD in verdict["description"]


class TestFormalReviews:
    def test_the_helper_defaults_to_an_allow_listed_reviewer(self):
        # Every test in this class relies on it. Stated, because if the default
        # drifted off the allow-list they would all go red for a reason that has
        # nothing to do with what they are named for.
        assert review("APPROVED")["user"]["login"] == ALLOW_LISTED_REVIEWER

    def test_the_helpers_derive_a_production_shaped_author_association(self):
        """The default `author_association` is relied on and so is pinned.

        `test_an_arbitrary_github_account_cannot_turn_the_gate_green` and
        `test_an_unrelated_login_is_refused` depend on the non-allow-listed
        default being a real non-null value -- that is what makes an
        association-reading widening *contestable* rather than refused for the
        wrong reason. And the Tom-shaped cases depend on the owner's being
        `OWNER`, so a narrowing cannot turn them red while production works.
        Both directions, both fixtures.
        """
        assert association_for(ALLOW_LISTED_REVIEWER) == "OWNER"
        assert association_for("random-passerby") == "NONE"
        assert review("APPROVED")["author_association"] == "OWNER"
        assert review("APPROVED", login="random-passerby")["author_association"] == "NONE"
        assert comment("x")["author_association"] == "NONE"
        assert comment("x", login=ALLOW_LISTED_REVIEWER)["author_association"] == "OWNER"
        # The override still wins, or the parametrized guard below would be
        # driving the derived value rather than the one it names.
        assert review("APPROVED", author_association="MEMBER")["author_association"] == "MEMBER"

    @pytest.mark.parametrize("state", ["APPROVED", "CHANGES_REQUESTED"])
    def test_a_formal_verdict_satisfies_the_gate(self, state):
        verdict = run_gate(pr(reviews=[review(state)]))
        assert verdict["state"] == "success"
        assert state in verdict["description"]

    @pytest.mark.parametrize("state", ["COMMENTED", "DISMISSED", "PENDING"])
    def test_a_non_verdict_review_state_does_not(self, state):
        assert run_gate(pr(reviews=[review(state)]))["state"] == "failure"

    def test_a_null_submitted_at_is_not_treated_as_stale(self):
        verdict = run_gate(pr(reviews=[review("APPROVED", submitted_at=None)]))
        assert verdict["state"] == "success"

    def test_a_stale_formal_review_is_refused(self):
        verdict = run_gate(pr(reviews=[review("APPROVED", submitted_at=BEFORE_HEAD)]))
        assert verdict["state"] == "failure"
        assert "predate its head commit" in verdict["description"]

    def test_a_bot_comment_is_reported_in_preference_to_a_formal_review(self):
        verdict = run_gate(
            pr(comments=[comment(REAL_FINISHED_COMMENT)], reviews=[review("APPROVED")])
        )
        assert verdict["state"] == "success"
        assert "comment by claude[bot]" in verdict["description"]


class TestTheDependabotExemption:
    def test_dependabot_is_exempt_with_no_review_at_all(self):
        verdict = run_gate(pr(author="dependabot[bot]"))
        assert verdict["state"] == "success"
        assert "Dependabot" in verdict["description"]

    def test_a_chore_title_is_NOT_exempt(self):
        # Deliberate: a title prefix is a one-word self-service bypass for any
        # agent, which is the opposite of what the gate is for. The remedy is
        # `/review`, so this is backpressure rather than a dead end.
        verdict = run_gate(pr(title="chore(deps): bump something"))
        assert verdict["state"] == "failure"

    def test_a_login_merely_containing_dependabot_is_not_exempt(self):
        assert run_gate(pr(author="not-dependabot[bot]-really"))["state"] == "failure"


class TestTheDescriptionFitsTheCommitStatusField:
    """GitHub truncates a status description past 140 characters."""

    def test_every_verdict_in_this_suite_fits(self):
        docs = [
            pr(),
            pr(author="dependabot[bot]"),
            pr(comments=[comment(REAL_FINISHED_COMMENT)]),
            pr(comments=[comment(REAL_FINISHED_COMMENT, updated_at=BEFORE_HEAD)]),
            pr(reviews=[review("APPROVED")]),
        ]
        for doc in docs:
            assert len(run_gate(doc)["description"]) <= 140

    def test_a_refusal_is_bounded_even_when_the_input_is_absurd(self):
        # Named for what it tests. Its first name claimed truncation, but a
        # non-allow-listed login never reaches the description at all -- the
        # verdict is the fixed "No code review for this head" string -- so
        # replacing `truncate140` with the identity left it green. The pass-path
        # truncation is covered by the next test, which does drive it.
        verdict = run_gate(pr(comments=[comment(REAL_FINISHED_COMMENT, login="x" * 300)]))
        assert verdict["state"] == "failure"
        assert len(verdict["description"]) <= 140

    def test_truncation_actually_triggers_on_an_overlong_why(self):
        verdict = run_gate(
            pr(
                bot_logins=["b" * 200],
                comments=[comment(REAL_FINISHED_COMMENT, login="b" * 200)],
            )
        )
        assert verdict["state"] == "success"
        assert len(verdict["description"]) == 140
        assert verdict["description"].endswith("...")


class TestTheVerdictShape:
    @pytest.mark.parametrize(
        "doc",
        [pr(), pr(author="dependabot[bot]"), pr(comments=[comment(REAL_FINISHED_COMMENT)])],
        ids=["absent", "exempt", "present"],
    )
    def test_the_verdict_has_exactly_the_two_expected_keys(self, doc):
        verdict = run_gate(doc)
        assert set(verdict) == {"state", "description"}
        assert verdict["state"] in {"success", "failure"}

    def test_missing_optional_fields_do_not_crash_the_gate(self):
        # `require-review.yml` builds this document from four API calls; a
        # field absent from an unexpected payload must not take the gate down,
        # because a dead gate publishes `error` and tells nobody anything.
        verdict = run_gate({"comments": [], "reviews": []})
        assert verdict["state"] == "failure"
        assert "#0" in verdict["description"]
