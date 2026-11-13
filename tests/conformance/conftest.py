"""Session-scoped real qBittorrent container for the conformance tests.

The container starts once per session; QbtHarness.reset() isolates tests.

Port rule (mandatory): publish the SAME port on both sides and set WEBUI_PORT
to it. A remapped publish makes the Host header disagree with WebUI\\Port, and
qBittorrent then answers every request 401, indistinguishable from a wrong
password; failed logins feed the 5-failure IP ban. See
docs/measurements/2026-10-02-qbittorrent-5.2.4-webui-ban.md (row A6 and the
"Publish on the port" note) and tools/probe_qbt_ban.py:17-21.
"""

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.conformance.image import QBT_IMAGE
from tests.conformance.qbt import HarnessError, QbtHarness

READY_TIMEOUT = 90.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PULL_TIMEOUT = 300.0  # the first `docker run` may pull the image
DOCKER_TIMEOUT = 30.0


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"docker {' '.join(args[:2])} timed out after {timeout:.0f}s") from exc


def _docker(*args: str, timeout: float = DOCKER_TIMEOUT) -> None:
    r = _run(list(args), timeout)
    if r.returncode != 0:
        raise HarnessError(f"docker {' '.join(args[:2])} failed: {r.stderr.strip()}")


def _wait_ready(base_url: str) -> None:
    """Unauthenticated GET; any HTTP answer (403 included) means the WebUI is up."""
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base_url}/api/v2/app/version", timeout=3)
            return
        except httpx.HTTPError:
            time.sleep(1)
    raise HarnessError(f"WebUI not up within {READY_TIMEOUT:.0f}s")


def _temp_password(container: str) -> str:
    """Parse the temporary password the way tools/probe_qbt_ban.py:temp_password does."""
    logs = _run(["logs", container], DOCKER_TIMEOUT)
    lines = (logs.stdout + logs.stderr).splitlines()
    hits = [ln.split(": ")[-1].strip() for ln in lines if "temporary password" in ln]
    if not hits:
        raise HarnessError("no temporary password in the container log")
    return hits[-1]


@pytest.fixture(scope="session")
def qbt(tmp_path_factory: pytest.TempPathFactory) -> Iterator[QbtHarness]:
    if shutil.which("docker") is None:
        raise HarnessError("docker not available")
    downloads = tmp_path_factory.mktemp("downloads")
    port = _free_port()
    container = f"lintarr-conformance-{port}"  # teardown keys on the name, not parsed output
    harness = None
    try:
        _docker(
            "run", "-d", "--rm", "--name", container,
            "-p", f"127.0.0.1:{port}:{port}",
            "-e", f"WEBUI_PORT={port}",
            "-e", f"PUID={os.getuid()}",
            "-e", f"PGID={os.getgid()}",
            "-v", f"{downloads}:/downloads",
            QBT_IMAGE,
            timeout=PULL_TIMEOUT,
        )  # fmt: skip
        base_url = f"http://127.0.0.1:{port}"
        _wait_ready(base_url)
        time.sleep(2)  # the WebUI answers a beat before auth is wired up (probe_qbt_ban.py)
        harness = QbtHarness(base_url, _temp_password(container), Path(downloads))
        harness.login()
        yield harness
    finally:
        try:
            if harness is not None:
                harness.close()
        finally:
            _run(["rm", "-f", container], DOCKER_TIMEOUT)
