"""The fixtures are evidence. These tests stop them drifting into convenience."""

import dataclasses

from lintarr.facts import is_known
from lintarr.models import QbtInstance
from tests.fixtures.homelab import qbt_with, repaired_qbt, wedged_qbt


def _fact_field_names() -> list[str]:
    """Every Fact-typed field on QbtInstance, derived so this cannot go stale.

    Hardcoding the list is what let four fields go unchecked; adding four more
    names would only move the next gap further out.
    """
    return [f.name for f in dataclasses.fields(QbtInstance) if f.name not in ("name", "version")]


def test_wedged_matches_the_values_recorded_in_homelab_393():
    q = wedged_qbt()
    assert q.max_active_downloads.value == 3
    assert q.max_active_torrents.value == 5
    assert q.dont_count_slow_torrents.value is False
    assert q.max_ratio_enabled.value is False
    assert q.max_seeding_time_enabled.value is False
    assert q.queueing_enabled.value is True


def test_repaired_matches_the_documented_values():
    q = repaired_qbt()
    assert q.max_active_downloads.value == 6
    assert q.max_active_torrents.value == 10
    assert q.dont_count_slow_torrents.value is True
    assert q.max_ratio_enabled.value is True
    assert q.max_ratio.value == 1.5
    assert q.max_seeding_time_enabled.value is True


def test_fact_field_names_covers_every_fact_field():
    """A filter bug that silently returns nothing must not make the integrity test vacuous."""
    names = _fact_field_names()
    assert len(names) == 16
    assert names  # non-empty, spelled out for clarity alongside the count above


#: Added after #393, which never recorded them; deliberately Unknown in the incident fixtures.
_NOT_IN_393 = ("save_path", "auto_tmm_enabled", "use_category_paths_in_manual_mode")


def test_every_fact_in_both_fixtures_is_known():
    """A fixture with an accidental Unknown would silently turn FAIL into SKIP."""
    for build in (wedged_qbt, repaired_qbt):
        q = build()
        for name in _fact_field_names():
            if name in _NOT_IN_393:
                continue
            assert is_known(getattr(q, name)), f"{build.__name__}.{name} is not Known"


def test_the_fields_393_never_recorded_are_unknown_in_both_fixtures():
    for build in (wedged_qbt, repaired_qbt):
        for name in _NOT_IN_393:
            fact = getattr(build(), name)
            assert not is_known(fact)
            assert fact.detail == "not recorded in homelab#393"


def test_qbt_with_overrides_one_fact_and_leaves_the_rest():
    q = qbt_with(max_active_torrents=99)
    assert q.max_active_torrents.value == 99
    assert q.max_active_downloads.value == 6


def test_qbt_with_passes_through_an_already_wrapped_fact():
    from lintarr.facts import Unknown, is_known

    q = qbt_with(max_ratio_enabled=Unknown("field-absent", "max_ratio_enabled"))
    assert not is_known(q.max_ratio_enabled)
    assert q.max_ratio_enabled.reason == "field-absent"


def test_qbt_with_overrides_categories():
    q = qbt_with(categories={"tv-sonarr": {}})
    assert q.categories.value == {"tv-sonarr": {}}
