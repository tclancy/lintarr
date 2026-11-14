"""Disagreements between trace() and real qBittorrent that a person has seen and
understood (spec "Known disagreements").

A scenario listed here passes only while qBittorrent keeps producing exactly the
recorded `observed` trace AND that trace still differs from the model. Either
changing turns the scenario red. An entry is added by hand in a PR, never by the
harness: that PR is the "seen and understood" step.

The findings F1-F3 are in docs/measurements/2026-11-13-qbt-conformance-assumptions.md
("Stop-rule findings"); Tom ruled them seen and understood on 2026-11-13. The
raw states behind each entry are in the same doc, "Differential observations
(Task 5, 2026-11-13)".
"""

from dataclasses import dataclass

from tests.model.queue import Counts


@dataclass(frozen=True)
class Known:
    observed: tuple[Counts, ...]  # one Counts per scenario event, as observed
    why: str  # opens with the finding that explains it, e.g. "F1: ..."
    tracked_in: str


_MEASURED = "(measurements 2026-11-13)"
_SEEDERS_IDLE = Counts(0, 0, 5, 0, 0)
# 3 downloads start and 3 seeders end queuedUP. In 1b and the slow-on variants all
# 5 seeders were active, so 3 yielded max_active_torrents slots. In
# 1-wedged-slow-off the upload limit had already queued 2 (F2), so only 1 yielded.
_DOWNLOADS_DISPLACE = Counts(3, 0, 2, 3, 0)
# Scenario 7: 2 downloads displace 2 of 3 seeders; removing the one active seeder
# lets one queuedUP seeder resume. No download was ever queued. Step 2 (a queuedUP
# seeder resuming after the delete) was first observed in this differential, not
# in Task 4; the raw rows are in the measurements doc, "Differential observations".
_REMOVE_SEEDER = (Counts(0, 0, 3, 0, 0), Counts(2, 0, 1, 2, 0), Counts(2, 0, 1, 1, 0))

_F1 = f"F1: seeders yield max_active_torrents slots to new downloads {_MEASURED}"
_F3 = (
    "F3: slow exemption does not free max_active_torrents, so seeders past the "
    f"limit go queuedUP (with F1: the downloads take the slots) {_MEASURED}"
)

_STEP2_NEW = (
    "; step 2, a queuedUP seeder resuming into the freed slot, was first observed "
    "in this differential, not measured in Task 4 (measurements 2026-11-13, "
    "Differential observations)"
)

KNOWN_DISAGREEMENTS: dict[str, Known] = {
    "1-wedged-slow-off": Known(
        observed=(Counts(0, 0, 3, 2, 0), _DOWNLOADS_DISPLACE),
        why=(
            "F2, F1: max_active_uploads=3 queues 2 of 5 seeders at step 0 (F2); the "
            f"3 downloads then start and displace a third seeder (F1) {_MEASURED}"
        ),
        tracked_in="CONTINUATION.md, Open items: F2 and F1",
    ),
    "1-wedged-slow-on": Known(
        observed=(_SEEDERS_IDLE, _DOWNLOADS_DISPLACE),
        why=_F3,
        tracked_in="CONTINUATION.md, Open items: F3",
    ),
    "1b-wedged-uploads-unlimited-slow-off": Known(
        observed=(_SEEDERS_IDLE, _DOWNLOADS_DISPLACE),
        why=_F1,
        tracked_in="CONTINUATION.md, Open items: F1",
    ),
    "1b-wedged-uploads-unlimited-slow-on": Known(
        observed=(_SEEDERS_IDLE, _DOWNLOADS_DISPLACE),
        why=_F3,
        tracked_in="CONTINUATION.md, Open items: F3",
    ),
    "7-remove-seeder-slow-off": Known(
        observed=_REMOVE_SEEDER,
        why=_F1 + _STEP2_NEW,
        tracked_in="CONTINUATION.md, Open items: F1",
    ),
    "7-remove-seeder-slow-on": Known(
        observed=_REMOVE_SEEDER,
        why=_F3 + _STEP2_NEW,
        tracked_in="CONTINUATION.md, Open items: F3",
    ),
}
