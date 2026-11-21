"""Pure path helpers for checks that compare paths in an arr's namespace."""

from collections.abc import Iterable
from pathlib import PurePosixPath

from lintarr.models import RemotePathMapping

_SEPARATORS = "/\\"
_PARENT = ".."

NOT_ABSOLUTE_POSIX = "is not absolute POSIX"
HAS_PARENT_SEGMENT = "contains a .. segment, so the mount it is on cannot be read"


def posix_abs_or_reason(raw: object) -> PurePosixPath | str:
    """``raw`` as an absolute POSIX path, or the clause saying why it isn't one.

    A ``..`` component is refused rather than resolved. Comparison here is
    lexical (``PurePosixPath.is_relative_to``), so ``/downloads/../media/x``
    would read as sitting under ``/downloads``; and resolving it lexically is
    only right when no earlier component is a symlink, which the string cannot
    say. Either way the mount is unknowable from the path alone, and a check
    that cannot place a path must report it unknown rather than guess. Real arr
    and qBittorrent paths do not contain ``..``.
    """
    if not isinstance(raw, str) or not raw.startswith("/") or "\\" in raw:
        return NOT_ABSOLUTE_POSIX
    if _PARENT in raw.split("/"):
        return HAS_PARENT_SEGMENT
    return PurePosixPath("/" + raw.lstrip("/"))


def posix_abs(raw: object) -> PurePosixPath | None:
    """Normalise ``raw`` to an absolute POSIX path, or ``None`` if it isn't one."""
    parsed = posix_abs_or_reason(raw)
    return parsed if isinstance(parsed, PurePosixPath) else None


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
