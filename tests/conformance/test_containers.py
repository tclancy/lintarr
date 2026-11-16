"""Container lifecycle helpers, with `docker` faked at containers._run."""

import subprocess
import time
from pathlib import Path

import pytest

from tests.conformance import containers
from tests.conformance.qbt import HarnessError


def _done(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_sweep_removes_expired_networks(monkeypatch):
    now = int(time.time())
    calls: list[list[str]] = []

    def fake_run(args, timeout):
        calls.append(list(args))
        if args[0] == "ps":
            return _done(f"c-old {now - 10}\nc-live {now + 1000}\n")
        if args[:2] == ["network", "ls"]:
            return _done(f"n-old {now - 10}\nn-live {now + 1000}\n")
        return _done()

    monkeypatch.setattr(containers, "_run", fake_run)
    containers.sweep_expired()
    assert ["network", "rm", "n-old"] in calls
    assert ["network", "rm", "n-live"] not in calls
    assert ["rm", "-f", "c-old"] in calls
    assert ["rm", "-f", "c-live"] not in calls
    last_container_rm = max(i for i, c in enumerate(calls) if c[0] == "rm")
    assert last_container_rm < calls.index(["network", "rm", "n-old"])


def test_container_ip_empty_is_harness_error(monkeypatch):
    monkeypatch.setattr(containers, "_run", lambda args, timeout: _done("\n"))
    with pytest.raises(HarnessError):
        containers.container_ip("c", "net")


def test_container_ip_returns_the_address(monkeypatch):
    monkeypatch.setattr(containers, "_run", lambda args, timeout: _done("172.18.0.2\n"))
    assert containers.container_ip("c", "net") == "172.18.0.2"


def test_start_qbt_joins_the_network_when_given(monkeypatch, tmp_path):
    runs: list[list[str]] = []

    def fake_run(args, timeout):
        runs.append(list(args))
        return _done()

    monkeypatch.setattr(containers, "_run", fake_run)
    monkeypatch.setattr(containers.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(containers, "_wait_ready", lambda url: None)
    monkeypatch.setattr(containers.time, "sleep", lambda s: None)
    monkeypatch.setattr(containers, "_temp_password", lambda c: "pw")

    class FakeHarness:
        def __init__(self, *a, **k):
            pass

        def login(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(containers, "QbtHarness", FakeHarness)
    containers.start_qbt(tmp_path, network="lintarr-conformance-net-1")
    run = next(a for a in runs if a[0] == "run")
    assert run[run.index("--network") + 1] == "lintarr-conformance-net-1"


def test_pair_uses_separate_download_dirs(monkeypatch, tmp_path):
    seen: list[Path] = []

    def fake_start(downloads, *, network=None):
        seen.append(downloads)
        return f"c{len(seen)}", object()

    monkeypatch.setattr(containers, "start_qbt", fake_start)
    monkeypatch.setattr(containers, "stop_qbt", lambda name, h: None)
    monkeypatch.setattr(containers, "container_ip", lambda n, net: "172.18.0.2")
    monkeypatch.setattr(containers, "_run", lambda args, timeout: _done())
    with containers.qbt_pair(tmp_path) as pair:
        assert pair.seeder_ip == "172.18.0.2"
    assert len(seen) == 2
    assert seen[0] != seen[1]
