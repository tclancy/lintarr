#!/usr/bin/env python3
"""Measure whether actively uploading seeders hold qBittorrent's max_active_torrents slots.

Produces the raw log behind docs/measurements/2026-11-16-qbt-uploading-seeders.md.
A manual tool, not a test: it records and summarises, it pins nothing.

Needs Docker. It starts a seeder/leecher pair through `qbt_pair()` (the same
helpers as the `qbt_pair` fixture: pinned image, port rule, expiry label,
teardown) and runs each scenario of `UPLOAD_SCENARIOS` through `observe` and
`judge`::

    uv run python tools/probe_qbt_uploading.py --reps 3 > uploading.jsonl
    uv run python tools/probe_qbt_uploading.py --reps 1 --only W-off > dry.jsonl

stdout gets JSON lines: per run a `run` line, every bring-up and window poll
(`phase`, `t`, raw rows), the preferences read back from both sides, and a
`result` line. stderr gets one summary line per run: the Outcome and the time
from the event to the counts settling, or the error.

Interrupt with Ctrl-C, not SIGKILL: teardown removes both containers and the
network. Anything left by a hard kill carries the expiry label and is swept by
the next run.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # the harness lives under tests/, not in the package

from tests.conformance.containers import Pair, qbt_pair  # noqa: E402
from tests.conformance.image import QBT_IMAGE  # noqa: E402
from tests.conformance.qbt import QbtHarness  # noqa: E402
from tests.conformance.scenarios import PREF_KEYS  # noqa: E402
from tests.conformance.states import UnmappedState, tally  # noqa: E402
from tests.conformance.uploading import (  # noqa: E402
    PEER_PREFS,
    UPLOAD_SCENARIOS,
    Poll,
    Recording,
    UploadScenario,
    judge,
    observe,
)

READBACK_KEYS = sorted(
    PREF_KEYS
    | PEER_PREFS.keys()
    | {"slow_torrent_ul_rate_threshold", "slow_torrent_dl_rate_threshold"}
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def emit(**rec: object) -> None:
    print(json.dumps(rec), flush=True)


def _counts_or_states(p: Poll, hashes: set[str]) -> object:
    """The poll's tally, or its raw states while a transient (e.g. checkingResumeData) is in it."""
    states = {r.hash: r.state for r in p.rows}
    try:
        return tally(states, hashes)
    except UnmappedState:
        return tuple(sorted(states.items()))


def settle_t(polls: Sequence[Poll], hashes: set[str]) -> float | None:
    """Window time of the first poll from which every later tally equals the final one."""
    if not polls:
        return None
    tallies = [_counts_or_states(p, hashes) for p in polls]
    i = len(tallies) - 1
    while i > 0 and tallies[i - 1] == tallies[-1]:
        i -= 1
    return polls[i].t


def _ours(sc: UploadScenario, polls: Sequence[Poll]) -> set[str]:
    prefix = f"{sc.id}-r"
    return {r.hash for p in polls for r in p.rows if r.name.startswith(prefix)}


def readback(h: QbtHarness) -> dict[str, object]:
    got = h.preferences()
    return {k: got.get(k, "<absent>") for k in READBACK_KEYS}


def emit_recording(sc: UploadScenario, rep: int, rec: Recording) -> None:
    for phase, polls in (("bringup", rec.bringup), ("window", rec.window)):
        for p in polls:
            rows = [asdict(r) for r in p.rows]
            emit(kind="poll", scenario=sc.id, rep=rep, phase=phase, t=p.t, rows=rows)


def summarise(sc: UploadScenario, rep: int, rec: Recording) -> dict[str, object]:
    """Judge the run; return the result fields, the error included, never raising."""
    try:
        o = judge(sc, rec)
    except Exception as exc:  # summarise every run, whatever judge says about it
        return {"error": f"{type(exc).__name__}: {exc}", "recorded_error": rec.error}
    return {
        "before": list(asdict(o.before).values()),
        "after": list(asdict(o.after).values()),
        "active_at_bringup": o.active_at_bringup,
        "verdict": o.verdict,
        "settle_t": settle_t(rec.window, _ours(sc, rec.window)),
        "bringup_s": rec.bringup[-1].t,
    }


def run_one(pair: Pair, sc: UploadScenario, rep: int) -> None:
    emit(kind="run", utc=utc_now(), scenario=sc.id, rep=rep, cfg=asdict(sc.cfg),
         n_seeders=sc.n_seeders, n_downloads=sc.n_downloads, leecher=sc.leecher)  # fmt: skip
    rec = observe(pair, sc, rep=rep)
    emit_recording(sc, rep, rec)
    for side, h in (("seeder", pair.seeder), ("leecher", pair.leecher)):
        emit(kind="prefs", scenario=sc.id, rep=rep, side=side, prefs=readback(h))
    result = summarise(sc, rep, rec)
    emit(kind="result", utc=utc_now(), scenario=sc.id, rep=rep, **result)
    print(f"{utc_now()} {sc.id} r{rep}: {result}", file=sys.stderr, flush=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument(
        "--only", action="append", choices=[sc.id for sc in UPLOAD_SCENARIOS],
        help="run only this scenario (repeatable); default all",
    )  # fmt: skip
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    chosen = [sc for sc in UPLOAD_SCENARIOS if not args.only or sc.id in args.only]
    with TemporaryDirectory(prefix="qbt-uploading-") as tmp, qbt_pair(Path(tmp)) as pair:
        emit(kind="session", utc=utc_now(), image=QBT_IMAGE, seeder=pair.seeder.version(),
             leecher=pair.leecher.version(), seeder_ip=pair.seeder_ip)  # fmt: skip
        for sc in chosen:
            for rep in range(1, args.reps + 1):
                run_one(pair, sc, rep)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
