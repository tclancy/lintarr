"""Session-scoped real qBittorrent container for the conformance tests.

The container starts once per session; QbtHarness.reset() isolates tests.
Lifecycle and the WebUI port rule live in tests/conformance/containers.py.
"""

from collections.abc import Iterator

import pytest

from tests.conformance import containers
from tests.conformance.qbt import QbtHarness
from tests.conformance.runner import EXC_TYPE_ATTR, RESULT_FILE, classify_report, write_result


@pytest.fixture(scope="session")
def qbt(tmp_path_factory: pytest.TempPathFactory) -> Iterator[QbtHarness]:
    containers.sweep_expired()
    name, harness = containers.start_qbt(tmp_path_factory.mktemp("downloads"))
    try:
        yield harness
    finally:
        containers.stop_qbt(name, harness)


@pytest.fixture(scope="module")
def qbt_pair(tmp_path_factory: pytest.TempPathFactory) -> Iterator[containers.Pair]:
    with containers.qbt_pair(tmp_path_factory.mktemp("pair")) as pair:
        yield pair


# --- result file (spec "Failure alerting") ----------------------------------------

_FAILURES = pytest.StashKey[list[tuple[str, str]]]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    """Classify setup, call and teardown alike, so a session-fixture HarnessError
    (a setup error) is counted."""
    report = yield
    if call.excinfo is not None:
        setattr(report, EXC_TYPE_ATTR, call.excinfo.type)
    kind = classify_report(report)
    if kind is not None:
        item.config.stash.setdefault(_FAILURES, []).append((report.nodeid, kind))
    return report


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write the result file only when a `container` item was selected (M1)."""
    if session.config.option.collectonly:
        return  # nothing ran, so there is no result to report
    items = getattr(session, "items", [])
    if not any(item.get_closest_marker("container") for item in items):
        return
    failures = session.config.stash.get(_FAILURES, [])
    write_result(session.config.rootpath / RESULT_FILE, failures, int(exitstatus))
