"""Container lifecycle for the conformance tests: one qBittorrent, or a seeder/leecher pair.

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
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

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


EXPIRY_LABEL = "lintarr-conformance.expires"
LIFETIME_SECONDS = 2 * 60 * 60  # comfortably above any session's length


def _expired(lines: list[str], now: float) -> list[str]:
    """Container IDs from `<id> <expiry epoch>` lines whose expiry has passed.

    Malformed lines are ignored: a container we cannot date is never removed.
    """
    ids = []
    for line in lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            if float(parts[1]) <= now:
                ids.append(parts[0])
        except ValueError:
            continue
    return ids


def _warn_rm_failed(target: str, detail: str) -> None:
    warnings.warn(f"docker rm -f {target} failed: {detail}", RuntimeWarning, stacklevel=3)


def _expired_ids(listing: str) -> list[str]:
    return _expired(listing.splitlines(), time.time())


def sweep_expired() -> None:
    """Remove containers, then networks, whose expiry label has passed (a crashed
    session, or one leaked by a timed-out `docker run`). Resources of a concurrent
    live run carry a future expiry and are never touched. Never raises.
    """
    fmt = '{{.ID}} {{.Label "' + EXPIRY_LABEL + '"}}'
    filt = f"label={EXPIRY_LABEL}"
    for listing, remove in (
        (["ps", "-a"], _remove),
        (["network", "ls"], _remove_network),
    ):
        try:
            r = _run([*listing, "--filter", filt, "--format", fmt], DOCKER_TIMEOUT)
        except HarnessError as exc:
            warnings.warn(
                f"docker {listing[0]} listing failed: {exc}", RuntimeWarning, stacklevel=2
            )
            continue
        for rid in _expired_ids(r.stdout):
            remove(rid)


def _remove_network(network: str) -> None:
    """Remove a network; never raises, so it cannot mask an in-flight exception."""
    try:
        r = _run(["network", "rm", network], DOCKER_TIMEOUT)
    except HarnessError as exc:
        warnings.warn(f"docker network rm {network} failed: {exc}", RuntimeWarning, stacklevel=2)
        return
    if r.returncode != 0:
        warnings.warn(
            f"docker network rm {network} failed: {r.stderr.strip()}", RuntimeWarning, stacklevel=2
        )


def _remove(container: str) -> None:
    """Remove a container; never raises, so it cannot mask an in-flight exception."""
    try:
        r = _run(["rm", "-f", container], DOCKER_TIMEOUT)
    except HarnessError as exc:
        _warn_rm_failed(container, str(exc))
        return
    if r.returncode != 0:
        _warn_rm_failed(container, r.stderr.strip())


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


@dataclass(frozen=True)
class Pair:
    """A seeder and a leecher on one private network; the leecher dials seeder_ip."""

    seeder: QbtHarness
    leecher: QbtHarness
    seeder_ip: str


def start_qbt(downloads: Path, *, network: str | None = None) -> tuple[str, QbtHarness]:
    """Start a qBittorrent container and return its name and a logged-in harness.

    On failure after the container started, it is removed before the error propagates.
    """
    if shutil.which("docker") is None:
        raise HarnessError("docker not available")
    port = _free_port()
    container = f"lintarr-conformance-{port}"  # teardown keys on the name, not parsed output
    net_args = ["--network", network] if network else []
    harness = None
    try:
        _docker(
            "run", "-d", "--rm", "--name", container,
            "--label", f"{EXPIRY_LABEL}={int(time.time()) + LIFETIME_SECONDS}",
            *net_args,
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
    except BaseException:
        stop_qbt(container, harness)
        raise
    return container, harness


def stop_qbt(name: str, harness: QbtHarness | None) -> None:
    try:
        if harness is not None:
            harness.close()
    finally:
        _remove(name)


def container_ip(name: str, network: str) -> str:
    fmt = '{{(index .NetworkSettings.Networks "' + network + '").IPAddress}}'
    r = _run(["inspect", "-f", fmt, name], DOCKER_TIMEOUT)
    ip = r.stdout.strip()
    if r.returncode != 0 or not ip:
        raise HarnessError(f"no IP for {name} on {network}: {r.stderr.strip()}")
    return ip


def _stop_quietly(name: str, harness: QbtHarness) -> None:
    """stop_qbt that only warns, so one failed teardown cannot skip the next or mask an error."""
    try:
        stop_qbt(name, harness)
    except Exception as exc:
        warnings.warn(f"stopping {name} failed: {exc}", RuntimeWarning, stacklevel=2)


@contextmanager
def qbt_pair(tmp: Path) -> Iterator[Pair]:
    """A seeder and a leecher, each with its own downloads dir, on a private network.

    The leecher reaches the seeder's BitTorrent port directly inside the network;
    6881 is not published.
    """
    network = f"lintarr-conformance-net-{_free_port()}"
    created = False
    seeder: tuple[str, QbtHarness] | None = None
    leecher: tuple[str, QbtHarness] | None = None
    try:
        sweep_expired()
        _docker(
            "network", "create",
            "--label", f"{EXPIRY_LABEL}={int(time.time()) + LIFETIME_SECONDS}",
            network,
        )  # fmt: skip
        created = True
        for sub in ("seeder", "leecher"):
            (tmp / sub).mkdir(parents=True, exist_ok=True)
        seeder = start_qbt(tmp / "seeder", network=network)
        leecher = start_qbt(tmp / "leecher", network=network)
        yield Pair(seeder[1], leecher[1], container_ip(seeder[0], network))
    finally:
        for started in (leecher, seeder):
            if started is not None:
                _stop_quietly(*started)
        if created:
            _remove_network(network)
