"""Polling helpers for the container tests: reach a state, or hold one.

Extracted from tests/conformance/test_assumptions.py, which measured the
timeouts below against the pinned image
(docs/measurements/2026-11-13-qbt-conformance-assumptions.md).

The two asserting helpers keep their ``_assert`` prefix through the move:
tests/test_gates.py's assertion gate counts a call to one as an assertion by
exactly that name, and a test whose only assertion is such a call must keep
counting. Importing a private name across harness modules is this repo's
existing shape (``share_limits`` imports ``queue_liveness``'s helpers).
"""

import time
from collections.abc import Callable

import pytest

from tests.conformance.qbt import QbtHarness
from tests.conformance.torrents import HarnessTorrent

# Measured: the share limit fired 8.4-8.9 s after the torrent reached stalledUP
# (one pass of qBittorrent's share-limit check). 30 s is that pass three times.
SHARE_LIMIT_TIMEOUT = 30.0
# Measured: an add settles in 1.3-3.2 s (checkingResumeData, then any queue release).
SETTLE_TIMEOUT = 10.0
# How long a state that should hold is watched, asserted on every poll. Longer
# than libtorrent's 30 s auto-manage interval, so a tick-driven change is caught.
HOLD_SECONDS = 35.0
POLL = 0.25

States = dict[str, str]
Check = Callable[[States], bool]


def _assert_eventually(qbt: QbtHarness, done: Check, timeout: float) -> States:
    deadline = time.monotonic() + timeout
    states = qbt.states()
    while not done(states):
        if time.monotonic() >= deadline:
            pytest.fail(f"condition not met within {timeout:.0f}s; last states {states}")
        time.sleep(POLL)
        states = qbt.states()
    return states


def _assert_holds(qbt: QbtHarness, holds: Check, seconds: float = HOLD_SECONDS) -> None:
    """Assert `holds` on every poll for `seconds`, not only at the end."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        states = qbt.states()
        assert holds(states), f"stopped holding after {seconds:.0f}s window began: {states}"
        time.sleep(POLL)


def state_is(t: HarnessTorrent, want: str) -> Check:
    return lambda states: states.get(t.info_hash) == want


def add_settled(
    qbt: QbtHarness, t: HarnessTorrent, *, complete: bool, want: str, category: str = ""
) -> None:
    qbt.add(t, complete=complete, category=category)
    _assert_eventually(qbt, state_is(t, want), SETTLE_TIMEOUT)
