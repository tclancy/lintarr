"""Pure path helpers for checks that compare paths in an arr's namespace."""

from collections.abc import Iterable
from pathlib import PurePosixPath

from lintarr.models import RemotePathMapping

_SEPARATORS = "/\\"


def posix_abs(raw: object) -> PurePosixPath | None:
    """Normalise ``raw`` to an absolute POSIX path, or ``None`` if it isn't one."""
    if not isinstance(raw, str) or not raw.startswith("/") or "\\" in raw:
        return None
    return PurePosixPath("/" + raw.lstrip("/"))


def _remainder_after(raw: str, remote_path: str) -> str | None:
    """What follows ``remote_path`` in ``raw`` on a separator boundary, else ``None``."""
    prefix = remote_path.rstrip(_SEPARATORS)
    if not raw.startswith(prefix):
        return None
    rest = raw[len(prefix) :]
    if rest and rest[0] not in _SEPARATORS:
        return None
    return rest


def _join(local_path: str, remainder: str) -> str:
    base = local_path.rstrip(_SEPARATORS)
    tail = remainder.replace("\\", "/").strip("/")
    if not tail:
        return base or "/"
    return f"{base}/{tail}"


def translate(raw: str, mappings: Iterable[RemotePathMapping], host: str | None) -> str:
    """Apply the first matching remote path mapping, as the arr does."""
    if host is None:
        return raw
    for mapping in mappings:
        if mapping.host.lower() != host.lower():
            continue
        rest = _remainder_after(raw, mapping.remote_path)
        if rest is not None:
            return _join(mapping.local_path, rest)
    return raw


def mount_of(path: PurePosixPath, mounts: Iterable[str]) -> PurePosixPath | None:
    """The longest readable mount that contains ``path``, or ``None``."""
    parsed = (posix_abs(mount) for mount in mounts)
    containing = [m for m in parsed if m is not None and path.is_relative_to(m)]
    return max(containing, key=lambda m: len(m.parts), default=None)
