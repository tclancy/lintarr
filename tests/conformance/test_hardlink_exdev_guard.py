"""The EXDEV pin must fail, not skip, without docker in CI (fast, no container)."""

import pytest

from tests.conformance import docker_guard


@pytest.fixture
def no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_guard, "docker_available", lambda: False)


def test_missing_docker_fails_in_ci(no_docker: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="docker is required in CI"):
        docker_guard.require_docker()


def test_missing_docker_skips_locally(no_docker: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.skip.Exception):
        docker_guard.require_docker()


def test_available_docker_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_guard, "docker_available", lambda: True)
    monkeypatch.setenv("CI", "true")
    assert docker_guard.require_docker() is None
