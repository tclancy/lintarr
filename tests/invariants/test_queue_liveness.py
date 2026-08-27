"""What queue-liveness must say about configurations we can name.

The first test is the one the project exists for. The rest fence it in: the
repaired configuration, the per-indexer escape hatch, and the two ways a
configuration can be undecidable rather than healthy.
"""

from datetime import UTC, datetime

from lintarr.facts import Known, Unknown
from lintarr.invariants.queue_liveness import check
from lintarr.models import ArrInstance, IndexerFacts
from lintarr.outcomes import Outcome
from tests.fixtures.homelab import qbt_with, repaired_qbt, wedged_qbt


def _fact(value):
    return Known(value=value, source="GET /x", read_at=datetime.now(UTC), service_version="v1")


def _indexer(*, seed_ratio, seed_time=None, protocol="torrent", enabled=True):
    return IndexerFacts(
        name="1337x",
        protocol=_fact(protocol) if isinstance(protocol, str) else protocol,
        enable_rss=_fact(enabled),
        enable_automatic_search=_fact(enabled),
        enable_interactive_search=_fact(enabled),
        seed_ratio=seed_ratio,
        seed_time=seed_time or Unknown("field-absent", "seed_time"),
        season_pack_seed_time=Unknown("field-absent", "season_pack_seed_time"),
    )


def _arrs(*indexers):
    return (ArrInstance(name="main", kind="sonarr", version="4.0.0", indexers=indexers),)


_NO_RATIO = Unknown("field-absent", "seed_ratio")

# One enabled torrent indexer, varied one axis at a time. Each of the last three
# differs from NO_GOALS in exactly one fact, which is what makes them usable as
# load-bearing pairs in tests/test_needs_are_load_bearing.py.
NO_GOALS = _arrs(_indexer(seed_ratio=_NO_RATIO))
WITH_GOALS = _arrs(_indexer(seed_ratio=_fact(2.0)))
WITH_SEED_TIME_ONLY = _arrs(_indexer(seed_ratio=_NO_RATIO, seed_time=_fact(2880)))
USENET_NO_GOALS = _arrs(_indexer(seed_ratio=_NO_RATIO, protocol="usenet"))
DISABLED_NO_GOALS = _arrs(_indexer(seed_ratio=_NO_RATIO, enabled=False))

# The five values that drifted in homelab#393, as overrides. Kept here so a
# test can vary one more setting on top of the wedge; wedged_qbt() itself is
# the evidence and takes no overrides.
WEDGE = {
    "max_active_downloads": 3,
    "max_active_torrents": 5,
    "dont_count_slow_torrents": False,
    "max_ratio_enabled": False,
    "max_seeding_time_enabled": False,
}


def test_the_real_incident_is_reported_as_a_conflict():
    """homelab#393. This is the whole reason the project exists."""
    f = check(wedged_qbt(), NO_GOALS)
    assert f.outcome is Outcome.FAIL


def test_the_overrides_used_by_later_tests_reproduce_the_incident():
    """WEDGE must stay a faithful stand-in for the recorded configuration."""
    assert check(qbt_with(**WEDGE), NO_GOALS).outcome is Outcome.FAIL
    assert [p.label for p in check(qbt_with(**WEDGE), NO_GOALS).premises] == [
        p.label for p in check(wedged_qbt(), NO_GOALS).premises
    ]


def test_the_repaired_configuration_passes():
    assert check(repaired_qbt(), NO_GOALS).outcome is Outcome.PASS


def test_a_conflict_names_the_settings_responsible():
    labels = {p.label for p in check(wedged_qbt(), NO_GOALS).premises}
    assert "qbt.no_global_ratio" in labels
    assert "qbt.no_global_seed_time" in labels
    assert "qbt.max_active_torrents_binds" in labels


def test_per_indexer_seed_goals_prevent_the_conflict():
    """Global share limits off does not mean seeding is unlimited."""
    assert check(wedged_qbt(), WITH_GOALS).outcome is Outcome.PASS


def test_one_indexer_without_goals_is_enough_to_wedge():
    mixed = _arrs(
        _indexer(seed_ratio=_fact(2.0)),
        _indexer(seed_ratio=Unknown("field-absent", "seed_ratio")),
    )
    assert check(wedged_qbt(), mixed).outcome is Outcome.FAIL


def test_a_usenet_indexer_without_goals_is_irrelevant():
    assert check(wedged_qbt(), USENET_NO_GOALS).outcome is Outcome.PASS


def test_a_disabled_indexer_without_goals_is_irrelevant():
    assert check(wedged_qbt(), DISABLED_NO_GOALS).outcome is Outcome.PASS


def test_a_seed_time_goal_alone_prevents_the_conflict():
    """seedRatio is not the only seed criterion Sonarr exposes.

    An indexer with only seedTime set is an ordinary configuration, and its
    torrents do stop seeding. Reading seed_ratio alone would flag it.
    """
    assert check(wedged_qbt(), WITH_SEED_TIME_ONLY).outcome is Outcome.PASS


def test_an_indexer_of_unknown_protocol_skips_rather_than_passing():
    """models.py's contract: an unparsed protocol must not become "not a torrent".

    Dropping it would report PASS on a stack whose one goal-less indexer we
    could not classify — the exact failure this check exists to find.
    """
    murky = _arrs(
        _indexer(
            seed_ratio=Unknown("field-absent", "s"),
            protocol=Unknown("field-absent", "protocol"),
        )
    )
    f = check(wedged_qbt(), murky)
    assert f.outcome is Outcome.SKIP
    assert [p.label for p in f.premises] == ["arr.indexer_without_seed_criteria"]


def test_an_unclassifiable_indexer_that_sets_goals_does_not_force_a_skip():
    """An indexer with seed criteria cannot wedge the queue whatever it is."""
    murky_but_safe = _arrs(
        _indexer(seed_ratio=_fact(2.0), protocol=Unknown("field-absent", "protocol"))
    )
    assert check(wedged_qbt(), murky_but_safe).outcome is Outcome.PASS


def test_zero_active_downloads_still_conflicts():
    """0 is a legal and immediately catastrophic value."""
    assert check(qbt_with(max_active_downloads=0), NO_GOALS).outcome is Outcome.FAIL


def test_zero_slots_conflicts_even_with_every_seeding_safeguard_on():
    """No seeder is needed to wedge a client that cannot start a first download.

    The repaired base has both share limits on and the slow-torrent exemption
    set — every premise of the seeding conflict is false — and it is still
    wedged, because nothing ever gets a slot.
    """
    f = check(qbt_with(max_active_torrents=0), NO_GOALS)
    assert f.outcome is Outcome.FAIL
    assert [p.label for p in f.premises] == [
        "qbt.queueing_enabled",
        "qbt.no_slot_for_a_first_download",
    ]


def test_both_conflicts_firing_reports_the_starvation_one():
    """Pins which of the two stages explains a doubly-broken client.

    On this configuration BOTH conflicts fire: nothing can start at all, and
    seeders would absorb every slot if anything ever did. Deciding the seeding
    conflict first would report five premises about share limits and indexers,
    none of which an operator can act on while max_active_torrents is 0. The
    verdict is the same either way, so nothing but this test holds the order.
    """
    f = check(qbt_with(**(WEDGE | {"max_active_torrents": 0})), NO_GOALS)
    assert f.outcome is Outcome.FAIL
    assert [p.label for p in f.premises] == [
        "qbt.queueing_enabled",
        "qbt.no_slot_for_a_first_download",
    ]


def test_a_proved_wedge_survives_an_unreadable_limit():
    """SKIP must not swallow FAIL: the seeding conflict never reads this fact.

    A key rename or a permission-scoped API user costs one preference. If that
    downgraded #393 to "could not look", the flagship check would go quiet on a
    stack it can still prove is broken — and the run would exit 3, not 1.
    """
    q = qbt_with(**(WEDGE | {"max_active_downloads": Unknown("field-absent", "mad")}))
    assert check(q, NO_GOALS).outcome is Outcome.FAIL


def test_a_starved_limit_settles_it_however_unreadable_its_partner_is():
    """Either limit at zero is conclusive on its own; OR does not wait on unknowns."""
    unread_downloads = qbt_with(
        max_active_torrents=0,
        max_active_downloads=Unknown("field-absent", "max_active_downloads"),
    )
    unread_total = qbt_with(
        max_active_downloads=0,
        max_active_torrents=Unknown("field-absent", "max_active_torrents"),
    )
    assert check(unread_downloads, NO_GOALS).outcome is Outcome.FAIL
    assert check(unread_total, NO_GOALS).outcome is Outcome.FAIL


def test_a_negative_limit_that_is_not_minus_one_binds():
    """-1 is the only unlimited sentinel; -2 is a limit no count can satisfy."""
    assert check(qbt_with(max_active_torrents=-2), NO_GOALS).outcome is Outcome.FAIL


def test_a_download_limit_alone_does_not_wedge_the_queue():
    """A seeder occupies a total-active slot, never a download slot.

    With max_active_torrents unlimited, completed torrents pile up harmlessly
    and the download slots keep rotating. Flagging this would fail most healthy
    stacks, since max_active_downloads is bounded on nearly all of them.
    """
    q = qbt_with(**(WEDGE | {"max_active_torrents": -1}))
    assert check(q, NO_GOALS).outcome is Outcome.PASS


def test_all_limits_unlimited_passes():
    q = qbt_with(max_active_downloads=-1, max_active_uploads=-1, max_active_torrents=-1)
    assert check(q, NO_GOALS).outcome is Outcome.PASS


def test_queueing_disabled_passes():
    """With no queue there is nothing to wedge: the limits stop applying."""
    q = qbt_with(**(WEDGE | {"queueing_enabled": False}))
    assert check(q, NO_GOALS).outcome is Outcome.PASS


def test_a_category_share_limit_prevents_the_conflict():
    """A category's own ratio limit releases its torrents' slots."""
    q = qbt_with(**(WEDGE | {"categories": {"tv": {"ratio_limit": 2.0, "seeding_time_limit": -2}}}))
    assert check(q, NO_GOALS).outcome is Outcome.PASS


def test_a_category_seeding_time_limit_prevents_the_conflict():
    """Both category share limits count, not just the ratio one."""
    own_time = {"tv": {"ratio_limit": -2, "seeding_time_limit": 20160}}
    assert check(qbt_with(**(WEDGE | {"categories": own_time})), NO_GOALS).outcome is Outcome.PASS


def test_a_category_ratio_limit_of_zero_prevents_the_conflict():
    """0 is a category's own limit — the strictest one — not "inherit global".

    Only -2 inherits. Reading 0 as inheritance would report a FAIL on a stack
    whose categories stop seeding the instant a torrent completes.
    """
    stop_at_once = {"tv": {"ratio_limit": 0.0, "seeding_time_limit": -2}}
    q = qbt_with(**(WEDGE | {"categories": stop_at_once}))
    assert check(q, NO_GOALS).outcome is Outcome.PASS


def test_a_category_that_inherits_the_global_limits_does_not_prevent_it():
    q = qbt_with(**(WEDGE | {"categories": {"tv": {"ratio_limit": -2, "seeding_time_limit": -2}}}))
    assert check(q, NO_GOALS).outcome is Outcome.FAIL


def test_a_null_category_map_is_no_categories_not_an_unknown():
    """A client that reports null categories has none, so none override."""
    q = qbt_with(**(WEDGE | {"categories": None}))
    assert check(q, NO_GOALS).outcome is Outcome.FAIL


def test_an_unknown_required_fact_skips_rather_than_passing():
    q = qbt_with(max_ratio_enabled=Unknown("field-absent", "max_ratio_enabled"))
    f = check(q, NO_GOALS)
    assert f.outcome is Outcome.SKIP
    assert [p.label for p in f.premises] == ["qbt.no_global_ratio"]


def test_an_unreadable_limit_skips_rather_than_passing():
    """Unlimited elsewhere is not evidence that the unread limit is survivable."""
    q = qbt_with(
        max_active_torrents=-1,
        max_active_downloads=Unknown("field-absent", "max_active_downloads"),
    )
    f = check(q, NO_GOALS)
    assert f.outcome is Outcome.SKIP
    assert [p.label for p in f.premises] == ["qbt.no_slot_for_a_first_download"]


def test_a_limit_read_as_something_other_than_a_number_skips():
    """A string where an int belongs is a fact we do not have, not a zero."""
    q = qbt_with(max_active_torrents=_fact("unlimited"))
    assert check(q, NO_GOALS).outcome is Outcome.SKIP
