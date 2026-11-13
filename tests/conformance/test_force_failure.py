"""The R8 drill's deliberately wrong prediction (no Docker).

CONFORMANCE_FORCE_FAILURE=true must make the differential go red. A Known entry
passes whenever observed equals its recording and differs from the prediction,
so corrupting a Known scenario's prediction would be absorbed. The drill must
target an unregistered scenario.
"""

import pytest

from tests.conformance.known import KNOWN_DISAGREEMENTS
from tests.conformance.runner import force_failure_scenario, judge, wrong_prediction
from tests.conformance.scenarios import SCENARIOS
from tests.model.queue import trace


def test_the_drill_targets_a_scenario_no_known_entry_can_absorb():
    target = force_failure_scenario(SCENARIOS, KNOWN_DISAGREEMENTS)
    assert target.id not in KNOWN_DISAGREEMENTS


def test_the_corrupted_prediction_differs_and_judge_raises():
    target = force_failure_scenario(SCENARIOS, KNOWN_DISAGREEMENTS)
    real = trace(target.cfg, target.events)
    wrong = wrong_prediction(real)
    assert wrong != real
    assert len(wrong) == len(real)
    # Observed == the real model trace: the only disagreement is the corruption.
    with pytest.raises(AssertionError, match="disagreement"):
        judge(target.id, real, wrong, KNOWN_DISAGREEMENTS)


def test_every_scenario_if_it_were_first_still_fails_when_corrupted():
    for s in SCENARIOS:
        real = trace(s.cfg, s.events)
        assert wrong_prediction(real) != real, s.id


def test_no_unregistered_scenario_means_no_drill():
    only_known = tuple(s for s in SCENARIOS if s.id in KNOWN_DISAGREEMENTS)
    with pytest.raises(ValueError):
        force_failure_scenario(only_known, KNOWN_DISAGREEMENTS)
