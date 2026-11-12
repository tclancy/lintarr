import pytest

from tests.conformance.states import Bucket, UnmappedState, bucket_of, tally
from tests.model.queue import Counts

TABLE = [
    ("downloading", Bucket.DOWNLOADING),
    ("stalledDL", Bucket.DOWNLOADING),
    ("metaDL", Bucket.DOWNLOADING),
    ("forcedDL", Bucket.DOWNLOADING),
    ("queuedDL", Bucket.QUEUED),
    ("uploading", Bucket.SEEDING),
    ("stalledUP", Bucket.SEEDING),
    ("forcedUP", Bucket.SEEDING),
    ("queuedUP", Bucket.SEEDING_QUEUED),
    ("pausedUP", Bucket.LEFT),
    ("stoppedUP", Bucket.LEFT),
]


@pytest.mark.parametrize(("state", "bucket"), TABLE)
def test_every_mapped_state(state, bucket):
    assert bucket_of(state) is bucket


@pytest.mark.parametrize("state", ["missingFiles", "checkingUP", "error", ""])
def test_unknown_state_raises(state):
    with pytest.raises(UnmappedState) as exc:
        bucket_of(state)
    assert exc.value.args == (state,)


def test_tally_counts_each_bucket():
    states = {
        "a": "downloading",
        "b": "queuedDL",
        "c": "queuedDL",
        "d": "uploading",
        "e": "queuedUP",
        "f": "pausedUP",
    }
    assert tally(states, set(states)) == Counts(1, 2, 1, 1, 1)


def test_vanished_torrent_counts_as_left():
    assert tally({"a": "downloading"}, {"a", "gone"}) == Counts(1, 0, 0, 0, 1)
