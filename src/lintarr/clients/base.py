"""What a download-client backend provides.

Imports nothing from ``lintarr.clients.*`` or ``lintarr.config``: every backend
imports this module, and the registry imports every backend, so an edge back
out of here is a cycle waiting for the second backend.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from lintarr.models import ArrInstance, ClientInstance
from lintarr.outcomes import Finding

#: One invariant over one client instance and every collected arr.
type Check = Callable[[ClientInstance, tuple[ArrInstance, ...]], Finding]

type Credential = Literal["user", "pass", "api_key"]


@dataclass(frozen=True, slots=True)
class ArrDependency:
    """What one arr-dependent invariant's failed-arr SKIP says.

    The premise label and the operator-facing detail travel together because
    the detail is the invariant's own sentence: ``hardlink-futility`` could not
    check *imports*, ``orphan-inevitability`` could not check *downloads*, and
    the next one will name its own noun. Keying the wording on which table the
    invariant id came out of instead — the shared checks' or a backend's — is
    only ever right for the first entry in each.
    """

    premise: str
    detail: str


@dataclass(frozen=True, slots=True)
class Backend:
    """One download client kind, self-contained in its own package.

    ``collect`` is called as ``collect(cfg, *, transport)`` with a
    ``ClientConfig`` and raises ``ServiceError`` for anything it cannot read.
    ``therefore`` holds the "Therefore" line for each ``(invariant, conflict)``
    this backend's checks can FAIL on, and may be empty. ``arr_dependent`` maps
    each of this backend's invariant ids that needs arr data to the
    ``ArrDependency`` its failed-arr SKIP carries; it defaults to empty. Its
    keys must not collide with ``lintarr.checks.SHARED_ARR_DEPENDENT``: a
    shared check already owns that id, and ``run`` refuses the pair rather than
    pick one.
    """

    kind: str
    credentials: frozenset[Credential]
    collect: Callable[..., ClientInstance]
    checks: tuple[Check, ...]
    therefore: Mapping[tuple[str, str], str]
    arr_dependent: Mapping[str, ArrDependency] = field(default_factory=dict)
