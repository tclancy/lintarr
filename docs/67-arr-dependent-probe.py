"""Probe both tables that feed the failed-arr SKIP. Runs on this branch and on its base.

Section A registers a second shared arr-dependent check -- `ratio-sum`, which
would be about seeding ratios, not imports -- and prints the SKIP the run gives
it. Section B registers a backend whose `arr_dependent` reuses
`hardlink-futility`'s id and prints what the merge did with the clash.

The mapping value changed shape in this fix, so the probe builds whichever one
the checkout it is running in understands.
"""

import dataclasses
import os
import sys

sys.path.insert(0, os.getcwd())

from lintarr import checks  # noqa: E402
from lintarr.clients import BACKENDS  # noqa: E402
from lintarr.models import ErrorRow, StackFacts  # noqa: E402
from lintarr.outcomes import Finding, Outcome  # noqa: E402
from lintarr.run import run_checks  # noqa: E402
from tests.fixtures.clients import as_client  # noqa: E402
from tests.fixtures.homelab_paths import homelab_arr, homelab_qbt  # noqa: E402

RATIO_DETAIL = "an arr could not be read, so its seeding ratios could not be checked"
DECLARED = frozenset({"qbittorrent", "sonarr"})
FAILED_ARR = ErrorRow("arr[sonarr]", "unreachable", "connection refused")
FACTS = StackFacts(clients=(as_client(homelab_qbt()),), arrs=(homelab_arr(),), errors=(FAILED_ARR,))


def entry(premise, detail):
    """The mapping value this checkout takes: (premise, detail) here, premise alone before."""
    try:
        from lintarr.clients.base import ArrDependency
    except ImportError:
        return premise
    return ArrDependency(premise, detail)


def ratio_sum(inst, arrs):
    return Finding(
        invariant="ratio-sum", instance=f"{inst.kind}[{inst.name}]", outcome=Outcome.PASS
    )


def show(finding):
    premise = finding.premises[0].label if finding.premises else "-"
    print(f"  {finding.outcome:<5} {finding.invariant:<21} {premise:<38} {finding.detail}")


print("A. a second shared arr-dependent check, beside the one that shipped")
checks.SHARED_CHECKS = (*checks.SHARED_CHECKS, ratio_sum)
checks.SHARED_ARR_DEPENDENT["ratio-sum"] = entry("arr.ratio_sum_is_reachable", RATIO_DETAIL)
for finding in run_checks(FACTS, declared=DECLARED):
    if finding.invariant in ("hardlink-futility", "ratio-sum"):
        show(finding)
print(f"  ratio-sum registered with: {RATIO_DETAIL!r}")
del checks.SHARED_ARR_DEPENDENT["ratio-sum"]
checks.SHARED_CHECKS = checks.SHARED_CHECKS[:-1]

print()
print("B. a backend whose arr_dependent reuses the shared id 'hardlink-futility'")
own = entry("qbt.backend_premise", "an arr could not be read, so the backend's own text")
BACKENDS["qbittorrent"] = dataclasses.replace(
    BACKENDS["qbittorrent"], arr_dependent={"hardlink-futility": own}
)
try:
    for finding in run_checks(FACTS, declared=DECLARED):
        if finding.invariant == "hardlink-futility":
            show(finding)
    print("  no error: the clash was merged silently")
except ValueError as exc:
    print(f"  ValueError: {exc}")
