import dataclasses
import itertools

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tests.model.queue import (
    AddComplete,
    AddIncomplete,
    Counts,
    FinishDownloads,
    ModelUndefined,
    QueueConfig,
    RemoveSeeder,
    simulate,
    trace,
)
from tests.model.test_queue_model import WEDGED

LIMITS = (-1, 0, 1, 2, 3, 4, 5, 6)


def test_incident_wedges_step_by_step():
    last = trace(WEDGED, [AddComplete(5), AddIncomplete(3)])[-1]
    assert last == Counts(downloading=0, queued=3, seeding=5, seeding_queued=0, left=0)


def test_share_limit_drains():
    cfg = dataclasses.replace(WEDGED, share_limit_enabled=True)
    assert trace(cfg, [AddComplete(5), AddIncomplete(3)])[-1] == Counts(3, 0, 0, 0, 5)


def test_slow_exemption_releases_the_wedge():
    cfg = dataclasses.replace(WEDGED, dont_count_slow_torrents=True)
    assert trace(cfg, [AddComplete(5), AddIncomplete(3)])[-1] == Counts(3, 0, 5, 0, 0)


def test_slow_exemption_does_not_rescue_zero_downloads():
    cfg = dataclasses.replace(WEDGED, max_active_downloads=0, dont_count_slow_torrents=True)
    assert trace(cfg, [AddIncomplete(2)])[-1] == Counts(0, 2, 0, 0, 0)


def test_remove_seeder_frees_exactly_one():
    events = [AddComplete(5), AddIncomplete(3), RemoveSeeder()]
    assert trace(WEDGED, events)[-1] == Counts(1, 2, 4, 0, 0)


def test_zero_downloads_blocks_first_start():
    cfg = dataclasses.replace(WEDGED, max_active_downloads=0)
    assert trace(cfg, [AddIncomplete(2)])[-1] == Counts(0, 2, 0, 0, 0)


def test_overfull_complete_is_undefined():
    with pytest.raises(ModelUndefined):
        trace(WEDGED, [AddComplete(6)])


def test_remove_seeder_without_seeders_is_undefined():
    with pytest.raises(ModelUndefined):
        trace(WEDGED, [AddIncomplete(1), RemoveSeeder()])


def test_one_counts_per_event():
    assert len(trace(WEDGED, [AddComplete(1), AddIncomplete(1), RemoveSeeder()])) == 3


@given(
    dl=st.sampled_from(LIMITS),
    ul=st.sampled_from(LIMITS),
    tot=st.sampled_from(LIMITS),
    slow=st.booleans(),
    share=st.booleans(),
    c=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=500, deadline=None)
def test_add_complete_equals_finishing_downloads(dl, ul, tot, slow, share, c):
    """M5: AddComplete(c) must equal c incomplete torrents that all finish.

    This cross-checks the AddComplete handler (used by every container
    scenario) against the path R3 ties to simulate().
    """
    cfg = QueueConfig(True, dl, ul, tot, slow, share)
    assume(trace(cfg, [AddIncomplete(c)])[-1].queued == 0)
    via_finish = trace(cfg, [AddIncomplete(c), FinishDownloads()])[-1]
    assert trace(cfg, [AddComplete(c)])[-1] == via_finish


def test_trace_agrees_with_simulate_over_the_sweep_box():
    """R3: simulate()'s world is n incomplete torrents that finish;
    FinishDownloads replays it.
    """
    mismatches = []
    for dl, ul, tot, slow, share in itertools.product(
        LIMITS, LIMITS, LIMITS, (False, True), (False, True)
    ):
        cfg = QueueConfig(True, dl, ul, tot, slow, share)
        for n in range(1, 11):
            events = [AddIncomplete(n)] + [FinishDownloads()] * (n + 1)
            if (trace(cfg, events)[-1].queued > 0) != simulate(cfg, n):
                mismatches.append((cfg, n))
    assert not mismatches, f"{len(mismatches)} disagreements, first: {mismatches[0]}"
