"""One mutation round for lintarr#67, for ``docs/67-failed-arr-skip-wording.md``.

Each mutant breaks one half of what the fix is supposed to guarantee -- the
wording, the premise, the clash guard -- and the round is only evidence if
every one of them turns the suite red. Run as::

    python3 docs/67-mutation-round.py <repo root>

It edits files in ``src/`` and restores them with ``git checkout --`` in a
``finally``, re-running the control after each restore, and refuses to start
when ``src/`` or ``tests/`` has uncommitted changes. It is demo scaffolding;
nothing in ``src/`` or ``tests/`` imports it.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(sys.argv[1])
TESTS = [
    "tests/test_run.py",
    "tests/clients/test_registry.py",
    "tests/clients/test_orphan_inevitability.py",
    "tests/checks/test_hardlink_futility.py",
]

MUTANTS = [
    (
        "SKIP detail hardcoded back to the shared wording",
        "src/lintarr/run.py",
        "detail=dependency.detail,",
        'detail="an arr could not be read, so its imports could not be checked",',
    ),
    (
        "SKIP premise taken from the detail instead",
        "src/lintarr/run.py",
        "premises=(Premise(dependency.premise, None),),",
        "premises=(Premise(dependency.detail, None),),",
    ),
    (
        "clash merged silently again (raise removed)",
        "src/lintarr/run.py",
        """    clash = sorted(own.keys() & checks.SHARED_ARR_DEPENDENT.keys())
    if clash:
        raise ValueError(
            f"backend {instance.kind!r} reuses shared invariant id(s) in arr_dependent: "
            f"{', '.join(clash)}"
        )
""",
        "",
    ),
    (
        "hardlink-futility's sentence reworded",
        "src/lintarr/checks/__init__.py",
        '"an arr could not be read, so its imports could not be checked"',
        '"an arr could not be read, so its downloads could not be checked"',
    ),
    (
        "orphan-inevitability's sentence reworded",
        "src/lintarr/clients/qbittorrent/__init__.py",
        '"an arr could not be read, so its downloads could not be checked"',
        '"an arr could not be read, so its imports could not be checked"',
    ),
]


def run_tests() -> tuple[int, str]:
    r = subprocess.run(
        ["uv", "run", "--quiet", "--frozen", "pytest", "-q", *TESTS],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    tail = [ln for ln in r.stdout.strip().splitlines() if ln.strip()][-1]
    # The run time is the one part of pytest's summary that differs every run,
    # and this round's output is captured into a doc that `showboat verify`
    # diffs. Drop it.
    return r.returncode, re.sub(r" in [0-9.]+s$", "", tail)


def restore(path: str) -> None:
    subprocess.run(["git", "checkout", "--", path], cwd=REPO, check=True)


def main() -> int:
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", "src", "tests"],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout
    if dirty.strip():
        print("refusing to mutate a dirty tree:\n" + dirty)
        return 2

    code, tail = run_tests()
    print(f"{'CONTROL (no mutation)':<48} {'GREEN' if code == 0 else 'RED':<6} {tail}")
    if code != 0:
        return 2

    survived = []
    for name, rel, old, new in MUTANTS:
        path = REPO / rel
        src = path.read_text()
        assert src.count(old) == 1, f"{name}: {src.count(old)} matches in {rel}"
        path.write_text(src.replace(old, new))
        try:
            code, tail = run_tests()
        finally:
            restore(rel)
        verdict = "KILLED" if code != 0 else "SURVIVED"
        if code == 0:
            survived.append(name)
        print(f"{name:<48} {verdict:<6} {tail}")
        control, _ = run_tests()
        assert control == 0, "control went red after restoring; aborting"

    print()
    print("survivors:", ", ".join(survived) if survived else "none")
    return 1 if survived else 0


sys.exit(main())
