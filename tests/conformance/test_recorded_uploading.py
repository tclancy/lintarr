"""RECORDED is anchored to the measurement doc's Agreement summary table (no Docker needed)."""

from pathlib import Path

import pytest

from tests.conformance.recorded_uploading import RECORDED
from tests.model.queue import Counts

DOC = Path(__file__).parents[2] / "docs/measurements/2026-11-16-qbt-uploading-seeders.md"
AGREED = "agreed"


def _fmt(c: Counts) -> str:
    return f"`({c.downloading},{c.queued},{c.seeding},{c.seeding_queued},{c.left})`"


def _pair(before: Counts, after: Counts) -> str:
    return f"{_fmt(before)}, {_fmt(after)}"


def summary_rows(doc: str) -> dict[str, list[str]]:
    """Cells of each Agreement summary table row, keyed by scenario id."""
    _, _, tail = doc.partition("## Agreement summary")
    rows: dict[str, list[str]] = {}
    for line in tail.splitlines():
        if line.startswith("## "):
            break
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells[0] not in ("id",) and not set(cells[0]) <= set("-"):
                rows[cells[0]] = cells
    return rows


def recorded_problems(doc: str, recorded: dict[str, tuple[Counts, Counts]]) -> list[str]:
    """Why `recorded` is not backed by the doc's summary table; empty if it is."""
    rows = summary_rows(doc)
    problems = []
    for sid, (before, after) in recorded.items():
        row = rows.get(sid)
        if row is None:
            problems.append(f"{sid}: no row in the Agreement summary")
            continue
        if row[-1] != AGREED:
            problems.append(f"{sid}: status is {row[-1]!r}, not {AGREED!r}")
        if _pair(before, after) not in row[2]:
            problems.append(f"{sid}: recorded pair not in its own row")
    return problems


def test_recorded_is_backed_by_the_doc() -> None:
    assert recorded_problems(DOC.read_text(), RECORDED) == []


def test_the_summary_table_was_found() -> None:
    assert set(RECORDED) <= set(summary_rows(DOC.read_text()))


_A = (Counts(0, 0, 3, 0, 0), Counts(1, 0, 2, 1, 0))
_B = (Counts(0, 0, 3, 2, 0), Counts(3, 0, 2, 3, 0))


def _doc(status_a: str = AGREED) -> str:
    return (
        "## Agreement summary\n\n"
        "| id | runs | recorded | verdict | status |\n"
        "|---|---|---|---|---|\n"
        f"| A | 3 of 3 | {_pair(*_A)} | yield | {status_a} |\n"
        f"| B | 3 of 3 | {_pair(*_B)} | yield | agreed |\n"
    )


def test_synthetic_doc_passes() -> None:
    assert recorded_problems(_doc(), {"A": _A, "B": _B}) == []


def test_recorded_scenario_with_status_disagreed_fails() -> None:
    problems = recorded_problems(_doc("disagreed"), {"A": _A})
    assert any("status" in p for p in problems)


def test_pair_matching_another_row_fails() -> None:
    problems = recorded_problems(_doc(), {"A": _B})
    assert any("own row" in p for p in problems)


@pytest.mark.parametrize("sid", list(RECORDED))
def test_real_doc_row_is_agreed(sid: str) -> None:
    assert summary_rows(DOC.read_text())[sid][-1] == AGREED
