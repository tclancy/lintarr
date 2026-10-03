"""The explanation for a failed collection has to reach the operator.

Before #18, ``collect_stack`` recorded ``(label, kind)`` and dropped
``ServiceError.detail`` on the floor, so every explanation written in the
collect layer was prose for a traceback nobody sees. The clearest casualty is
on the qBittorrent 403 path, where ``authenticate`` writes forty words telling
the operator an IP ban may be active and pointing at issue #7 — and the CLI
could only ever print ``ERROR  qbittorrent[main]: unauthorised``.

These tests pin the detail's *route* — collect to ``StackFacts`` to both
renderers and to the outcome layer — and deliberately never pin its wording.
A reworded detail must not turn any of them red; that is why each assertion
reads the detail out of the fixture or out of ``ServiceError`` rather than
repeating a string. ``kind`` is asserted by exact equality against the
``ErrorKind`` alias, because the one regression that would make the detail
worse than useless is prose getting concatenated into the kind that every
other layer matches on.
"""

import json
import os
import typing

import httpx
import pytest
from click.testing import CliRunner

from lintarr.cli import cli
from lintarr.collect.http import ErrorKind, ServiceError
from lintarr.collect.stack import collect_stack
from lintarr.config import load_config
from lintarr.models import ErrorRow, StackFacts
from lintarr.outcomes import Outcome
from lintarr.run import run_checks

_PREFIXES = ("QBIT_", "SONARR_", "RADARR_", "LINTARR_")
_CLEARED = {k: None for k in os.environ if k.startswith(_PREFIXES)}

ENV = {
    "QBIT_URL": "http://qbt:8080",
    "QBIT_USER": "admin",
    "QBIT_PASS": "pw",
    "SONARR_URL": "http://sonarr:8989",
    "SONARR_API_KEY": "k",
}

#: Every value the error-row ``kind`` is allowed to be. Read off the alias
#: rather than listed here, so a new kind cannot slip past the concatenation
#: guard below by being absent from a hand-written copy.
KINDS = frozenset(typing.get_args(ErrorKind.__value__))


def _blank_detail_row():
    """An error row whose detail is the empty string.

    Built here rather than inline so the renderers' empty-detail handling is
    exercised through the real row type, not a stand-in that happens to have
    the same attributes.
    """
    return ErrorRow(label="qbittorrent[main]", kind="banned", detail="")


def _transport(*, qbt_down=False, sonarr_down=False):
    """A stack where either service can be made to fail independently.

    Both arms of ``collect_stack`` have their own ``except ServiceError``, so a
    fix applied to one of them passes any test that only ever breaks the other.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "qbt":
            if qbt_down:
                # 403, which is the path carrying the longest detail on main.
                return httpx.Response(403, text="Forbidden")
            match path:
                case "/api/v2/auth/login":
                    return httpx.Response(200, text="Ok.")
                case "/api/v2/app/version":
                    return httpx.Response(200, text="v5.2.3")
                case "/api/v2/app/preferences":
                    return httpx.Response(200, json={"queueing_enabled": True})
                case "/api/v2/torrents/categories":
                    return httpx.Response(200, json={})
        if host == "sonarr":
            if sonarr_down:
                raise httpx.ConnectError("refused", request=request)
            match path:
                case "/api/v3/system/status":
                    return httpx.Response(200, json={"version": "4.0.0"})
                case "/api/v3/indexer":
                    return httpx.Response(200, json=[])
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def _collect(**down):
    return collect_stack(load_config(ENV), transport=_transport(**down))


def _only_row(facts):
    assert len(facts.errors) == 1, f"expected exactly one error row, got {facts.errors!r}"
    return facts.errors[0]


# ---------------------------------------------------------------- collect layer


@pytest.mark.parametrize(
    ("down", "label", "kind"),
    [
        ({"qbt_down": True}, "qbittorrent[main]", "unauthorised"),
        ({"sonarr_down": True}, "sonarr[main]", "unreachable"),
    ],
    ids=["qbittorrent-arm", "arr-arm"],
)
def test_collect_stack_keeps_the_detail_on_both_arms(down, label, kind):
    row = _only_row(_collect(**down))
    assert (row.label, row.kind) == (label, kind)
    assert row.detail, "the detail is the whole point of the row — it must not be empty"


def test_the_qbittorrent_403_recovery_note_survives_collection():
    """The specific prose #18 was filed over.

    ``authenticate`` writes a forty-word explanation for a 403 naming the
    candidate cause and the ticket to read. Matched on a short substring the
    sentence cannot lose without changing meaning, not on the whole sentence.
    """
    row = _only_row(_collect(qbt_down=True))
    assert "issue #7" in row.detail


def test_the_row_carries_the_detail_the_exception_carried():
    """No paraphrase in between.

    Asserted against ``ServiceError`` itself rather than a literal, so the
    collect layer is free to reword and this test still means something.
    """
    expected = ServiceError("unreachable", "/api/v3/system/status: ConnectError").detail
    assert _only_row(_collect(sonarr_down=True)).detail == expected


# ------------------------------------------------------------------- CLI output


def _run(args, **down):
    env = {**_CLEARED, **ENV}
    result = CliRunner().invoke(cli, args, env=env, obj={"transport": _transport(**down)})
    return result


def test_json_carries_the_detail_as_its_own_field():
    payload = json.loads(_run(["dump-facts", "--json"], qbt_down=True).output)
    row = payload["errors"][0]
    assert row["kind"] == "unauthorised", "the kind must stay a bare ErrorKind value"
    assert row["detail"], "the detail needs a field of its own"
    assert row["detail"] not in row["kind"], "the detail must not be folded into the kind"


@pytest.mark.parametrize(
    "down", [{"qbt_down": True}, {"sonarr_down": True}], ids=["qbittorrent-arm", "arr-arm"]
)
def test_every_kind_the_json_emits_is_a_bare_error_kind_value(down):
    """The anti-concatenation guard, and the reason ``KINDS`` is derived.

    ``"unauthorised: qBittorrent refused login..."`` reads fine to a human and
    silently breaks every consumer that compares ``kind`` for equality — which
    is ``run.py``, the JSON schema, and any CI job branching on it.
    """
    payload = json.loads(_run(["dump-facts", "--json"], **down).output)
    assert payload["errors"], "no error row collected — this guard would be vacuous"
    for row in payload["errors"]:
        assert row["kind"] in KINDS, f"{row['kind']!r} is not one of {sorted(KINDS)}"


def test_human_dump_facts_prints_the_detail():
    facts = _collect(qbt_down=True)
    output = _run(["dump-facts"], qbt_down=True).output
    assert "ERROR  qbittorrent[main]: unauthorised" in output
    assert _only_row(facts).detail in output


def test_check_names_the_detail_in_its_error_finding():
    """``check`` is the command operators actually run.

    It renders ``Finding.detail``, and that string was built from the kind
    alone — so fixing only ``dump-facts`` would leave the primary command
    still printing "could not read this service: unauthorised" and nothing else.
    """
    facts = _collect(qbt_down=True)
    output = _run(["check"], qbt_down=True).output
    assert _only_row(facts).detail in output


# ----------------------------------------------------------------- outcome layer


def test_error_findings_carry_the_detail():
    facts = _collect(sonarr_down=True)
    errors = [
        f for f in run_checks(facts, declared=frozenset({"sonarr"})) if f.outcome is Outcome.ERROR
    ]
    assert len(errors) == 1
    assert _only_row(facts).detail in errors[0].detail
    assert "unreachable" in errors[0].detail, "the kind still has to be in there too"


def test_a_blank_detail_leaves_no_dangling_separator():
    """``ServiceError`` does not forbid an empty detail, so the renderers must not assume one.

    A naive f-string gives "could not read this service: banned — " with
    nothing after the dash, which reads as truncated output and sends an
    operator looking for the rest of a message that does not exist.
    """
    facts = StackFacts(qbits=(), arrs=(), errors=(_blank_detail_row(),))
    (finding,) = run_checks(facts, declared=frozenset({"qbittorrent"}))
    assert finding.detail.rstrip() == finding.detail
    assert not finding.detail.rstrip().endswith(("—", "-", ":"))


def test_attempted_still_recovers_the_service_kind_from_the_label():
    """``run._attempted`` splits the label on ``[`` to get the service kind.

    Widening the row changes that unpacking, and getting it wrong is silent:
    a declared service that errored would be reported twice, once as ERROR and
    once as "declared but never collected", which is the exact double-report
    ``_absent_service_findings`` exists to prevent.
    """
    facts = _collect(sonarr_down=True)
    findings = run_checks(facts, declared=frozenset({"qbittorrent", "sonarr"}))
    assert [(f.outcome, f.instance) for f in findings if f.instance.startswith("sonarr")] == [
        (Outcome.ERROR, "sonarr[main]")
    ]
