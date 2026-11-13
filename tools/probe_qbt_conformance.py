#!/usr/bin/env python3
"""Re-run the Task 4 conformance measurements against the pinned qBittorrent image.

Produces the raw logs behind
docs/measurements/2026-11-13-qbt-conformance-assumptions.md. A manual tool, not a
test: it asserts nothing and only records what the client reports.

Needs Docker. It starts its own throwaway container through the same helpers as
the `qbt` fixture in tests/conformance/conftest.py (same image, port rule, expiry
label and teardown), so nothing has to be brought up by hand::

    uv run python tools/probe_qbt_conformance.py item4b --reps 3
    uv run python tools/probe_qbt_conformance.py item1 item1b item2 item3a

Every state transition of the torrents an item adds is printed as one JSON line
and appended to ``<out>/probe-<item>-<UTC start>.jsonl`` (``--out`` defaults to
``.scratch``, which is git-ignored). Each line carries a UTC timestamp, the
seconds since the step began, and the torrent's raw ``torrents/info`` row.

Items (each starts from an empty client and the image's default queue prefs):

- ``item1``  ``max_ratio=0``, action stop: one complete torrent; watch 60 s.
- ``item1b`` ``max_seeding_time=0``, action stop: same.
- ``item2``  ``max_ratio=0``, action 3 (remove with files): same, plus whether
  the data file survived.
- ``item3a`` which ``slow_torrent_inactive_timer`` values setPreferences accepts.
- ``item3b`` ``max_active_torrents=2`` full of seeders, exemption on, then one
  incomplete; watch 60 s.
- ``item3c`` as 3b with the exemption off.
- ``item3d`` ``max_active_downloads=1`` held by one slow download, exemption on,
  timer 1; add a second download; watch 120 s.
- ``item3e`` as 3d with ``max_active_torrents=1`` instead (the F3 run).
- ``item3f`` as 3d with timer 0.
- ``item3g`` as 3d with the exemption off (control).
- ``item4``  ``_WEDGED`` prefs: 5 complete, settle 40 s, then 3 incomplete,
  watch 90 s; logs trace()'s prediction and the observed counts per step.
- ``item4b`` as item4 with ``max_active_uploads=-1`` (the F1 headline run).

Interrupt with Ctrl-C, not SIGKILL: teardown removes the container. A container
left behind by a hard kill carries the expiry label and is swept by the next run.
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # the harness lives under tests/, not in the package

from tests.conformance import conftest as fixture  # noqa: E402
from tests.conformance.image import QBT_IMAGE  # noqa: E402
from tests.conformance.qbt import QbtHarness  # noqa: E402
from tests.conformance.states import tally  # noqa: E402
from tests.conformance.torrents import HarnessTorrent, make_torrent  # noqa: E402
from tests.fixtures.homelab import _WEDGED  # noqa: E402
from tests.model.queue import AddComplete, AddIncomplete, QueueConfig, trace  # noqa: E402

FIELDS = (
    "hash", "name", "state", "progress", "ratio", "uploaded", "ratio_limit",
    "max_ratio", "seeding_time", "seeding_time_limit", "max_seeding_time",
    "priority", "force_start", "num_seeds", "dlspeed", "upspeed", "added_on",
    "completion_on", "amount_left",
)  # fmt: skip

QUEUE_KEYS = (
    "queueing_enabled", "max_active_downloads", "max_active_uploads",
    "max_active_torrents", "dont_count_slow_torrents", "slow_torrent_dl_rate_threshold",
    "slow_torrent_ul_rate_threshold", "slow_torrent_inactive_timer", "max_ratio_enabled",
    "max_ratio", "max_ratio_act", "max_seeding_time_enabled", "max_seeding_time",
    "max_inactive_seeding_time_enabled", "max_inactive_seeding_time",
)  # fmt: skip

POLL = 0.25


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@contextmanager
def conformance_container() -> Iterator[QbtHarness]:
    """The `qbt` fixture's lifecycle, outside pytest. Always removes the container."""
    with TemporaryDirectory(prefix="qbt-probe-") as tmp:
        downloads = Path(tmp)
        port = fixture._free_port()
        name = f"lintarr-conformance-{port}"
        started = False
        try:
            fixture._sweep_expired()
            fixture._docker(
                "run", "-d", "--rm", "--name", name,
                "--label", f"{fixture.EXPIRY_LABEL}={int(time.time()) + fixture.LIFETIME_SECONDS}",
                "-p", f"127.0.0.1:{port}:{port}", "-e", f"WEBUI_PORT={port}",
                "-e", f"PUID={os.getuid()}", "-e", f"PGID={os.getgid()}",
                "-v", f"{downloads}:/downloads", QBT_IMAGE,
                timeout=fixture.PULL_TIMEOUT,
            )  # fmt: skip
            started = True
            yield _logged_in(f"http://127.0.0.1:{port}", name, downloads)
        finally:
            if started:
                fixture._remove(name)


def _logged_in(base_url: str, container: str, downloads: Path) -> QbtHarness:
    fixture._wait_ready(base_url)
    time.sleep(2)  # as in the fixture: auth is wired up a beat after the WebUI answers
    harness = QbtHarness(base_url, fixture._temp_password(container), downloads)
    harness.login()
    return harness


class Probe:
    """One item's log file plus the reads every item needs."""

    def __init__(self, qbt: QbtHarness, item: str, out: Path) -> None:
        self.qbt = qbt
        self.item = item
        self.path = out / f"probe-{item}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.jsonl"

    def log(self, **rec: object) -> None:
        line = json.dumps({"utc": utc_now(), **rec})
        print(line, flush=True)
        with self.path.open("a") as f:
            f.write(line + "\n")

    def rows(self) -> dict[str, dict]:
        r = self.qbt._http.get("/api/v2/torrents/info")
        r.raise_for_status()
        return {row["hash"]: {k: row.get(k) for k in FIELDS} for row in r.json()}

    def prefs(self) -> dict:
        r = self.qbt._http.get("/api/v2/app/preferences")
        r.raise_for_status()
        return r.json()

    def fresh(self, defaults: dict, prefs: dict) -> None:
        self.qbt.reset()
        self.qbt.set_prefs(defaults | prefs)
        readback = self.prefs()
        self.log(prefs_sent=prefs, prefs_readback={k: readback.get(k) for k in QUEUE_KEYS})

    def add(self, t: HarnessTorrent, *, complete: bool) -> None:
        self.qbt.add(t, complete=complete)
        self.log(added=t.info_hash, name=t.name, complete=complete)

    def watch(
        self, hashes: list[str], seconds: float, stop: Callable[[dict], bool] | None = None
    ) -> tuple[float | None, dict[str, dict]]:
        """Log each state change (or vanish) of `hashes`; return (stop time, last rows)."""
        t0 = time.monotonic()
        last: dict[str, str] = {}
        rows: dict[str, dict] = {}
        while time.monotonic() - t0 < seconds:
            rows = self.rows()
            for h in hashes:
                state = rows.get(h, {}).get("state", "<absent>")
                if last.get(h) != state:
                    self.log(
                        t=round(time.monotonic() - t0, 2), hash=h, state=state, row=rows.get(h)
                    )
                    last[h] = state
            if stop is not None and stop(rows):
                return time.monotonic() - t0, rows
            time.sleep(POLL)
        return None, rows


def _state_is(h: str, want: str) -> Callable[[dict], bool]:
    return lambda rows: rows.get(h, {}).get("state") == want


def _share_limit(p: Probe, d: dict, prefs: dict, tag: str) -> None:
    p.fresh(d, prefs)
    t = make_torrent(tag)
    p.add(t, complete=True)
    p.watch([t.info_hash], 60)
    p.log(data_file_exists=(p.qbt._downloads / t.name).exists())


def item1(p: Probe, d: dict, rep: int) -> None:
    _share_limit(p, d, {"max_ratio_enabled": True, "max_ratio": 0, "max_ratio_act": 0},
                 f"probe1-r{rep}")  # fmt: skip


def item1b(p: Probe, d: dict, rep: int) -> None:
    prefs = {"max_seeding_time_enabled": True, "max_seeding_time": 0, "max_ratio_act": 0}
    _share_limit(p, d, prefs, f"probe1b-r{rep}")


def item2(p: Probe, d: dict, rep: int) -> None:
    _share_limit(p, d, {"max_ratio_enabled": True, "max_ratio": 0, "max_ratio_act": 3},
                 f"probe2-r{rep}")  # fmt: skip


def item3a(p: Probe, d: dict, rep: int) -> None:
    for value in (-5, -1, 0, 1, 2, 5, 60):
        p.qbt.reset()
        p.qbt.set_prefs(d | {"slow_torrent_inactive_timer": value})
        p.log(sent=value, readback=p.prefs()["slow_torrent_inactive_timer"])


def _seeders_then_download(p: Probe, d: dict, dont_count: bool, tag: str) -> None:
    p.fresh(d, {"max_active_downloads": -1, "max_active_uploads": -1,
                "max_active_torrents": 2, "dont_count_slow_torrents": dont_count,
                "slow_torrent_inactive_timer": 1})  # fmt: skip
    seeds = [make_torrent(f"{tag}-seed{i}") for i in range(2)]
    for s in seeds:
        p.add(s, complete=True)
    hs = [s.info_hash for s in seeds]
    p.watch(hs, 10, stop=lambda rows: all(_state_is(h, "stalledUP")(rows) for h in hs))
    inc = make_torrent(f"{tag}-inc")
    p.add(inc, complete=False)
    _, rows = p.watch([inc.info_hash, *hs], 60)
    p.log(final=list(rows.values()))


def item3b(p: Probe, d: dict, rep: int) -> None:
    _seeders_then_download(p, d, True, f"probe3b-r{rep}")


def item3c(p: Probe, d: dict, rep: int) -> None:
    _seeders_then_download(p, d, False, f"probe3c-r{rep}")


def _second_download(p: Probe, d: dict, limits: dict, dont_count: bool, timer: int,
                     tag: str) -> None:  # fmt: skip
    p.fresh(d, limits | {"max_active_uploads": -1, "dont_count_slow_torrents": dont_count,
                         "slow_torrent_inactive_timer": timer})  # fmt: skip
    a, b = make_torrent(f"{tag}-a"), make_torrent(f"{tag}-b")
    p.add(a, complete=False)
    p.watch([a.info_hash], 10, stop=_state_is(a.info_hash, "stalledDL"))
    p.add(b, complete=False)
    _, rows = p.watch([a.info_hash, b.info_hash], 120)
    p.log(final=list(rows.values()))


DL1 = {"max_active_downloads": 1, "max_active_torrents": -1}
TOT1 = {"max_active_downloads": -1, "max_active_torrents": 1}


def item3d(p: Probe, d: dict, rep: int) -> None:
    _second_download(p, d, DL1, True, 1, f"probe3d-r{rep}")


def item3e(p: Probe, d: dict, rep: int) -> None:
    _second_download(p, d, TOT1, True, 1, f"probe3e-r{rep}")


def item3f(p: Probe, d: dict, rep: int) -> None:
    _second_download(p, d, DL1, True, 0, f"probe3f-r{rep}")


def item3g(p: Probe, d: dict, rep: int) -> None:
    _second_download(p, d, DL1, False, 1, f"probe3g-r{rep}")


def _wedged_prefs(**over: object) -> dict:
    """`_WEDGED` minus `categories` (not a preference key), timer 1 per the spec."""
    return (
        {k: v for k, v in _WEDGED.items() if k != "categories"}
        | {"slow_torrent_inactive_timer": 1}
        | over
    )


def _queue_config(p: dict) -> QueueConfig:
    return QueueConfig(
        queueing_enabled=p["queueing_enabled"],
        max_active_downloads=p["max_active_downloads"],
        max_active_uploads=p["max_active_uploads"],
        max_active_torrents=p["max_active_torrents"],
        dont_count_slow_torrents=p["dont_count_slow_torrents"],
        share_limit_enabled=p["max_ratio_enabled"] or p["max_seeding_time_enabled"],
    )


def _five_seeders_then_three_downloads(p: Probe, d: dict, prefs: dict, tag: str) -> None:
    p.fresh(d, prefs)
    predicted = trace(_queue_config(prefs), [AddComplete(5), AddIncomplete(3)])
    p.log(predicted=[repr(c) for c in predicted])
    seeds = [make_torrent(f"{tag}-seed{i}") for i in range(5)]
    incs = [make_torrent(f"{tag}-inc{i}") for i in range(3)]
    hs = [s.info_hash for s in seeds]
    for s in seeds:
        p.add(s, complete=True)
    p.watch(hs, 40)
    p.log(step=1, observed=repr(tally(p.qbt.states(), set(hs))), raw=list(p.rows().values()))
    for t in incs:
        p.add(t, complete=False)
    hi = hs + [t.info_hash for t in incs]
    p.watch(hi, 90)
    p.log(step=2, observed=repr(tally(p.qbt.states(), set(hi))), raw=list(p.rows().values()))


def item4(p: Probe, d: dict, rep: int) -> None:
    _five_seeders_then_three_downloads(p, d, _wedged_prefs(), f"probe4-r{rep}")


def item4b(p: Probe, d: dict, rep: int) -> None:
    prefs = _wedged_prefs(max_active_uploads=-1)
    _five_seeders_then_three_downloads(p, d, prefs, f"probe4b-r{rep}")


ITEMS: dict[str, Callable[[Probe, dict, int], None]] = {
    f.__name__: f
    for f in (item1, item1b, item2, item3a, item3b, item3c, item3d, item3e, item3f, item3g,
              item4, item4b)
}  # fmt: skip


def _defaults(qbt: QbtHarness) -> dict:
    """The image's own queue prefs, read once on the fresh container."""
    r = qbt._http.get("/api/v2/app/preferences")
    r.raise_for_status()
    return {k: v for k, v in r.json().items() if k in QUEUE_KEYS}


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("items", nargs="+", choices=sorted(ITEMS))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--out", type=Path, default=REPO / ".scratch")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    with conformance_container() as qbt:
        defaults = _defaults(qbt)
        for name in args.items:
            for rep in range(1, args.reps + 1):
                probe = Probe(qbt, name, args.out)
                probe.log(item=name, rep=rep, version=qbt.version(), defaults=defaults)
                ITEMS[name](probe, defaults, rep)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
