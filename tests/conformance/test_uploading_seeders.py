"""Nightly pin: each uploading-seeder scenario still yields the recorded counts.

Needs Docker. A mismatch is an AssertionError (kind `disagreement`); a harness
problem raises HarnessError from observe/judge.
"""

import itertools

import pytest

from tests.conformance.containers import Pair
from tests.conformance.recorded_uploading import RECORDED
from tests.conformance.runner import with_one_retry
from tests.conformance.uploading import UPLOAD_SCENARIOS, Recording, UploadScenario, judge, observe

pytestmark = pytest.mark.container

_REPS = itertools.count(1)  # module-wide, so a retry never reuses torrent names
_SCENARIOS = {sc.id: sc for sc in UPLOAD_SCENARIOS}
_TAIL_S = 30.0


def _tail(rec: Recording) -> str:
    """The final 30 s of window polls, one line per poll."""
    if not rec.window:
        return "(no window polls)"
    cutoff = rec.window[-1].t - _TAIL_S
    return "\n".join(
        f"t={p.t}: " + ", ".join(f"{r.name}={r.state}/{r.upspeed}" for r in p.rows)
        for p in rec.window
        if p.t >= cutoff
    )


def _assert_matches_measurement(pair: Pair, sc: UploadScenario) -> None:
    rec = observe(pair, sc, rep=next(_REPS))
    o = judge(sc, rec)
    want = RECORDED[sc.id]
    assert (o.before, o.after) == want, (
        f"{sc.id}: recorded {want}, measured {(o.before, o.after)}, verdict {o.verdict}\n"
        f"final 30 s of polls:\n{_tail(rec)}"
    )


@pytest.mark.parametrize("scenario_id", list(RECORDED))
def test_uploading_scenario_matches_measurement(qbt_pair: Pair, scenario_id: str) -> None:
    with_one_retry(lambda: _assert_matches_measurement(qbt_pair, _SCENARIOS[scenario_id]))
