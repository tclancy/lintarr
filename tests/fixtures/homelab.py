"""Configurations from a real incident, kept as evidence.

Transcribed from tclancy/homelab#393. On 2026-08-25 the whole arr stack sat at
0 kB/s: 52 torrents, 21 of them incomplete, some stalled for weeks, with no
error, no health warning and no alert anywhere. 29 torrents were seeding
indefinitely, several for 45+ days, and every active slot was permanently
occupied by one of them.

The live stack has since been repaired, so #393 is the ONLY surviving record of
the broken configuration. Do not edit these values to make a test pass — change
the code instead, or the project loses the one configuration it knows for
certain produced a silent total stall in production.

`max_active_uploads` was not recorded in #393. Both builders use qBittorrent's
default of 3; that guess was later verified against a live 5.2.3 instance on
2026-08-26 rather than left as an unstated assumption.
"""

from datetime import UTC, datetime
from typing import Any

from lintarr.facts import Known, Unknown
from lintarr.models import QbtInstance

_VERSION = "v5.2.3"
_SOURCE = "GET /api/v2/app/preferences"

_REPAIRED: dict[str, Any] = {
    "queueing_enabled": True,
    "max_active_downloads": 6,
    "max_active_uploads": 3,
    "max_active_torrents": 10,
    "dont_count_slow_torrents": True,
    "max_ratio_enabled": True,
    "max_ratio": 1.5,
    "max_ratio_act": 3,
    "max_seeding_time_enabled": True,
    "max_seeding_time": 20160,
    "categories": {},
}

# The five drifted values. Everything else matched.
_WEDGED: dict[str, Any] = _REPAIRED | {
    "max_active_downloads": 3,
    "max_active_torrents": 5,
    "dont_count_slow_torrents": False,
    "max_ratio_enabled": False,
    "max_seeding_time_enabled": False,
}


def _known(value: Any) -> Known[Any]:
    return Known(value=value, source=_SOURCE, read_at=datetime.now(UTC), service_version=_VERSION)


def _wrap(value: Any) -> Any:
    """Wrap a raw value as Known; pass an already-wrapped Fact through unchanged.

    Lets qbt_with(**overrides) accept an Unknown directly, so a test can build
    "this fact was never read" through the same builder instead of reaching
    for object.__setattr__ on a frozen slotted dataclass.
    """
    return value if isinstance(value, (Known, Unknown)) else _known(value)


def _build(values: dict[str, Any], name: str) -> QbtInstance:
    return QbtInstance(
        name=name,
        version=_VERSION,
        **{key: _wrap(value) for key, value in values.items()},
    )


def wedged_qbt(name: str = "main") -> QbtInstance:
    """The configuration that froze the stack on 2026-08-25. Must report FAIL."""
    return _build(_WEDGED, name)


def repaired_qbt(name: str = "main") -> QbtInstance:
    """The documented configuration that unfroze it. Must report PASS."""
    return _build(_REPAIRED, name)


def qbt_with(name: str = "main", **overrides: Any) -> QbtInstance:
    """A repaired instance with individual preferences overridden.

    An override that is already a Known or Unknown instance passes through
    unchanged; a raw value gets wrapped in Known as usual.
    """
    return _build(_REPAIRED | overrides, name)
