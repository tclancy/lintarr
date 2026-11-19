import pytest

import lintarr.checks
from lintarr.clients import BACKENDS
from lintarr.models import ArrInstance, ClientInstance
from lintarr.outcomes import Finding, Outcome
from tests.fakes.backend import FAKE


@pytest.fixture
def fake_backend(monkeypatch):
    monkeypatch.setitem(BACKENDS, "fake", FAKE)


def probe(inst: ClientInstance, arrs: tuple[ArrInstance, ...]) -> Finding:
    return Finding(
        invariant="shared-probe", instance=f"{inst.kind}[{inst.name}]", outcome=Outcome.PASS
    )


@pytest.fixture
def shared_probe(monkeypatch):
    monkeypatch.setattr(lintarr.checks, "SHARED_CHECKS", (probe,))
