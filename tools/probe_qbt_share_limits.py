#!/usr/bin/env python3
"""Print what each qBittorrent share-limit action does, globally and per category.

Produces the table behind docs/measurements/2026-11-21-qbt-share-limit-actions.md.
A manual tool, not a test: it records, it pins nothing. The pins are
tests/conformance/test_share_limit_actions.py, and both read the same table,
`tests/conformance/firing.FATES`, so the probe cannot drift from them::

    uv run python tools/probe_qbt_share_limits.py

Needs Docker. One container, started by `qbt_with_categories()` with a category
per action already in its config (the 5.2.3 WebAPI cannot set one; see
tests/conformance/categories.py). Each case adds a complete, peerless torrent,
arms the global ratio limit at 0 and waits for the limit to fire.

The last column says whether the observation equals the committed table. A `no`
there is the news: either qBittorrent changed or the table is wrong.
"""

import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # the harness lives under tests/, not in the package

from tests.conformance.categories import qbt_with_categories  # noqa: E402
from tests.conformance.firing import (  # noqa: E402
    FATE_BY_NAME,
    FATES,
    Fate,
    Observed,
    arm_ratio_zero,
    disarm,
    watch_until_fired,
)
from tests.conformance.image import QBT_IMAGE  # noqa: E402
from tests.conformance.qbt import QbtHarness  # noqa: E402
from tests.conformance.torrents import make_torrent  # noqa: E402
from tests.conformance.watching import SHARE_LIMIT_TIMEOUT, add_settled  # noqa: E402

#: The fate of each action, by the value `max_ratio_act` carries.
FATE_BY_ACTION = {fate.action: fate for fate in FATES}

#: One category per action, plus `Default` to show the redirect.
CATEGORIES = {fate.name.lower(): fate.name for fate in FATES} | {"default": "Default"}

HEADER = "| case | action | state after | files | super seeding | fired after | matches the table |"
RULE = "|---|---|---|---|---|---|---|"


def _row(case: str, action: str, seen: Observed, fate: Fate, seconds: float) -> str:
    state = seen.state or "gone from torrents/info"
    files = "kept" if seen.file_exists else "deleted"
    agrees = "yes" if seen == fate.observed else f"NO (expected {fate.observed})"
    seeding = "on" if seen.super_seeding else "off"
    fired = f"{seconds:.1f} s"
    return f"| {case} | {action} | {state} | {files} | {seeding} | {fired} | {agrees} |"


def _fire(
    qbt: QbtHarness, torrents: Mapping[str, object], action: int
) -> tuple[dict[str, Observed], float]:
    """Arm the global ratio limit at 0; report each torrent's fate and how long it took."""
    started = time.monotonic()
    arm_ratio_zero(qbt, action)
    seen = watch_until_fired(qbt, torrents, SHARE_LIMIT_TIMEOUT)
    return seen, time.monotonic() - started


def probe_global(qbt: QbtHarness) -> None:
    """Each `max_ratio_act` value, one torrent at a time."""
    rows = []
    for fate in FATES:
        disarm(qbt, fate.action)
        t = make_torrent(f"probe-global-{fate.action}")
        add_settled(qbt, t, complete=True, want="stalledUP")
        seen, seconds = _fire(qbt, {"t": t}, fate.action)
        rows.append(_row(f"global {fate.action}", fate.name, seen["t"], fate, seconds))
    print("\n## the global action (`max_ratio_act`)\n")
    print("\n".join([HEADER, RULE, *rows]))


def probe_default_global(qbt: QbtHarness) -> None:
    """Whether the global can report `Default` (-1) at all."""
    disarm(qbt, 2)
    before = qbt.preferences()["max_ratio_act"]
    qbt.set_prefs({"max_ratio_act": -1})
    after = qbt.preferences()["max_ratio_act"]
    print(f"\nmax_ratio_act: set 2 -> reads {before}; then set -1 -> reads {after}")


def probe_categories(qbt: QbtHarness, action: int) -> None:
    """Every category's action at once, under one global action."""
    disarm(qbt, action)
    torrents = {name: make_torrent(f"probe-cat-{name}-{action}") for name in CATEGORIES}
    for name, t in torrents.items():
        add_settled(qbt, t, complete=True, want="stalledUP", category=name)
    seen, seconds = _fire(qbt, torrents, action)
    named = FATE_BY_ACTION[action].name
    print(f"\n## the category action, under global {action} ({named})\n")
    rows = [
        _row(
            f"category {name}",
            reported,
            seen[name],
            FATE_BY_NAME.get(reported) or FATE_BY_ACTION[action],
            seconds,
        )
        for name, reported in CATEGORIES.items()
    ]
    print("\n".join([HEADER, RULE, *rows]))


def main() -> int:
    with TemporaryDirectory(prefix="qbt-share-limits-") as tmp:
        with qbt_with_categories(Path(tmp), CATEGORIES) as qbt:
            print(f"{datetime.now(UTC).isoformat(timespec='seconds')}  image {QBT_IMAGE}")
            print(f"qBittorrent {qbt.version()}")
            print("\ncategories as the endpoint reports them:")
            for name, body in sorted(qbt.categories().items()):
                print(f"  {name}: share_limit_action={body['share_limit_action']!r}")
            probe_global(qbt)
            probe_default_global(qbt)
            for fate in (FATE_BY_NAME["RemoveWithContent"], FATE_BY_NAME["Stop"]):
                probe_categories(qbt, fate.action)
    return 0


if __name__ == "__main__":
    sys.exit(main())
