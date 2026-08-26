"""Facts and the explicit absence of facts.

A fact is either read or unknown, never defaulted. Defaulting an unread
setting to a plausible value is how a checker produces a confident wrong
answer, so absence is represented in the type rather than as ``None``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypeGuard

type UnknownReason = Literal["service-absent", "field-absent", "insufficient-permission"]


@dataclass(frozen=True, slots=True)
class Known[T]:
    value: T
    source: str
    read_at: datetime
    service_version: str


@dataclass(frozen=True, slots=True)
class Unknown:
    reason: UnknownReason
    detail: str


type Fact[T] = Known[T] | Unknown


def is_known[T](f: Fact[T]) -> TypeGuard[Known[T]]:
    """True when *f* was actually read from a service."""
    return isinstance(f, Known)


def read(payload: Mapping[str, Any], key: str, *, source: str, version: str) -> Fact[Any]:
    """Read *key* from *payload*.

    Key present -> ``Known``, including when its value is ``None``: services
    legitimately return nulls, and that is a different thing from never having
    been read.
    """
    if key not in payload:
        return Unknown(reason="field-absent", detail=f"{key!r} absent from {source}")
    return Known(
        value=payload[key],
        source=source,
        read_at=datetime.now(UTC),
        service_version=version,
    )
