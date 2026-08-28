from lintarr.outcomes import Finding, Outcome, Premise, exit_code, worst


def test_outcome_na_renders_as_slash_form():
    assert Outcome.NOT_APPLICABLE == "N/A"


def test_worst_is_empty_pass():
    assert worst([]) is Outcome.PASS


def test_worst_follows_precedence():
    assert worst([Outcome.PASS, Outcome.FAIL]) is Outcome.FAIL
    assert worst([Outcome.FAIL, Outcome.SKIP]) is Outcome.SKIP
    assert worst([Outcome.SKIP, Outcome.ERROR]) is Outcome.ERROR
    assert worst([Outcome.ERROR, Outcome.NOT_APPLICABLE]) is Outcome.ERROR


def test_not_applicable_outranks_skip():
    """The one ordering no other pair pins down: reversing these passes every other test."""
    assert worst([Outcome.SKIP, Outcome.NOT_APPLICABLE]) is Outcome.NOT_APPLICABLE


def test_exit_code_error_outranks_fail():
    assert exit_code([Outcome.FAIL, Outcome.ERROR]) == 2


def test_exit_code_fail_is_one():
    assert exit_code([Outcome.PASS, Outcome.FAIL]) == 1


def test_exit_code_incomplete_is_three_under_strict():
    assert exit_code([Outcome.PASS, Outcome.SKIP]) == 3
    assert exit_code([Outcome.PASS, Outcome.NOT_APPLICABLE]) == 3


def test_exit_code_incomplete_is_zero_without_strict():
    assert exit_code([Outcome.PASS, Outcome.SKIP], strict=False) == 0


def test_all_pass_is_zero():
    assert exit_code([Outcome.PASS, Outcome.PASS]) == 0


def test_premise_state_none_means_unknown():
    assert Premise(label="x", state=None).state is None


def test_finding_defaults_to_no_premises_and_no_detail():
    f = Finding(invariant="inv", instance="qbt[main]", outcome=Outcome.PASS)
    assert f.premises == ()
    assert f.detail == ""
