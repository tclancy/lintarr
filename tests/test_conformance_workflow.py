"""The nightly conformance workflow must be able to fail loudly and say so.

Same YAML-reading pattern as tests/test_ci_workflow.py. The alert step is
`if: failure()`, so a clean run can never exercise it; these guards pin its
wiring instead.
"""

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW = _ROOT / ".github" / "workflows" / "conformance.yml"
_TESTS_WORKFLOW = _ROOT / ".github" / "workflows" / "tests.yml"
_SHA_PIN = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def _load(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    assert isinstance(doc, dict) and doc.get("jobs"), f"{path} parsed to {doc!r}"
    return doc


def _triggers(doc: dict) -> dict:
    # PyYAML (YAML 1.1) reads bare `on` as True.
    for key in (True, "on"):
        if key in doc:
            return doc[key]
    raise AssertionError(f"no `on:` block; top-level keys were {list(doc)}")


def _steps(doc: dict) -> list[dict]:
    return [step for job in doc["jobs"].values() for step in job["steps"]]


def _uses(doc: dict) -> list[str]:
    return [step["uses"] for step in _steps(doc) if "uses" in step]


def test_nightly_cron_and_dispatch_force_failure_input():
    on = _triggers(_load(_WORKFLOW))
    assert on["schedule"] == [{"cron": "41 6 * * *"}]
    force = on["workflow_dispatch"]["inputs"]["force_failure"]
    assert force["type"] == "boolean"
    assert force["default"] is False


def test_permissions_are_read_only_and_there_is_one_job():
    doc = _load(_WORKFLOW)
    assert doc["permissions"] == {"contents": "read"}
    assert len(doc["jobs"]) == 1
    (job,) = doc["jobs"].values()
    assert isinstance(job["timeout-minutes"], int)
    assert 0 < job["timeout-minutes"] <= 45


def test_the_pytest_step_runs_the_container_marker_with_the_drill_flag():
    steps = [s for s in _steps(_load(_WORKFLOW)) if "pytest" in s.get("run", "")]
    assert len(steps) == 1, steps
    (step,) = steps
    assert re.search(r"\buv run --frozen pytest\b.*-m container\b", step["run"])
    assert step["env"]["CONFORMANCE_FORCE_FAILURE"] == "${{ inputs.force_failure }}"


def test_the_alert_step_runs_on_failure_with_the_secret_and_run_url():
    steps = [s for s in _steps(_load(_WORKFLOW)) if "conformance_alert.sh" in s.get("run", "")]
    assert len(steps) == 1, steps
    (step,) = steps
    assert step["if"] == "failure()"
    assert step["env"]["NTFY_TOKEN"] == "${{ secrets.NTFY_TOKEN }}"
    assert step["env"]["RUN_URL"] == (
        "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
    )
    assert not step.get("continue-on-error")


def test_every_action_is_pinned_to_a_sha():
    uses = _uses(_load(_WORKFLOW))
    assert len(uses) >= 3, uses  # reachability: an empty list passes the loop trivially
    assert not [u for u in uses if not _SHA_PIN.match(u)], uses


def test_action_shas_match_tests_yml():
    ours = _uses(_load(_WORKFLOW))
    theirs = _uses(_load(_TESTS_WORKFLOW))
    assert ours
    unmatched = [u for u in ours if u not in theirs]
    assert not unmatched, f"actions not pinned identically to tests.yml: {unmatched}"
