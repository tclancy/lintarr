"""Docker-availability guard for container tests that need only plain docker."""

import os
import shutil
import subprocess

import pytest


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def require_docker() -> None:
    """Skip locally without docker; fail in CI, where a skip would hide a dead pin."""
    if docker_available():
        return
    if os.environ.get("CI"):
        pytest.fail("docker is required in CI for the EXDEV axiom")
    pytest.skip("docker is not available")
