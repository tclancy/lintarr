"""What a download-client backend provides.

Imports nothing from ``lintarr.clients.*`` or ``lintarr.config``: every backend
imports this module, and the registry imports every backend, so an edge back
out of here is a cycle waiting for the second backend.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from lintarr.models import ArrInstance, ClientInstance
from lintarr.outcomes import Finding

#: One invariant over one client instance and every collected arr.
type Check = Callable[[ClientInstance, tuple[ArrInstance, ...]], Finding]

type Credential = Literal["user", "pass", "api_key"]


@dataclass(frozen=True, slots=True)
class Backend:
    """One download client kind, self-contained in its own package.

    ``collect`` is called as ``collect(cfg, *, transport)`` with a
    ``ClientConfig`` and raises ``ServiceError`` for anything it cannot read.
    ``therefore`` holds the "Therefore" line for each ``(invariant, conflict)``
    this backend's checks can FAIL on, and may be empty.
    """

    kind: str
    credentials: frozenset[Credential]
    collect: Callable[..., ClientInstance]
    checks: tuple[Check, ...]
    therefore: Mapping[tuple[str, str], str]
