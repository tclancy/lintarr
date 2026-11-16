"""The uploading-seeder outcomes measured in
docs/measurements/2026-11-16-qbt-uploading-seeders.md, as (before, after) Counts.

Every scenario in UPLOAD_SCENARIOS resolved 3 of 3 runs, so all four are pinned.
A scenario the doc marks unresolved must not be added here
(test_recorded_uploading.py checks that).
"""

from tests.model.queue import Counts

_U1 = (Counts(0, 0, 3, 0, 0), Counts(1, 0, 2, 1, 0))

RECORDED: dict[str, tuple[Counts, Counts]] = {
    "U0-off": _U1,
    "U1-off": _U1,
    "U1-on": _U1,
    "W-off": (Counts(0, 0, 3, 2, 0), Counts(3, 0, 2, 3, 0)),
}
