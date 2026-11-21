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


def _has_drive_letter(path: str) -> bool:
    """``path`` starts with a Windows drive letter (``OsPath.HasWindowsDriveLetter``)."""
    if len(path) < 2 or not path[0].isalpha() or path[1] != ":":
        return False
    return len(path) == 2 or path[2] in _SEPARATORS


def _is_windows(path: str) -> bool:
    """``path`` is a Windows path to the arr (``OsPath.DetectPathKind``).

    A leading ``/`` decides first, so ``/dl\\x`` is POSIX to the arr however
    many backslashes follow.
    """
    if path.startswith("/"):
        return False
    return _has_drive_letter(path) or "\\" in path


def _is_rooted(path: str) -> bool:
    """``path`` is rooted (``OsPath.IsRooted``); an unrooted path matches nothing."""
    if _is_windows(path):
        return path.startswith("\\\\") or _has_drive_letter(path)
    return path.startswith("/")


def _as_compared(text: str, windows: bool) -> str:
    """``text`` folded the way the arr compares it.

    ``OsPath`` compares a Windows path case-insensitively and reads ``/`` and
    ``\\`` as one separator; a POSIX path is compared case-sensitively, so
    ``/DL`` and ``/dl`` are two directories. Folding is applied to equal-length
    slices of the originals, so it never moves the boundary the remainder is
    cut on. See ``docs/measurements/2026-11-21-arr-path-mapping-edges.md``.
    """
    return text.replace("\\", "/").lower() if windows else text


def _remainder_after(raw: str, remote_path: str) -> str | None:
    """What follows ``remote_path`` in ``raw`` on a separator boundary, else ``None``."""
    if not (_is_rooted(raw) and _is_rooted(remote_path)):
        return None
    prefix = remote_path.rstrip(_SEPARATORS)
    windows = _is_windows(raw) or _is_windows(remote_path)
    if _as_compared(raw[: len(prefix)], windows) != _as_compared(prefix, windows):
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


def _applied(mapping: RemotePathMapping, remainder: str) -> str:
    """``remainder`` placed under ``mapping``'s local path, as the arr places it.

    Two edges the arr reaches before the ordinary join does, both measured in
    ``docs/measurements/2026-11-21-arr-path-mapping-edges.md``:

    - **A root remote path swallows the path.** The arr subtracts the mapping's
      remote path from the client's, and ``/`` has no components to subtract,
      so the subtraction hands back the mapping's own remote path -- rooted,
      and a rooted right-hand side wins the join outright. So ``/`` ->
      ``/mnt`` does not prefix anything with ``/mnt``; every path the client
      reports becomes ``/``. Faithful, not tidy, and deliberately kept: a
      check must claim only what the arr will really see.
    - **A blank local path places nothing.** The arr's API refuses one
      (``IsValidPath`` and ``PathExistsValidator`` on ``localPath``), so it can
      only come from a hand-edited database; the arr's own arithmetic would
      turn ``/downloads/tv`` into ``/tv``, an absolute path that reads as real
      and would then be placed on a mount. lintarr returns ``''`` instead,
      which no caller can place, so the path is reported unknown rather than
      guessed. The one knowing divergence here from what the arr's code does.
    """
    if not mapping.remote_path.strip(_SEPARATORS):
        return "/"
    if not mapping.local_path.strip():
        return ""
    return _join(mapping.local_path, remainder)


def translate(raw: str, mappings: Iterable[RemotePathMapping], host: str | None) -> str:
    """Apply the first matching remote path mapping, as the arr does."""
    if host is None:
        return raw
    for mapping in mappings:
        if mapping.host.lower() != host.lower():
            continue
        rest = _remainder_after(raw, mapping.remote_path)
        if rest is not None:
            return _applied(mapping, rest)
    return raw


def mount_of(path: PurePosixPath, mounts: Iterable[str]) -> PurePosixPath | None:
    """The longest readable mount that contains ``path``, or ``None``."""
    parsed = (posix_abs(mount) for mount in mounts)
    containing = [m for m in parsed if m is not None and path.is_relative_to(m)]
    return max(containing, key=lambda m: len(m.parts), default=None)
