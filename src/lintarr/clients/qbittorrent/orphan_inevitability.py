"""``orphan-inevitability``: a seed limit fires and nothing removes the files.

For one qBittorrent client ``C``, every collected arr ``A`` and every arr
download client ``D`` that is ``C`` (``lintarr.checks.arr_clients``, the rule
``hardlink-futility`` uses), the pair ``(A, D)`` conflicts when some seed limit
can be reached for ``A``'s torrents and the action qBittorrent then takes, with
what ``A`` does about it, leaves the files on disk. See the spec's "The check".

How the arr removes a download (Sonarr ``v5-develop``, read 2026-11-19; Radarr
shares the shape): only with Remove Completed on, only for torrents still in its
own category, and only when the limit that stopped the torrent is the torrent's
own (an indexer goal) or an inherited global. A category's own limit counts only
when the same-arm global, which the arr checks for a ``-2`` torrent, is
reachable at or below it (``share_limits``).

The decision reads the action first. Action 3 (RemoveWithContent) settles the
pair without reading reachability, so an input only reachability needs can
never turn it into a SKIP.

Known limits (spec "Known limits"): an indexer pinned to another client is still
counted; parent categories are not followed; older arr builds that miss
qBittorrent 5's ``stoppedUP`` are not modelled; Cleanuparr-style external
cleanup is not seen and FAILs honestly; an arr too old to report top-level
``removeCompletedDownloads`` reads Remove Completed as unknown, so a Stop action
with a reachable limit SKIPs on every strict run; a category's own inactive
limit is never counted as covered by the global (its value is not collected).

The "an arr failed to collect" SKIP lives in ``run.py``, keyed by
``BACKEND.arr_dependent``: this check sees no collection errors.
"""

from collections.abc import Iterator
from dataclasses import dataclass, replace

from lintarr.checks.arr_clients import NO_ARR_SENDS_HERE, arr_label, clients_that_are, save_paths_of
from lintarr.clients.qbittorrent.share_limits import (
    ACTION_BY_NAME,
    can_fire,
    can_fire_unrecognised,
    effective_action,
)
from lintarr.invariants.combinator import conflict_if, premise
from lintarr.models import ArrDownloadClient, ArrInstance, ClientInstance, QbtInstance
from lintarr.outcomes import Finding, Outcome, Premise

INVARIANT_ID = "orphan-inevitability"
REMOVE_KEEPS_FILES = "remove-keeps-files"
NOTHING_RECLAIMS = "nothing-reclaims"
PREMISE_FILES_STAY = "arr.limit_fires_and_files_stay"
PREMISE_NOTHING_RECLAIMS = "arr.limit_fires_and_nothing_reclaims"

#: FAIL order: the first conflict found here wins.
CONFLICTS = (REMOVE_KEEPS_FILES, NOTHING_RECLAIMS)
_PREMISE_OF = {REMOVE_KEEPS_FILES: PREMISE_FILES_STAY, NOTHING_RECLAIMS: PREMISE_NOTHING_RECLAIMS}

_STOP = ACTION_BY_NAME["Stop"]
_REMOVE = ACTION_BY_NAME["Remove"]
_REMOVE_WITH_CONTENT = ACTION_BY_NAME["RemoveWithContent"]
_NAME_OF = {value: name for name, value in ACTION_BY_NAME.items()} | {-1: "Default"}


@dataclass(frozen=True, slots=True)
class Verdict:
    """One pair's answer: True conflict, False none, None unknown.

    ``conflicts`` names the questions the answer bears on. A known action bears
    on one; an unknown action, or arr clients that cannot be told, on both.
    """

    state: bool | None
    conflicts: tuple[str, ...]
    fragment: str


@dataclass(frozen=True, slots=True)
class _Pair:
    """One ``(A, D)`` with its settled category and whether the arr removes."""

    label: str
    category: str
    imported: bool
    arr_removes: bool | None

    @property
    def name(self) -> str:
        where = f"category {self.category}" if self.category else "no category"
        return f"{self.label} ({where})"


def _imported_category(d: ArrDownloadClient) -> str:
    """Spec step 0: the post-import category when it re-labels, else ``""``."""
    value = d.imported_category
    if isinstance(value, str) and value and value != d.category:
        return value
    return ""


def _pair(arr: ArrInstance, d: ArrDownloadClient) -> _Pair:
    """Spec step 0: the settled category, and Remove Completed as the arr can apply it."""
    imported = _imported_category(d)
    removes = d.remove_completed if isinstance(d.remove_completed, bool) else None
    return _Pair(
        label=arr_label(arr),
        category=imported or d.category,
        imported=bool(imported),
        arr_removes=False if imported else removes,
    )


def _fires(pair: _Pair, conflict: str, fires: bool | None, why: str) -> Verdict:
    """A conflict iff *fires*; unknown when it could not be told."""
    if fires is None:
        return Verdict(
            None, (conflict,), f"{pair.name}: whether a seed limit can fire could not be read"
        )
    if fires:
        return Verdict(True, (conflict,), f"{pair.name}: a limit can fire; {why}")
    return Verdict(False, (conflict,), f"{pair.label}: no limit can fire; seeding is unlimited")


def _remove_keeps_files(qbt: QbtInstance, arr: ArrInstance, pair: _Pair) -> Verdict:
    """Action 1: qBittorrent drops the torrent and leaves its files."""
    return _fires(
        pair,
        REMOVE_KEEPS_FILES,
        can_fire(qbt, arr, pair.category),
        "action is Remove, which keeps files",
    )


def _stop_without_arr_removal(qbt: QbtInstance, arr: ArrInstance, pair: _Pair) -> Verdict:
    if pair.imported:
        why = (
            f"action is Stop and the arr re-labels imports to category {pair.category}, "
            "so it can no longer remove them"
        )
    else:
        why = "action is Stop and the arr's Remove Completed is off"
    return _fires(pair, NOTHING_RECLAIMS, can_fire(qbt, arr, pair.category), why)


def _arr_reclaims(qbt: QbtInstance, arr: ArrInstance, pair: _Pair) -> Verdict:
    """No unrecognised limit fires: say which of the two reasons settled it.

    Either no limit can fire at all, or one can and it is a limit the arr
    recognises. Both are PASS, but only the second involves a limit firing, so
    the first borrows the unlimited-seeding line rather than claiming a stop
    that cannot happen.
    """
    if can_fire(qbt, arr, pair.category) is False:
        return _fires(pair, NOTHING_RECLAIMS, False, "")
    return Verdict(
        False,
        (NOTHING_RECLAIMS,),
        f"{pair.label}: qBittorrent stops at the limit and the arr removes completed downloads",
    )


def _stop_with_arr_removal(qbt: QbtInstance, arr: ArrInstance, pair: _Pair) -> Verdict:
    """Only a category's own limit the global does not cover leaves files."""
    fires = can_fire_unrecognised(qbt, arr, pair.category)
    if fires is False:
        return _arr_reclaims(qbt, arr, pair)
    why = (
        f"action is Stop and the limit that fires is category {pair.category}'s own, "
        "which the arr does not check"
    )
    return _fires(pair, NOTHING_RECLAIMS, fires, why)


def _stop_with_arr_removal_unknown(qbt: QbtInstance, arr: ArrInstance, pair: _Pair) -> Verdict:
    """Remove Completed unread: only "no limit can fire at all" settles the pair."""
    fires = can_fire(qbt, arr, pair.category)
    if fires is False:
        return _fires(pair, NOTHING_RECLAIMS, False, "")
    unread = f"{pair.name}: the arr's Remove Completed setting could not be read"
    if fires is None:
        unread = f"{unread}; {_fires(pair, NOTHING_RECLAIMS, None, '').fragment}"
    return Verdict(None, (NOTHING_RECLAIMS,), unread)


def _stop(qbt: QbtInstance, arr: ArrInstance, pair: _Pair) -> Verdict:
    if pair.arr_removes is None:
        return _stop_with_arr_removal_unknown(qbt, arr, pair)
    if pair.arr_removes:
        return _stop_with_arr_removal(qbt, arr, pair)
    return _stop_without_arr_removal(qbt, arr, pair)


def _keeps_seeding(qbt: QbtInstance, arr: ArrInstance, pair: _Pair, action: int) -> Verdict:
    """Super seeding, ``-1`` or any other action: nothing removes anything."""
    name = _NAME_OF.get(action, str(action))
    why = f"action is {name}, which removes nothing"
    return _fires(pair, NOTHING_RECLAIMS, can_fire(qbt, arr, pair.category), why)


def _pair_verdict(qbt: QbtInstance, arr: ArrInstance, d: ArrDownloadClient) -> Verdict:
    """Spec steps 1-2: decide from the action first, reading reachability only if needed."""
    pair = _pair(arr, d)
    action = effective_action(qbt, pair.category)
    if action is None:
        return Verdict(None, CONFLICTS, f"{pair.name}: the share-limit action could not be read")
    if action == _REMOVE_WITH_CONTENT:
        return Verdict(False, (), f"{pair.label}: qBittorrent removes files at the limit")
    if action == _REMOVE:
        return _remove_keeps_files(qbt, arr, pair)
    if action == _STOP:
        return _stop(qbt, arr, pair)
    return _keeps_seeding(qbt, arr, pair, action)


def _arr_verdicts(
    arr: ArrInstance, instance: ClientInstance, qbt: QbtInstance
) -> Iterator[Verdict]:
    clients = clients_that_are(arr, instance, save_paths_of(instance))
    if isinstance(clients, str):
        yield Verdict(None, CONFLICTS, clients)
        return
    for d in clients:
        yield _pair_verdict(qbt, arr, d)


def _lines(verdicts: list[Verdict], state: bool | None, conflict: str | None = None) -> list[str]:
    return list(
        dict.fromkeys(
            v.fragment
            for v in verdicts
            if v.state is state and (conflict is None or conflict in v.conflicts)
        )
    )


def _fail(label: str, verdicts: list[Verdict]) -> Finding | None:
    """The first conflict any pair reaches, in ``CONFLICTS`` order."""
    for conflict in CONFLICTS:
        lines = _lines(verdicts, True, conflict)
        if lines:
            finding = conflict_if(
                INVARIANT_ID, label, premise(_PREMISE_OF[conflict], True), conflict=conflict
            )
            return _with_detail(finding, lines)
    return None


def _skip(label: str, verdicts: list[Verdict]) -> Finding | None:
    """Unknown premises, each once, for every question some pair could not answer.

    The conflict is always ``nothing-reclaims``, like PASS and N/A.
    """
    unknown = [c for c in CONFLICTS if any(v.state is None and c in v.conflicts for v in verdicts)]
    if not unknown:
        return None
    return Finding(
        invariant=INVARIANT_ID,
        instance=label,
        outcome=Outcome.SKIP,
        premises=tuple(Premise(_PREMISE_OF[c], None) for c in unknown),
        detail="; ".join(_lines(verdicts, None)),
        # Fixed, not unknown[0]: the conflict is part of the edge key, and a
        # SKIP that flipped between conflicts would re-page under --strict.
        # The premises still say which questions went unanswered.
        conflict=NOTHING_RECLAIMS,
    )


def _with_detail(finding: Finding, lines: list[str]) -> Finding:
    return replace(finding, detail="; ".join(lines))


def _pass(label: str, verdicts: list[Verdict]) -> Finding:
    """What decided each pair, or that no arr uses this client at all."""
    lines = _lines(verdicts, False) or [NO_ARR_SENDS_HERE]
    finding = Finding(
        invariant=INVARIANT_ID, instance=label, outcome=Outcome.PASS, conflict=NOTHING_RECLAIMS
    )
    return _with_detail(finding, lines)


def _not_applicable(label: str) -> Finding:
    return Finding(
        invariant=INVARIANT_ID,
        instance=label,
        outcome=Outcome.NOT_APPLICABLE,
        detail="no sonarr or radarr was collected, so no download can be checked",
        conflict=NOTHING_RECLAIMS,
    )


def check(instance: ClientInstance, arrs: tuple[ArrInstance, ...]) -> Finding:
    """One finding for *instance* over every collected arr."""
    label = f"qbittorrent[{instance.name}]"
    if not arrs:
        return _not_applicable(label)
    qbt = instance.specific
    verdicts = [v for arr in arrs for v in _arr_verdicts(arr, instance, qbt)]
    return _fail(label, verdicts) or _skip(label, verdicts) or _pass(label, verdicts)
