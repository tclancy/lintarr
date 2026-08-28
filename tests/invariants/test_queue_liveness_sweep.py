"""Level 1 validation: does the closed form agree with an executable model?

This catches errors in DERIVING the closed form. It cannot catch a wrong model
— a sweep over the predicate's own parameters cannot discover a parameter the
predicate is missing. That is P2's job.

The magnitude range exists to confirm the predicate is INSENSITIVE to
magnitude, since it tests only whether a limit binds. If any premise ever
becomes magnitude-sensitive, this box must widen.

`ul` is swept even though neither side reads it: an upload limit gates seeding
slots and cannot stop a download from starting, so the sweep is where that
invariance is pinned rather than asserted.
"""

import itertools

from hypothesis import given, settings
from hypothesis import strategies as st

from lintarr.invariants.queue_liveness import check
from lintarr.outcomes import Outcome
from tests.fixtures.homelab import qbt_with
from tests.invariants.test_queue_liveness import NO_GOALS
from tests.model.queue import QueueConfig, simulate

LIMITS = (-1, 0, 1, 2, 3, 4, 5, 6)

# The model answers "does this wedge with N torrents"; the predicate answers
# "can this configuration wedge at all". They are comparable only at an N large
# enough to exceed every limit in LIMITS — below that the model correctly says
# "not wedged" for a config that certainly can wedge, and the sweep would report
# a wall of spurious disagreements that someone would then "fix" by breaking the
# model. Wedging is monotone in N, so one sufficiently large N suffices.
SWEEP_TORRENTS = 100
assert SWEEP_TORRENTS > max(LIMITS), (
    "N must exceed every limit for this comparison to mean anything"
)


def _model(dl: int, ul: int, tot: int, slow: bool, share: bool) -> QueueConfig:
    return QueueConfig(
        queueing_enabled=True,
        max_active_downloads=dl,
        max_active_uploads=ul,
        max_active_torrents=tot,
        dont_count_slow_torrents=slow,
        share_limit_enabled=share,
    )


def _predicate(dl: int, ul: int, tot: int, slow: bool, share: bool) -> bool:
    q = qbt_with(
        max_active_downloads=dl,
        max_active_uploads=ul,
        max_active_torrents=tot,
        dont_count_slow_torrents=slow,
        max_ratio_enabled=share,
        max_seeding_time_enabled=False,
    )
    return check(q, NO_GOALS).outcome is Outcome.FAIL


def test_closed_form_matches_the_model_exhaustively():
    mismatches = []
    for dl, ul, tot, slow, share in itertools.product(
        LIMITS, LIMITS, LIMITS, (False, True), (False, True)
    ):
        predicted = _predicate(dl, ul, tot, slow, share)
        observed = simulate(_model(dl, ul, tot, slow, share), n_torrents=SWEEP_TORRENTS)
        if predicted != observed:
            mismatches.append((dl, ul, tot, slow, share, predicted, observed))
    assert not mismatches, f"{len(mismatches)} disagreements, first: {mismatches[0]}"


@given(
    dl=st.sampled_from(LIMITS),
    ul=st.sampled_from(LIMITS),
    tot=st.sampled_from(LIMITS),
    slow=st.booleans(),
    share=st.booleans(),
    n=st.integers(min_value=max(LIMITS) + 1, max_value=500),
)
@settings(max_examples=300, deadline=None)
def test_closed_form_matches_the_model_on_random_queues(dl, ul, tot, slow, share, n):
    """The sweep's comparison over random large N.

    N starts above max(LIMITS) for the reason recorded at SWEEP_TORRENTS: below
    that the two are answering different questions, not disagreeing.
    """
    assert _predicate(dl, ul, tot, slow, share) == simulate(_model(dl, ul, tot, slow, share), n)


def test_the_model_is_n_dependent_and_the_predicate_is_not():
    """Documents WHY the sweep pins N, so nobody later simplifies it away."""
    wedging = _model(3, 3, 5, slow=False, share=False)
    assert simulate(wedging, n_torrents=5) is False
    assert simulate(wedging, n_torrents=6) is True
    assert _predicate(3, 3, 5, False, False) is True
