"""Shared checks: invariants over client-neutral facts, run on every backend.

``run`` reads ``SHARED_CHECKS`` at call time, so a test can patch it.
``SHARED_THEREFORE`` holds each shared conflict's "Therefore" line; ``cli``
consults it before any backend's own table, because a shared finding carries
the label of whichever backend it ran against.
"""

from lintarr.checks import hardlink_futility
from lintarr.clients.base import ArrDependency, Check

SHARED_CHECKS: tuple[Check, ...] = (hardlink_futility.check,)

SHARED_THEREFORE: dict[tuple[str, str], str] = {
    (hardlink_futility.INVARIANT_ID, hardlink_futility.CROSS_MOUNT): (
        "every import from this client into that library is a full copy, not a hardlink, "
        "so each one doubles its disk use. Put downloads and library under one volume in "
        "the arr's container (TRaSH's `/data` layout), or turn hardlinks off to make the "
        "copying deliberate."
    ),
}

#: Invariant id -> what its failed-arr SKIP says (see ``run._resolve_failed_arr``).
#: The detail is each check's own sentence, written here beside the premise it
#: carries; no backend may reuse one of these ids.
SHARED_ARR_DEPENDENT: dict[str, ArrDependency] = {
    hardlink_futility.INVARIANT_ID: ArrDependency(
        hardlink_futility.PREMISE,
        "an arr could not be read, so its imports could not be checked",
    )
}

SHARED_INVARIANTS: frozenset[str] = frozenset(key[0] for key in SHARED_THEREFORE)
