from tests.model.queue import QueueConfig, simulate

WEDGED = QueueConfig(
    queueing_enabled=True,
    max_active_downloads=3,
    max_active_uploads=3,
    max_active_torrents=5,
    dont_count_slow_torrents=False,
    share_limit_enabled=False,
)
REPAIRED = QueueConfig(
    queueing_enabled=True,
    max_active_downloads=6,
    max_active_uploads=3,
    max_active_torrents=10,
    dont_count_slow_torrents=True,
    share_limit_enabled=True,
)


def test_the_real_incident_wedges():
    assert simulate(WEDGED, n_torrents=52) is True


def test_the_repaired_config_does_not_wedge():
    assert simulate(REPAIRED, n_torrents=52) is False


def test_a_share_limit_alone_prevents_the_wedge():
    """Seeders leaving is what frees the slot."""
    import dataclasses

    assert simulate(dataclasses.replace(WEDGED, share_limit_enabled=True), 52) is False


def test_slow_torrent_exemption_alone_prevents_the_wedge():
    import dataclasses

    assert simulate(dataclasses.replace(WEDGED, dont_count_slow_torrents=True), 52) is False


def test_queueing_disabled_never_wedges():
    import dataclasses

    assert simulate(dataclasses.replace(WEDGED, queueing_enabled=False), 52) is False


def test_zero_active_downloads_wedges_immediately():
    import dataclasses

    cfg = dataclasses.replace(REPAIRED, max_active_downloads=0)
    assert simulate(cfg, n_torrents=2) is True


def test_unlimited_sentinel_never_wedges():
    import dataclasses

    cfg = dataclasses.replace(
        WEDGED, max_active_downloads=-1, max_active_uploads=-1, max_active_torrents=-1
    )
    assert simulate(cfg, 52) is False


def test_fewer_torrents_than_slots_never_wedges():
    assert simulate(WEDGED, n_torrents=1) is False


def test_the_wedge_boundary_is_exact():
    """WEDGED has max_active_torrents=5, so 5 torrents drain and 6 wedge.

    Pins the >= in slots_free(): an off-by-one there passes every other test
    in this file while changing thousands of configurations.
    """
    assert simulate(WEDGED, n_torrents=5) is False
    assert simulate(WEDGED, n_torrents=6) is True


def test_the_downloads_boundary_is_exact():
    """Pins the >= on max_active_downloads — but the real threshold here is
    0 vs 1, not an arbitrary N vs N+1.

    Unlike max_active_torrents (whose `active` accumulates seeders and so has
    a genuine cumulative threshold), `downloading` resets to 0 at the end of
    every round. That makes the batch size each round `min(max_active_downloads,
    room)`, which converges to the same eventual cumulative total regardless of
    the exact value of max_active_downloads, as long as it is >= 1 — only the
    value 0 (no slot ever opens) is distinguishable from "some positive value."
    Confirmed empirically: mutating this comparison to `>` changes the answer
    on zero sampled configs with max_active_downloads >= 1 (200k random draws),
    and only diverges at exactly 0. So the meaningful pin is 0-wedges /
    1-does-not, with max_active_torrents set high enough to never itself bind.
    """
    import dataclasses

    cfg_zero = dataclasses.replace(WEDGED, max_active_torrents=1000, max_active_downloads=0)
    cfg_one = dataclasses.replace(WEDGED, max_active_torrents=1000, max_active_downloads=1)
    assert simulate(cfg_zero, n_torrents=1) is True
    assert simulate(cfg_one, n_torrents=1) is False


def test_the_answer_is_invariant_under_max_active_uploads():
    """The uploads limit gates seeding, not whether a download can start.

    Deliberate: wiring it into slots_free() would make this model disagree
    with the predicate and silently break the sweep it exists to validate.
    """
    import dataclasses

    answers = {
        simulate(dataclasses.replace(WEDGED, max_active_uploads=u), 52) for u in (-1, 0, 1, 3)
    }
    assert answers == {True}


def test_the_answer_is_invariant_under_max_active_uploads_when_repaired():
    import dataclasses

    answers = {
        simulate(dataclasses.replace(REPAIRED, max_active_uploads=u), 52) for u in (-1, 0, 1, 3)
    }
    assert answers == {False}


def test_zero_active_torrents_wedges_immediately():
    """The other half of the slots_free() hoist: mat=0 with the slow-torrent
    exemption on. An exemption cannot manufacture a slot that never existed."""
    import dataclasses

    assert simulate(dataclasses.replace(REPAIRED, max_active_torrents=0), 2) is True


def test_empty_queue_never_wedges():
    """Nothing queued can never satisfy 'at least one torrent is still
    queued', regardless of how starved the config is.

    Uses a zero-slots config (max_active_downloads=0) deliberately: with
    WEDGED's own non-zero limits, slots_free() is already true at the empty
    initial state, so the inner loop's own `queued == 0` check would return
    False anyway and the n_torrents <= 0 guard would never be exercised. A
    config that is otherwise an immediate wedge (per the hoist check) is
    needed to prove the guard, not just the loop, is doing the work.
    """
    import dataclasses

    cfg = dataclasses.replace(WEDGED, max_active_downloads=0)
    assert simulate(cfg, 0) is False
