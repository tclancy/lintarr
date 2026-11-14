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
    assert tally(states, set(states)) == Counts(
        downloading=1, queued=2, seeding=1, seeding_queued=1, left=1
    )


def test_vanished_torrent_counts_as_left():
    assert tally({"a": "downloading"}, {"a", "gone"}) == Counts(
        downloading=1, queued=0, seeding=0, seeding_queued=0, left=1
    )


def test_extra_hashes_in_states_are_ignored():
    states = {"a": "downloading", "stale": "uploading"}
    assert tally(states, {"a"}) == Counts(
        downloading=1, queued=0, seeding=0, seeding_queued=0, left=0
    )


def test_extra_hash_with_unmapped_state_does_not_raise():
    states = {"a": "downloading", "stale": "missingFiles"}
    assert tally(states, {"a"}) == Counts(
        downloading=1, queued=0, seeding=0, seeding_queued=0, left=0
    )
