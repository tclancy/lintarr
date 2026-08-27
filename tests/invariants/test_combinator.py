from datetime import UTC, datetime

import pytest

from lintarr.facts import Known, Unknown
from lintarr.invariants.combinator import DuplicatePremiseLabel, conflict_if, premise
from lintarr.outcomes import Outcome


def _known(value: bool) -> Known[bool]:
    return Known(value=value, source="GET /x", read_at=datetime.now(UTC), service_version="v1")


def test_all_premises_true_is_a_conflict():
    f = conflict_if("inv", "qbt[main]", premise("a", True), premise("b", True))
    assert f.outcome is Outcome.FAIL
    assert [p.label for p in f.premises] == ["a", "b"]


def test_one_false_premise_is_a_pass():
    f = conflict_if("inv", "qbt[main]", premise("a", True), premise("b", False))
    assert f.outcome is Outcome.PASS
    assert f.premises == ()


def test_unknown_premise_skips_even_when_others_would_conflict():
    """A missing input must never be read as agreement."""
    f = conflict_if(
        "inv", "qbt[main]", premise("a", True), premise("b", Unknown("field-absent", "b"))
    )
    assert f.outcome is Outcome.SKIP


def test_skip_names_only_the_unknown_premises():
    f = conflict_if(
        "inv",
        "qbt[main]",
        premise("a", True),
        premise("b", Unknown("field-absent", "b")),
        premise("c", Unknown("service-absent", "c")),
    )
    assert [p.label for p in f.premises] == ["b", "c"]


def test_premise_unwraps_a_known_fact():
    assert premise("a", _known(True)).state is True
    assert premise("a", _known(False)).state is False


def test_duplicate_labels_are_rejected():
    with pytest.raises(DuplicatePremiseLabel, match="a"):
        conflict_if("inv", "qbt[main]", premise("a", True), premise("a", False))


def test_a_conflict_carries_every_premise_not_just_the_true_ones():
    """For a conjunction the whole set is the explanation; a partial set would mislead."""
    f = conflict_if("inv", "qbt[main]", premise("a", True), premise("b", True), premise("c", True))
    assert len(f.premises) == 3
