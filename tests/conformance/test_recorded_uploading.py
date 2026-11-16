"""RECORDED is anchored to the measurement doc (no Docker needed)."""

from pathlib import Path

import pytest

from tests.conformance.recorded_uploading import RECORDED
from tests.conformance.uploading import UPLOAD_SCENARIOS

DOC = Path(__file__).parents[2] / "docs/measurements/2026-11-16-qbt-uploading-seeders.md"


def _fmt(c) -> str:
    return f"`({c.downloading},{c.queued},{c.seeding},{c.seeding_queued},{c.left})`"


def _unresolved_ids(doc: str) -> set[str]:
    """Scenario ids on any line of the doc that says 'unresolved'."""
    ids = {sc.id for sc in UPLOAD_SCENARIOS}
    return {
        i for line in doc.splitlines() if "unresolved" in line.lower() for i in ids if i in line
    }


def test_doc_is_present() -> None:
    assert DOC.is_file()


@pytest.mark.parametrize("scenario_id", list(RECORDED))
def test_recorded_cites_the_doc(scenario_id: str) -> None:
    doc = DOC.read_text()
    before, after = RECORDED[scenario_id]
    assert scenario_id in doc
    assert f"{_fmt(before)} | {_fmt(after)}" in doc


def test_no_unresolved_scenario_is_recorded() -> None:
    assert not (_unresolved_ids(DOC.read_text()) & set(RECORDED))


def test_every_scenario_is_recorded_or_unresolved() -> None:
    missing = {sc.id for sc in UPLOAD_SCENARIOS} - set(RECORDED) - _unresolved_ids(DOC.read_text())
    assert not missing
