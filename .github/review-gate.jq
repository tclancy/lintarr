# Review-gate decision logic for `require-review.yml`.
#
# A pure function: JSON in, verdict out. No network, no `gh`, no state. The
# workflow does the fetching; this file does the deciding, which is why it can
# be unit-tested against fixtures by `tests/test_review_gate_decision.py`.
#
# PROVENANCE. Ported from `tclancy/parsons-remote`'s copy at blob
# `736303679dcddba97b39bf73681ce38ee86e5aa9` (parsons-remote PR #26), which was
# itself ported from `tclancy/bluelinky` PR #52. This is the second half of the
# rollout Tom greenlit on `tclancy/bluelinky#35` -- option (a), "a reviewer plus
# a status bound to the head", for the public repos. That ticket's final list
# names this repo, and this is the last of the four.
#
# THE COPY SOURCE IS parsons-remote AND NOT bluelinky, deliberately. bluelinky's
# `main` is missing three fixes that landed downstream of it, and bluelinky #53
# -- which carries them there -- is still open as of 2026-11-01: the login
# filter on the formal-review arm below, the guard-step assertions pinned by
# slice rather than by presence, and a `review()` fixture that carries
# `author_association`. Diff against the blob above before assuming a difference
# here is a bug.
#
# THIS IS THE THIRD COPY OF THIS LOGIC IN THE FLEET, and the sizing argument
# that was deferred at two is now live. bluelinky#35 said "two is a port; three
# is a composite action" -- that line was written when there were two. There are
# three. Factoring these ~260 lines plus `require-review.yml` into a composite
# action (or a reusable workflow) in a PUBLIC repo the other three can reference
# is the obvious next move, and it is Tom's call rather than something a shift
# decides on its own: it changes where the fleet's gate logic lives. Named in
# this PR's body and in the project file, filed nowhere -- lintarr and
# metaframework are both off the filing list (perennial, fallow).
#
# WHAT DIFFERS FROM parsons-remote's COPY, and it is one thing. Every note in
# that file whose claim was about *that* repo has been re-measured against this
# one and rewritten where the answer changed; those are marked `DIVERGES (n)` at
# the point of divergence -- (1) is in this file, (2) and (3) are in
# `require-review.yml`. There is NO behavioural difference, and that was
# verified rather than asserted: with every full-line comment and blank line
# stripped from both files, this one and the blob above are byte-identical
# (checked 2026-11-01). Nothing in CI can re-check it -- the blob lives in
# another repo -- so treat it as a dated provenance claim, not as a guard.
# Three copies that *decide* differently would be worse than three copies,
# which is why the divergences are confined to prose.
#
# INPUT
#   {
#     "number":            42,
#     "head_committed_at": "2026-10-23T03:00:00Z" | null,
#     "author":            "<PR author login>",
#     "title":             "<PR title>",
#     "bot_logins":        ["claude[bot]"],
#     "reviewer_logins":   ["tclancy"],
#     "comments":          [ GitHub issue-comment objects ],
#     "reviews":           [ GitHub review objects ]
#   }
#
# OUTPUT
#   { "state": "success" | "failure", "description": "<=140 chars" }

# Timestamps are compared as STRINGS, not parsed. Every value here comes from
# the same GitHub REST API in the same `YYYY-MM-DDTHH:MM:SSZ` shape, and for
# two same-shaped ISO-8601 UTC stamps lexicographic order *is* chronological
# order. `fromdateiso8601` would be more obviously correct and also introduces
# a parse-failure mode (it rejects fractional seconds) into a file whose whole
# job is to return a verdict; string comparison has no failure mode at all.
#
# A null on either side means "cannot be shown to be stale", so the review
# counts -- refusing to invent a refusal out of an absent field (a PENDING
# review has `submitted_at: null`).
def is_fresh($head):
  if . == null or $head == null then true else . >= $head end;

# Whether a review-bot comment represents a review that actually HAPPENED.
#
# THE FIRST VERSION OF THIS RULE WAS "no outstanding `- [ ]` items", and it was
# wrong in the one direction that matters. That rule only rejects a non-review
# if the non-review carries an UNTICKED BOX -- and the two comment shapes
# `claude-code-action` emits when it has reviewed nothing carry no checklist at
# all, so both sailed through as reviews:
#
#   The in-flight comment. `track_progress: true` forces tag mode, which posts
#   this at PREPARE time, before any review work happens:
#
#       Claude Code is working… <img .../>
#       I'll analyze this and get back to you.
#       [View job run](...)
#
#   The errored-run comment:
#
#       **Claude encountered an error** —— [View job](...)
#
#   Neither has a `- [x]` or a `- [ ]`. Under the old rule the gate published
#   `success` seconds after the reviewer spawned. That is metaframework#687 --
#   "18 PRs passed the gate with zero reviews" -- which this file would have
#   reintroduced in a new repo.
#
# So the rule has two parts. An outstanding box still disqualifies anything.
# On top of that, a comment that IS one of the action's own status comments
# must say it finished, or carry a completed checklist:
#
#   shape                                        verdict  why
#   in-flight "Claude Code is working…"          no       no checklist, not finished
#   errored, no checklist                        no       no checklist, not finished
#   errored, partial checklist                   no       outstanding box
#   errored, completed checklist                 YES      keeps metaframework#560
#   "**Claude finished ... in 3m 48s**"          YES      finished
#   plain summary posted via `gh pr comment`     YES      not a status comment
#   prose quoting `- [ ] ` mid-sentence          YES      marker is line-anchored
#
# The errored-but-completed row is deliberate and is NOT laxness: metaframework
# #560 found that an `error_max_turns` run has read the diff and posted its
# findings before blowing its budget. Rejecting those would silently reverse a
# measured decision.
#
# DIVERGES (1), and it is the sharpest difference between this copy and
# parsons-remote's. That file records that all three of its real reviews land on
# the `says_finished` row, so the shape it actually produces is covered by the
# narrowest accepting row and only the checklist rows are inherited untested.
# **Here NONE of the three accepting rows has ever seen live data.**
# `gh api repos/tclancy/lintarr/issues/comments` filtered to `claude[bot]`
# returned 0 on 2026-11-01: the Claude GitHub App is not installed on this repo,
# so no review bot has ever posted in it. Every accepting row below is therefore
# inherited on the strength of the other two repos' measurements, and the first
# real review here is the first test of any of them. Worth knowing before
# reading a surprising verdict as a logic bug.
#
# One consequence of all this: the gate's green arrives on a comment EDIT (the
# in-flight comment is edited into the "finished" one), which is why
# `require-review.yml` listens for `issue_comment: [created, edited]` and
# scores comments on `updated_at`. On `created` alone the gate could never see
# a run finish.

# The action's own status comments, identified by their leading status line.
# `^\*{0,2}` because the in-flight line is unbolded and the other two are
# bolded, and `encountered an error` is matched as a PREFIX so it covers both
# `**Claude encountered an error**` and `**Claude encountered an error after
# 2m 10s**`.
def is_action_status:
  test("^\\*{0,2}Claude (Code is working|finished|encountered an error)");

def says_finished: test("^\\*{0,2}Claude finished");

# `(^|\n)` rather than `\n` alone: a body whose very first bytes are a
# checklist item has no preceding newline. No real shape measured here starts
# that way, but the anchor costs nothing and the alternative is a rule that
# silently stops applying to a shape nobody has seen yet.
def has_outstanding_item: test("(^|\n)- \\[ \\] ");
def has_completed_item:   test("(^|\n)- \\[x\\] ");

def counts_as_review:
  (. != null)
  and (. != "")
  # Line-anchored on purpose: a review that *discusses* checklists in prose
  # should not be thrown away for quoting the marker mid-sentence.
  and (has_outstanding_item | not)
  and (if is_action_status
       then (says_finished or has_completed_item)
       else true end);

def truncate140:
  if length > 140 then (.[0:137] + "...") else . end;

def pass($why): { state: "success", description: ($why | truncate140) };
def fail($why): { state: "failure", description: ($why | truncate140) };

# The ONE exemption, and it is on the AUTHOR, never on a title prefix.
#
# `claude-code-review.yml`'s `if:` declines two kinds of PR: Dependabot's, and
# any whose title starts with `chore`. The obvious move is to exempt both here
# so the gate never demands a review the reviewer declines to perform. The
# first is right; the second is not. The title-prefix heuristic over-matches (a
# human `chore:` PR goes unreviewed) and under-matches (a hand-retitled
# Dependabot PR gets gated again). In a gate whose whole purpose is
# backpressure on AGENT PRs, a title prefix is a one-word self-service bypass:
# any agent can title its PR `chore:` and never face a reviewer.
#
# Leaving `chore` out does not create an unremediable red X, because this gate
# HAS a remedy that works without Tom: a `/review` comment runs the reviewer
# and re-publishes this status (see the refusal message below). So a `chore` PR
# reads red until someone asks for a review, which is backpressure rather than
# a dead end.
#
# `tests/test_require_review_workflow.py` asserts the AUTHOR lists agree
# between the two files, and asserts the reviewer's title-prefix list is
# exactly `chore` -- so adding a `docs` prefix over there is red here, rather
# than silently creating the dead end this paragraph says cannot happen.
def exempt_reason:
  if .author == "dependabot[bot]"
  then "Review not required: Dependabot PR -- the reviewer skips these by design."
  else null end;

# `index`, not `inside`/`contains`: on arrays those two do SUBSTRING matching
# against each element, so `["claude"] | inside(["claude[bot]"])` is true and
# any login that is a prefix of an allow-listed one would satisfy the gate.
# Measured on jq 1.8.1: `["claude[bot]"] | index("claude")` is null, while
# `["claude[bot]"] | index(["claude[bot]"])` is 0 -- an array argument is a
# subsequence search, a scalar argument is an exact element match.
#
# `. as $l` FIRST, and that line is load-bearing. The obvious spelling,
# `($bots | index(. // ""))`, rebinds `.` to `$bots` as it crosses the pipe, so
# it evaluates `index($bots)` -- the subsequence form above -- and returns 0 for
# every input. That is a predicate that cannot fail: it admitted the login
# `"totally-unrelated"`. Pinned by the prefix-login case in
# `tests/test_review_gate_decision.py`.
#
# `index(null)` returns null rather than erroring, so a comment with no author
# needs no separate guard.
def is_bot($bots): . as $l | ($bots | index($l)) != null;

def candidates($bots; $reviewers):
  # Comments first, then formal reviews -- so a PR satisfied by a bot review
  # comment reports that rather than a formal review that happens to coexist.
  #
  # `updated_at`, not `created_at`: the tracking comment is created unticked
  # and EDITED to completion, so its creation time is not when the review
  # existed. The trade-off, stated: editing an old comment refreshes it. An
  # edit by the review bot is the self-heal path this gate depends on, and an
  # edit by a human to their own review comment is a deliberate act by the one
  # person the gate exists to apply backpressure for.
  [ ( .comments[]?
      | select(.user.login | is_bot($bots))
      | select(.body | counts_as_review)
      | { ts: .updated_at,
          why: "Review present: comment by \(.user.login // "?") at \(.updated_at // "?")" } ),
    # THE LOGIN FILTER ON THIS ARM IS LOAD-BEARING, and it is the fix that made
    # parsons-remote #26 the copy source rather than bluelinky `main`. Found in
    # review on 2026-11-01 and reproduced against the real program:
    #
    #   reviews: [{user: {login: "random-passerby"}, state: "CHANGES_REQUESTED"}]
    #     -> {"state":"success","description":"Review present: formal ..."}
    #
    # This repo is PUBLIC (`gh repo view tclancy/lintarr --json visibility` is
    # `PUBLIC`, 2026-11-01), so any authenticated GitHub user can submit a
    # review on an open PR and turn the gate green having read nothing. Worse
    # for the gate's actual threat model: GitHub permits one fleet bot to
    # approve another's PR, so `metaframework-dispatch-bot[bot]` approving a
    # `dispatch-bot[bot]` PR satisfied it too. That is the same one-command
    # self-service bypass this file rejects `chore:` titles for.
    #
    # `$reviewers`, not `$bots`: the two lists answer different questions and
    # must not be merged. A review BOT satisfies the gate by posting a comment;
    # a HUMAN satisfies it by submitting a formal verdict. Defaulting
    # `reviewer_logins` to `["tclancy"]` keeps this arm as the Tom-shaped escape
    # hatch it is meant to be -- and on this repo, with no App installed, it is
    # currently the ONLY arm that can ever fire.
    ( .reviews[]?
      | select(.user.login | is_bot($reviewers))
      | select(.state == "APPROVED" or .state == "CHANGES_REQUESTED")
      | { ts: .submitted_at,
          why: "Review present: formal \(.state) by \(.user.login // "?") at \(.submitted_at // "?")" } )
  ];

. as $pr
| ($pr.bot_logins // ["claude[bot]"]) as $bots
| ($pr.reviewer_logins // ["tclancy"]) as $reviewers
| $pr.head_committed_at as $head
| ($pr | exempt_reason) as $exempt
| if $exempt != null then pass($exempt)
  else
    ($pr | candidates($bots; $reviewers)) as $all
    | ($all | map(select(.ts | is_fresh($head)))) as $fresh
    | if ($fresh | length) > 0 then pass($fresh[0].why)
      elif ($all | length) > 0 then
        # Two refusals, because the two have different causes and the
        # description is published as the commit-status text -- the
        # highest-traffic instruction site in the gate. Collapsing them sends
        # the reader to the wrong fix.
        #
        # BOTH NAME `/review`, and there is a limitation on that which is
        # specific to this repo's situation rather than to the mechanism. A
        # COMMIT STATUS attaches to whatever SHA you POST it to, and
        # `require-review.yml` resolves the PR head and posts there from every
        # trigger -- which is the entire reason this gate is a status and not a
        # check-run, whose verdict would bind to the SHA of its own event
        # (metaframework#620). So the `/review` chain closes in principle: the
        # comment fires the reviewer, the reviewer posts as `claude[bot]`, that
        # comment fires this workflow, and the status on the head flips.
        #
        # **It cannot close here yet**, for two reasons that are both about
        # provisioning and neither about this logic: the Claude GitHub App is
        # not installed on this repo, so nothing answers a `/review`; and an
        # `issue_comment` run always executes the DEFAULT BRANCH's copy of a
        # workflow, so until this file is on `main` no `/review` can fire the
        # gate either. See `require-review.yml`'s `issue_comment` note. Until
        # Tom provisions the App, the working remedy is his own formal
        # APPROVED / CHANGES_REQUESTED, which arrives as `pull_request_review`
        # and does carry the PR ref.
        #
        # A re-gate (`gh pr close N && gh pr reopen N`) also re-fires this
        # workflow on the current head, and is what an agent can raise without
        # moving the head SHA.
        fail("Reviews on this PR all predate its head commit (newest \(($all | map(.ts) | max) // "?") vs head \($head // "?")). Comment /review to re-review this head.")
      else
        fail("No code review for this head. Comment /review on PR #\($pr.number // 0) to run the reviewer -- it re-publishes this status.")
      end
  end
