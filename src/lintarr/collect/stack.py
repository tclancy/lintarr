"""Assemble a StackFacts snapshot from every configured instance.

One failing service must not abort the run, and must not vanish silently
either — failures are recorded so the outcome layer can report ERROR.
"""

import httpx

from lintarr.collect.arr import collect_arr
from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.collect.qbittorrent import AUTH_PATH, collect_qbt
from lintarr.config import LintarrConfig
from lintarr.models import ArrInstance, QbtInstance, StackFacts


def collect_stack(
    cfg: LintarrConfig, *, transport: httpx.BaseTransport | None = None
) -> StackFacts:
    qbits: list[QbtInstance] = []
    arrs: list[ArrInstance] = []
    errors: list[tuple[str, str]] = []

    for qbt in cfg.qbits:
        label = f"qbittorrent[{qbt.name}]"
        try:
            with ReadOnlyClient(qbt.url, transport=transport, auth_path=AUTH_PATH) as client:
                qbits.append(collect_qbt(client, qbt))
        except ServiceError as exc:
            errors.append((label, exc.kind))

    for arr in cfg.arrs:
        label = f"{arr.kind}[{arr.name}]"
        try:
            with ReadOnlyClient(
                arr.url, transport=transport, headers={"X-Api-Key": arr.api_key}
            ) as client:
                arrs.append(collect_arr(client, arr))
        except ServiceError as exc:
            errors.append((label, exc.kind))

    return StackFacts(qbits=tuple(qbits), arrs=tuple(arrs), errors=tuple(errors))
