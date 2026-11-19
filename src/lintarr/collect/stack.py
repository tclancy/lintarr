"""Assemble a StackFacts snapshot from every configured instance.

One failing service must not abort the run, and must not vanish silently
either — failures are recorded so the outcome layer can report ERROR.
"""

import httpx

from lintarr.clients import BACKENDS
from lintarr.collect.arr import collect_arr
from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.config import LintarrConfig
from lintarr.models import ArrInstance, ClientInstance, ErrorRow, StackFacts


def collect_stack(
    cfg: LintarrConfig, *, transport: httpx.BaseTransport | None = None
) -> StackFacts:
    clients: list[ClientInstance] = []
    arrs: list[ArrInstance] = []
    errors: list[ErrorRow] = []

    for client_cfg in cfg.clients:
        label = f"{client_cfg.kind}[{client_cfg.name}]"
        try:
            clients.append(BACKENDS[client_cfg.kind].collect(client_cfg, transport=transport))
        except ServiceError as exc:
            errors.append(ErrorRow(label, exc.kind, exc.detail))

    for arr in cfg.arrs:
        label = f"{arr.kind}[{arr.name}]"
        try:
            with ReadOnlyClient(
                arr.url, transport=transport, headers={"X-Api-Key": arr.api_key}
            ) as client:
                arrs.append(collect_arr(client, arr))
        except ServiceError as exc:
            errors.append(ErrorRow(label, exc.kind, exc.detail))

    return StackFacts(clients=tuple(clients), arrs=tuple(arrs), errors=tuple(errors))
