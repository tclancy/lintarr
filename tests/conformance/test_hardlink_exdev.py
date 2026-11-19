"""Nightly pin for the axiom the ``hardlink-futility`` FAIL rests on.

``link(2)`` across two bind mounts fails with ``EXDEV`` even when both are
directories of one host filesystem; within one bind mount it succeeds.
"""

import os
import subprocess
from pathlib import Path

import pytest

from tests.conformance.docker_guard import require_docker

pytestmark = pytest.mark.container

# Pinned by tag, like image.py's QBT_IMAGE.
ALPINE_IMAGE = "alpine:3.20"


@pytest.fixture(autouse=True)
def _need_docker() -> None:
    require_docker()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "f").write_text("x")
    return tmp_path


def _run(mounts: list[str], script: str) -> subprocess.CompletedProcess[str]:
    argv = ["docker", "run", "--rm"]
    for m in mounts:
        argv += ["-v", m]
    argv += [ALPINE_IMAGE, "sh", "-c", script]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120)


def test_link_across_two_bind_mounts_is_exdev(tree: Path) -> None:
    result = _run([f"{tree / 'a'}:/a", f"{tree / 'b'}:/b"], "ln /a/f /b/f")
    assert result.returncode != 0, result
    assert "cross-device link" in result.stderr.lower(), result.stderr
    assert not (tree / "b" / "f").exists()


def test_link_within_one_bind_mount_succeeds(tree: Path) -> None:
    result = _run([f"{tree}:/x"], "ln /x/a/f /x/b/f && stat -c %h /x/a/f")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "2"
    assert os.stat(tree / "a" / "f").st_nlink == 2
