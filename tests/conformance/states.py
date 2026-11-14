"""Map qBittorrent torrent states onto the model's Counts buckets."""

from collections import Counter
from collections.abc import Mapping
from enum import StrEnum

from tests.model.queue import Counts


class Bucket(StrEnum):
    DOWNLOADING = "downloading"
    QUEUED = "queued"
    SEEDING = "seeding"
    SEEDING_QUEUED = "seeding_queued"
    LEFT = "left"


class UnmappedState(Exception):
    """A qBittorrent state the spec's table does not cover."""


_BUCKETS: dict[str, Bucket] = {
    "downloading": Bucket.DOWNLOADING,
    "stalledDL": Bucket.DOWNLOADING,
    "metaDL": Bucket.DOWNLOADING,
    "forcedDL": Bucket.DOWNLOADING,
    "queuedDL": Bucket.QUEUED,
    "uploading": Bucket.SEEDING,
    "stalledUP": Bucket.SEEDING,
    "forcedUP": Bucket.SEEDING,
    "queuedUP": Bucket.SEEDING_QUEUED,
    "pausedUP": Bucket.LEFT,
    "stoppedUP": Bucket.LEFT,
}


def bucket_of(state: str) -> Bucket:
    try:
        return _BUCKETS[state]
    except KeyError:
        raise UnmappedState(state) from None


def tally(states: Mapping[str, str], expected_hashes: set[str]) -> Counts:
    """Count expected torrents per bucket; one absent from `states` is LEFT."""
    counts = Counter(bucket_of(states[h]) if h in states else Bucket.LEFT for h in expected_hashes)
    return Counts(
        downloading=counts[Bucket.DOWNLOADING],
        queued=counts[Bucket.QUEUED],
        seeding=counts[Bucket.SEEDING],
        seeding_queued=counts[Bucket.SEEDING_QUEUED],
        left=counts[Bucket.LEFT],
    )
