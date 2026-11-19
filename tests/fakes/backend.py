"""A download-client backend that exists only in the tests.

It does no HTTP. It proves the seam: registering it is the only edit needed
for config, collection, checks and rendering to treat it as a first-class kind.
"""

from dataclasses import dataclass

from lintarr.clients.base import Backend
from lintarr.collect.http import ServiceError
from lintarr.config import ClientConfig
from lintarr.models import ArrInstance, ClientFacts, ClientInstance
from lintarr.outcomes import Finding, Outcome


@dataclass(frozen=True, slots=True)
class FakeFacts:
    broken: bool


def collect(cfg: ClientConfig, *, transport: object) -> ClientInstance:
    if cfg.url.endswith("/down"):
        raise ServiceError("unreachable", "fake")
    return ClientInstance(
        name=cfg.name,
        kind="fake",
        version="0",
        common=ClientFacts(),
        specific=FakeFacts(broken=cfg.url.endswith("/broken")),
    )


def fake_check(inst: ClientInstance, arrs: tuple[ArrInstance, ...]) -> Finding:
    assert isinstance(inst.specific, FakeFacts)
    return Finding(
        invariant="fake-check",
        instance=f"fake[{inst.name}]",
        outcome=Outcome.FAIL if inst.specific.broken else Outcome.PASS,
    )


FAKE = Backend(
    kind="fake",
    credentials=frozenset({"api_key"}),
    collect=collect,
    checks=(fake_check,),
    therefore={},
)
