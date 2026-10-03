"""Human-mode rendering, at the level of the renderers themselves.

Two of issue #6's residuals are here. Both are about ``_render_human`` and
neither is reachable from an end-to-end ``dump-facts`` run, which is why they
had gone unasserted: the version defect needed *two* services rendered side by
side to be visible as a defect rather than a style, and the nested-list
identity branch cannot be reached at all through the current model.

Calling the private renderers directly is deliberate. A branch that no
collectable snapshot can reach has no public entry point, and the alternative
to testing it here is leaving it untested until the day it goes live — which
is exactly the state #6 recorded it in.
"""

from datetime import UTC, datetime

from lintarr.cli import _fact_to_dict, _render_human, _render_nested_list, _version_label
from lintarr.facts import Known


def _fact(value, source="GET /x"):
    return {"known": True, "value": value, "source": source, "read_at": "", "service_version": "v1"}


def _unknown(reason="field-absent"):
    return {"known": False, "reason": reason, "detail": ""}


# ---------------------------------------------------------------------------
# Residual: human mode printed ``qbittorrent[main] vv5.2.3``.
# ---------------------------------------------------------------------------


def test_a_version_that_already_carries_a_v_is_not_given_a_second_one():
    """qBittorrent's ``/api/v2/app/version`` answers ``v5.2.4``, ``v`` included."""
    assert _version_label("v5.2.4") == "v5.2.4"


def test_a_bare_version_is_given_one():
    """An arr's ``/api/v3/system/status`` answers ``4.0.0``, bare."""
    assert _version_label("4.0.0") == "v4.0.0"


def test_both_services_render_with_exactly_one_v():
    """The defect was only visible with both in one payload.

    Rendering either service alone looks correct — ``vv5.2.4`` reads as a
    quirk of qBittorrent's own string until the ``v4.0.0`` beside it shows the
    renderer is adding one unconditionally. That is why this asserts on the
    pair rather than on either line.
    """
    payload = {
        "qbits": [{"name": "main", "version": "v5.2.4", "queueing_enabled": _fact(True)}],
        "arrs": [
            {"name": "main", "kind": "sonarr", "version": "4.0.0", "indexers": []},
        ],
        "errors": [],
    }
    out = _render_human(payload)
    assert "qbittorrent[main] v5.2.4" in out
    assert "sonarr[main] v4.0.0" in out
    assert "vv" not in out


def test_the_version_is_not_trimmed_in_the_json_payload():
    """Only the display normalises. The fact layer keeps what the service said.

    ``Known.service_version`` is what version-ranged axioms are matched on, so
    an adapter or serialiser that trimmed the ``v`` would silently move every
    such range. The renderer is the only place that may touch it.
    """
    read_at = datetime(2026, 10, 3, 5, 0, tzinfo=UTC)
    known = Known(value=True, source="GET /x", read_at=read_at, service_version="v5.2.4")
    assert _fact_to_dict(known)["service_version"] == "v5.2.4"


# ---------------------------------------------------------------------------
# Residual: ``_render_nested_list``'s identity parenthetical is unreachable.
# ---------------------------------------------------------------------------


def test_a_nested_item_with_no_plain_fields_renders_no_parenthetical():
    """The live case. ``IndexerFacts.name`` is its only non-Fact field.

    Once ``protocol`` became a ``Fact`` there was nothing left for the
    parenthetical to carry, so every real indexer renders bare. Pinned so that
    a future plain field showing up in the output is a deliberate change and
    not a surprise.
    """
    lines = _render_nested_list(
        "indexers",
        [{"name": "1337x", "protocol": _fact("torrent"), "seed_ratio": _unknown()}],
        indent="    ",
    )
    assert lines[0] == "    indexers:"
    assert lines[1] == "      1337x"
    assert "(" not in lines[1]


def test_a_nested_item_with_a_plain_field_renders_it_in_the_parenthetical():
    """The branch #6 recorded as dead code, exercised directly.

    It is kept rather than deleted because the condition that made it dead is a
    property of today's model, not of the renderer: a second nested dataclass
    with a plain field brings it straight back. Deleting it would be fine; what
    is not fine is the state #6 found it in — present, unreachable, and with no
    test to say what it does on the day it stops being unreachable.
    """
    lines = _render_nested_list(
        "indexers",
        [{"name": "1337x", "id": 7, "implementation": "Torznab", "seed_ratio": _unknown()}],
        indent="    ",
    )
    assert lines[1] == "      1337x  (id=7, implementation='Torznab')"


def test_the_parenthetical_excludes_facts_and_nested_lists():
    """Only plain scalars identify an item; a Fact gets its own indented line.

    The three exclusions are one expression in the renderer, so a test that
    varied only one of them would leave the other two unpinned.
    """
    lines = _render_nested_list(
        "indexers",
        [
            {
                "name": "1337x",
                "id": 7,
                "protocol": _fact("torrent"),
                "tags": [1, 2],
            }
        ],
        indent="    ",
    )
    assert lines[1] == "      1337x  (id=7)"
    assert any("protocol" in line and "torrent" in line for line in lines[2:])
