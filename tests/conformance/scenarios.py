"""The differential's scenarios (spec "Scenario set"): a model config, the full
qBittorrent preferences it stands for, and the events to drive.

`prefs` is always built by `prefs_for` from `cfg`, so the two cannot drift, and
it always sets the same full key set (`PREF_KEYS`): `QbtHarness.reset()` only
deletes torrents, so a key left out would be inherited from the scenario before.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace

from tests.fixtures.homelab import _REPAIRED, _WEDGED
from tests.model.queue import (
    UNLIMITED,
    AddComplete,
    AddIncomplete,
    Event,
    QueueConfig,
    RemoveSeeder,
)

# Measured: timer 1 lets the slow exemption act within one settle
# (docs/measurements/2026-11-13-qbt-conformance-assumptions.md, item 3a).
SLOW_TIMER_S = 1

# The model reads these five straight off the preferences.
_CFG_KEYS = (
    "queueing_enabled",
    "max_active_downloads",
    "max_active_uploads",
    "max_active_torrents",
    "dont_count_slow_torrents",
)
_SHARE_KEYS = (
    "max_ratio_enabled",
    "max_ratio",
    "max_ratio_act",
    "max_seeding_time_enabled",
    "max_inactive_seeding_time_enabled",
)
PREF_KEYS = frozenset(_CFG_KEYS + _SHARE_KEYS + ("slow_torrent_inactive_timer",))

# Share-limit actions that free the slot: stop, remove, remove with files.
# 2 (super seeding) keeps the torrent active, so trace() rule 4 would not hold.
RELEASING_ACTIONS = frozenset({0, 1, 3})


@dataclass(frozen=True)
class Scenario:
    id: str
    cfg: QueueConfig
    prefs: dict[str, object]
    events: tuple[Event, ...]


def _share_limit_on(prefs: Mapping[str, object]) -> bool:
    return bool(prefs["max_ratio_enabled"] or prefs["max_seeding_time_enabled"])


def _check_overrides(overrides: Mapping[str, object]) -> None:
    unknown = overrides.keys() - PREF_KEYS
    derived = overrides.keys() & set(_CFG_KEYS)
    if unknown or derived:
        raise ValueError(f"override not allowed: {sorted(unknown | derived)}")


def _check_share_limit(cfg: QueueConfig, prefs: Mapping[str, object]) -> None:
    if _share_limit_on(prefs) != cfg.share_limit_enabled:
        raise ValueError("share-limit preferences disagree with cfg.share_limit_enabled")
    if cfg.share_limit_enabled and prefs["max_ratio_act"] not in RELEASING_ACTIONS:
        raise ValueError(f"max_ratio_act {prefs['max_ratio_act']} does not release the slot")


def prefs_for(cfg: QueueConfig, *, slow_timer_s: int, **overrides: object) -> dict[str, object]:
    """The full preference set `cfg` stands for.

    A share limit, when on, is `max_ratio=0` (measured to fire about 10 s after
    a zero-upload add, item 1), with action stop unless overridden.
    """
    _check_overrides(overrides)
    prefs: dict[str, object] = {key: getattr(cfg, key) for key in _CFG_KEYS}
    prefs |= {
        "max_ratio_enabled": cfg.share_limit_enabled,
        "max_ratio": 0 if cfg.share_limit_enabled else -1,
        "max_ratio_act": 0,
        "max_seeding_time_enabled": False,
        "max_inactive_seeding_time_enabled": False,
        "slow_torrent_inactive_timer": slow_timer_s,
    }
    prefs |= overrides
    _check_share_limit(cfg, prefs)
    return prefs


def _homelab_cfg(values: Mapping[str, object], *, share_limit_enabled: bool) -> QueueConfig:
    return QueueConfig(
        **{key: values[key] for key in _CFG_KEYS},  # type: ignore[arg-type]
        share_limit_enabled=share_limit_enabled,
    )


def _limits(dl: int, ul: int, tot: int, *, share: bool = False, queueing: bool = True):
    return QueueConfig(queueing, dl, ul, tot, False, share)


def _both_slow(
    base_id: str, cfg: QueueConfig, events: tuple[Event, ...], **overrides: object
) -> Iterator[Scenario]:
    """The scenario with `dont_count_slow_torrents` off, then on."""
    for slow in (False, True):
        c = replace(cfg, dont_count_slow_torrents=slow)
        prefs = prefs_for(c, slow_timer_s=SLOW_TIMER_S, **overrides)
        yield Scenario(f"{base_id}-slow-{'on' if slow else 'off'}", c, prefs, events)


_U = UNLIMITED
_WEDGED_CFG = _homelab_cfg(_WEDGED, share_limit_enabled=False)
# Spec scenario 2's substitution: the real limits (ratio 1.5, 14 days) cannot fire
# in a run with zero upload, so ratio 0 and no seeding-time limit stand in for
# them. The real action (3, remove with files) is kept.
_REPAIRED_CFG = _homelab_cfg(_REPAIRED, share_limit_enabled=True)
_SEEDERS_THEN_DOWNLOADS = (AddComplete(5), AddIncomplete(3))

SCENARIOS: tuple[Scenario, ...] = (
    # 1. The homelab#393 wedge, _WEDGED unchanged (its max_ratio_act carried too).
    *_both_slow(
        "1-wedged", _WEDGED_CFG, _SEEDERS_THEN_DOWNLOADS, max_ratio_act=_WEDGED["max_ratio_act"]
    ),
    # 1b. The same with the upload limit unlimited, so the wedge is tested alone.
    *_both_slow(
        "1b-wedged-uploads-unlimited",
        replace(_WEDGED_CFG, max_active_uploads=_U),
        _SEEDERS_THEN_DOWNLOADS,
        max_ratio_act=_WEDGED["max_ratio_act"],
    ),
    # 2. The repaired homelab config (with the share-limit substitution above).
    *_both_slow(
        "2-repaired",
        _REPAIRED_CFG,
        _SEEDERS_THEN_DOWNLOADS,
        max_ratio_act=_REPAIRED["max_ratio_act"],
    ),
    # 3. max_active_downloads=0: wedged at once, with no seeders at all.
    *_both_slow("3-downloads-zero", _limits(0, _U, _U), (AddIncomplete(2),)),
    # 4. Every limit unlimited: nothing ever queues.
    *_both_slow("4-unlimited", _limits(_U, _U, _U), (AddComplete(3), AddIncomplete(3))),
    # 5. The download limit binds and the torrent limit does not: downloaders alone queue.
    *_both_slow("5-downloads-bind", _limits(2, _U, 10), (AddIncomplete(4),)),
    # 6. A share limit (ratio 0, action stop) releases the seeders' torrent slots.
    *_both_slow(
        "6-share-limit-releases",
        _limits(_U, _U, 2, share=True),
        (AddComplete(2), AddIncomplete(2)),
    ),
    # 7. Removing a seeder advances the queue by exactly one.
    *_both_slow(
        "7-remove-seeder", _limits(_U, _U, 3), (AddComplete(3), AddIncomplete(2), RemoveSeeder())
    ),
    # 8. Queueing off: the limits are ignored and nothing queues.
    *_both_slow(
        "8-queueing-off",
        _limits(1, 1, 1, queueing=False),
        (AddComplete(2), AddIncomplete(2)),
    ),
)
