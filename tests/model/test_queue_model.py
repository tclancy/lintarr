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
