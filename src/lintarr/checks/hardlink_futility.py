"""``hardlink-futility``: an arr imports by hardlink across two mounts.

For one client ``C``, every collected arr ``A``, every enabled arr download
client ``D`` that is ``C``, and every accessible root folder ``R`` of ``A``,
the triple ``(A, D, R)`` conflicts when ``C``'s save path for ``D``, translated
into ``A``'s namespace, and ``R`` sit on different mounts (neither ``/``) and
``A`` has hardlinks on. See the spec's "The check" for every rule.

Known limitation (spec step 1): a check sees one client, not its siblings, so
two collected clients of one kind that both define ``D``'s category both claim
``D``. That over-reports, and gives a false FAIL when the configured client
shares a category name with an unconfigured one the arr really uses.

Axioms (spec "Axioms"), each with its status:

- ``link(2)`` across two mount points fails with ``EXDEV``, even when both are
  bind mounts of one filesystem. Conformance-tested:
  ``test_link_across_two_bind_mounts_is_exdev``.
- Within one mount point on ext4/xfs, ``link(2)`` succeeds. Conformance-tested:
  ``test_link_within_one_bind_mount_succeeds``.
- Within one mount point, ``link(2)`` succeeds on mergerfs/btrfs subvolumes.
  Assumed false-negative risk: a documented limitation.
- Arr ``diskspace`` lists every mount a download path can lie on. Assumed (the
  arr omits some fs types, e.g. network mounts, so a download on an unlisted
  mount nested inside a listed one reads as the parent mount).
- qBittorrent's empty/relative category ``savePath`` resolution. Assumed from
  source.
- Sonarr/Radarr apply the first matching remote path mapping in API order.
  Assumed from source (``RemotePathMappingService``).

Both tests live in ``tests/conformance/test_hardlink_exdev.py``.

The "an arr failed to collect" SKIP lives in ``run.py`` (``_resolve_failed_arr``):
this check sees no collection errors.
"""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from lintarr.checks.arr_clients import (
    NO_ARR_SENDS_HERE,
    arr_label,
    clients_that_are,
    save_paths_of,
    tuple_of,
)
from lintarr.checks.paths import mount_of, posix_abs, translate
from lintarr.facts import is_known
from lintarr.invariants.combinator import conflict_if, premise
from lintarr.models import (
    ArrDownloadClient,
    ArrInstance,
    ClientInstance,
    RemotePathMapping,
    RootFolder,
)
from lintarr.outcomes import Finding, Outcome

INVARIANT_ID = "hardlink-futility"
CROSS_MOUNT = "cross-mount"
PREMISE = "arr.hardlink_import_crosses_a_mount"

_ROOT = PurePosixPath("/")


@dataclass(frozen=True, slots=True)
class Verdict:
    """One triple's (or one arr's) answer: True conflict, False none, None unknown."""

    state: bool | None
    fragment: str = ""
    #: The arr whose download and library share a mount, for the PASS detail.
    shared_by: str = ""


def _hardlinks(arr: ArrInstance) -> bool | None:
    fact = arr.copy_using_hardlinks
    return fact.value if is_known(fact) and isinstance(fact.value, bool) else None


def _mount_or_reason(path: PurePosixPath, mounts: tuple[str, ...]) -> PurePosixPath | str:
    mount = mount_of(path, mounts)
    return mount if mount is not None else f"the arr reports no mount containing {path}"


def _one_side_on_root(
    label: str, download: PurePosixPath, dm: PurePosixPath, root: PurePosixPath
) -> Verdict:
    """Spec: exactly one side only on ``/`` is unknown, and the detail says which."""
    if dm == _ROOT:
        reason = (
            f"no mount other than / for {download}; either it cannot see the download "
            "or that mount is not listed (network mounts are not)"
        )
    else:
        reason = f"no mount for root folder {root} other than /, so the library is not on a volume"
    return Verdict(None, f"{label}: the arr reports {reason}")


def _triple(
    label: str,
    hardlinks: bool | None,
    download: PurePosixPath,
    root: PurePosixPath,
    mounts: tuple[str, ...],
) -> Verdict:
    """Spec step 5 for one ``(A, D, R)`` whose paths are already in ``A``'s namespace."""
    dm, rm = _mount_or_reason(download, mounts), _mount_or_reason(root, mounts)
    for side in (dm, rm):
        if isinstance(side, str):
            return Verdict(None, f"{label}: {side}")
    if dm == rm:
        return Verdict(False, shared_by=label)
    if _ROOT in (dm, rm):
        return _one_side_on_root(label, download, dm, root)
    if hardlinks is None:
        return Verdict(None, f"{label}: its hardlink setting could not be read")
    conflict = (
        f"{label}: downloads in {download} are on mount {dm}, root folder {root} is on mount {rm}"
    )
    return Verdict(True, conflict)


@dataclass(frozen=True, slots=True)
class _ArrView:
    """The facts an involved arr must supply, each already read and type-checked."""

    label: str
    hardlinks: bool | None
    roots: tuple[RootFolder, ...]
    mounts: tuple[str, ...]
    mappings: tuple[RemotePathMapping, ...]


def _unreadable_arr_inputs(arr: ArrInstance) -> list[str]:
    fields = {
        "root folders": tuple_of(arr.root_folders, RootFolder),
        "mounts": tuple_of(arr.mounts, str),
        "remote path mappings": tuple_of(arr.remote_path_mappings, RemotePathMapping),
    }
    return [name for name, value in fields.items() if value is None]


def _view(arr: ArrInstance) -> _ArrView:
    return _ArrView(
        label=arr_label(arr),
        hardlinks=_hardlinks(arr),
        roots=tuple_of(arr.root_folders, RootFolder) or (),
        mounts=tuple_of(arr.mounts, str) or (),
        mappings=tuple_of(arr.remote_path_mappings, RemotePathMapping) or (),
    )


def _download_path(view: _ArrView, d: ArrDownloadClient, raw: object) -> PurePosixPath | str:
    """Spec steps 2-3: the save path in the arr's namespace, or why it isn't readable."""
    if not isinstance(raw, str):
        return f"{view.label}: the client's save path for {d.category!r} is not a string"
    translated = translate(raw, view.mappings, d.host)
    path = posix_abs(translated)
    if path is not None:
        return path
    source = f" (translated from {raw!r})" if translated != raw else ""
    return f"{view.label}: save path {translated!r}{source} is not absolute POSIX"


def _root_path(view: _ArrView, folder: RootFolder) -> PurePosixPath | str:
    path = posix_abs(folder.path)
    if path is None:
        return f"{view.label}: root folder {folder.path!r} is not absolute POSIX"
    return path


def _usable_roots(view: _ArrView) -> tuple[RootFolder, ...]:
    return tuple(r for r in view.roots if r.accessible is not False)


def _left_out_roots(view: _ArrView) -> list[str]:
    return [
        f"{view.label}: root folder {r.path} is not accessible and was left out"
        for r in view.roots
        if r.accessible is False
    ]


def _triples(
    view: _ArrView, clients: Iterable[ArrDownloadClient], save_paths: Mapping[str, object]
) -> Iterator[Verdict]:
    """Every ``(A, D, R)`` verdict for one arr whose inputs are all readable."""
    for d in clients:
        download = _download_path(view, d, save_paths[d.category])
        for folder in _usable_roots(view):
            root = _root_path(view, folder)
            unreadable = next((p for p in (download, root) if isinstance(p, str)), None)
            if unreadable is not None:
                yield Verdict(None, unreadable)
            else:
                yield _triple(view.label, view.hardlinks, download, root, view.mounts)


def _arr_verdicts(
    arr: ArrInstance, instance: ClientInstance, save_paths: Mapping[str, object] | None
) -> tuple[list[Verdict], bool]:
    """One arr's verdicts, and whether it (or, unreadably, may) send downloads to this client."""
    label = arr_label(arr)
    clients = clients_that_are(arr, instance, save_paths)
    if clients == ():
        return [], False
    if _hardlinks(arr) is False:
        return [Verdict(False, f"{label}: hardlinks are off, so imports copy by design")], True
    if isinstance(clients, str):
        return [Verdict(None, clients)], True
    unreadable = _unreadable_arr_inputs(arr)
    if save_paths is None:
        unreadable.append("this client's save paths")
    if unreadable:
        return [Verdict(None, f"{label}: could not read {', '.join(unreadable)}")], True
    view = _view(arr)
    notes = [Verdict(False, note) for note in _left_out_roots(view)]
    if not _usable_roots(view):
        notes.append(Verdict(False, f"{label}: no root folders, so nothing imports"))
    return [*notes, *_triples(view, clients, save_paths)], True


def _aggregate(verdicts: list[Verdict]) -> tuple[bool | None, list[str]]:
    """FAIL on any conflict, else SKIP on any unknown, else PASS; with the lines to show."""
    conflicts = [v.fragment for v in verdicts if v.state is True]
    if conflicts:
        return True, list(dict.fromkeys(conflicts))
    unknowns = [v.fragment for v in verdicts if v.state is None]
    if unknowns:
        return None, list(dict.fromkeys(unknowns))
    return False, _pass_lines(verdicts)


def _pass_lines(verdicts: list[Verdict]) -> list[str]:
    """What a PASS decided: which arrs share a mount, then each note (hardlinks off, left out)."""
    shared = list(dict.fromkeys(v.shared_by for v in verdicts if v.shared_by))
    lines = (
        [f"downloads and library folders share a mount for {', '.join(shared)}"] if shared else []
    )
    return lines + list(dict.fromkeys(v.fragment for v in verdicts if v.fragment))


def _not_applicable(label: str) -> Finding:
    return Finding(
        invariant=INVARIANT_ID,
        instance=label,
        outcome=Outcome.NOT_APPLICABLE,
        detail="no sonarr or radarr was collected, so no import can be checked",
        conflict=CROSS_MOUNT,
    )


def check(instance: ClientInstance, arrs: tuple[ArrInstance, ...]) -> Finding:
    """One finding for *instance* over every collected arr."""
    label = f"{instance.kind}[{instance.name}]"
    if not arrs:
        return _not_applicable(label)
    save_paths = save_paths_of(instance)
    verdicts: list[Verdict] = []
    involved = False
    for arr in arrs:
        arr_verdicts, sends_here = _arr_verdicts(arr, instance, save_paths)
        verdicts += arr_verdicts
        involved |= sends_here
    state, lines = _aggregate(verdicts)
    finding = conflict_if(INVARIANT_ID, label, premise(PREMISE, state), conflict=CROSS_MOUNT)
    detail = "; ".join(lines) if lines or involved else NO_ARR_SENDS_HERE
    return replace(finding, detail=detail)
