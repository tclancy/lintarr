"""Shared checks: invariants over client-neutral facts, run on every backend.

None yet. ``run`` reads ``SHARED_CHECKS`` at call time, so a test can patch it.
"""

from lintarr.clients.base import Check

SHARED_CHECKS: tuple[Check, ...] = ()
