"""The harness itself works against a real container. Needs Docker."""

import time

import pytest

from tests.conformance.states import Bucket, UnmappedState, bucket_of
from tests.conformance.torrents import make_torrent

pytestmark = pytest.mark.container


def _await_bucket(qbt, info_hash: str, want: Bucket, timeout: float = 30.0) -> str:
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        state = qbt.states().get(info_hash)
        try:
            if state is not None and bucket_of(state) == want:
                return state
        except UnmappedState:
            pass  # transient (e.g. checkingResumeData); the deadline still bounds it
        time.sleep(0.5)
    pytest.fail(f"{info_hash} never reached {want}; last state {state!r}")


def _await_present(qbt, info_hash: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if info_hash in qbt.states():
            return
        time.sleep(0.25)
    pytest.fail(f"{info_hash} never appeared in the client")


def test_container_reports_expected_version(qbt):
    assert qbt.version().startswith("v5.2.3")


def test_complete_torrent_reaches_seeding(qbt):
    qbt.reset()
    t = make_torrent("smoke-complete")
    qbt.add(t, complete=True)
    state = _await_bucket(qbt, t.info_hash, Bucket.SEEDING)
    assert bucket_of(state) == Bucket.SEEDING


def test_incomplete_torrent_downloads(qbt):
    qbt.reset()
    t = make_torrent("smoke-incomplete")
    qbt.add(t, complete=False)
    state = _await_bucket(qbt, t.info_hash, Bucket.DOWNLOADING)
    assert state == "stalledDL"


def test_reset_empties_client(qbt):
    qbt.reset()
    t = make_torrent("smoke-reset")
    qbt.add(t, complete=False)
    _await_present(qbt, t.info_hash)
    qbt.reset()
    assert qbt.states() == {}
