"""The stale-container sweep selects only containers whose expiry has passed."""

from tests.conformance.conftest import _expired


def test_only_past_expiries_are_selected():
    lines = ["aaa 100", "bbb 200", "ccc 300"]
    assert _expired(lines, now=200) == ["aaa", "bbb"]


def test_malformed_lines_are_never_selected():
    lines = ["", "only-id", "ddd notanumber", "eee 1 extra", "fff 5"]
    assert _expired(lines, now=1000) == ["fff"]
