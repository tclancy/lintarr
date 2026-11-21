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

import pytest

from lintarr.cli import (
    _fact_to_dict,
    _render_human,
    _render_nested_list,
    _renderable_facts,
    _version_label,
)
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


@pytest.mark.parametrize("version", ["V5.2.4", "VV5.2.4", "vV5.2.4", "vv5.2.4"])
def test_a_capitalised_or_repeated_marker_still_renders_one_lowercase_v(version):
    """Neither shape is reachable today, and both defeat the obvious fix.

    ``version.removeprefix("v")`` — the first fix written here — renders
    ``V5.2.4`` as ``vV5.2.4``: the same double-marker defect in a different
    case, and invisible to a ``"vv" not in output`` assertion. qBittorrent
    sends lowercase, so this is about the function being true to its docstring
    rather than about a live client.
    """
    assert _version_label(version) == "v5.2.4"


def test_a_version_that_is_only_a_marker_is_not_mistaken_for_a_number():
    """``v`` alone leaves nothing behind, and the label says so rather than
    rendering a bare ``v`` that reads like a version."""
    assert _version_label("v") == "v"


def test_an_empty_version_is_passed_through_rather_than_labelled():
    """Unreachable through either adapter — both ``_read_version``
    implementations raise ``bad-response`` on an empty string — so there is no
    version to label and inventing one would be the fabrication
    ``arr._read_version`` already refuses (``str(None)`` -> ``'None'``)."""
    assert _version_label("") == ""


def test_both_services_render_with_exactly_one_v():
    """The defect was only visible with both in one payload.

    Rendering either service alone looks correct — ``vv5.2.4`` reads as a
    quirk of qBittorrent's own string until the ``v4.0.0`` beside it shows the
    renderer is adding one unconditionally. That is why this asserts on the
    pair rather than on either line.
    """
    payload = {
        "clients": [
            {
                "name": "main",
                "kind": "qbittorrent",
                "version": "v5.2.4",
                "queueing_enabled": _fact(True),
            }
        ],
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


# ---------------------------------------------------------------------------
# hardlink-futility: a shared check's "Therefore" line.
# ---------------------------------------------------------------------------


def _cross_mount_fail(instance="qbittorrent[main]"):
    from lintarr.checks.hardlink_futility import CROSS_MOUNT, INVARIANT_ID, PREMISE
    from lintarr.outcomes import Finding, Outcome, Premise

    return Finding(
        invariant=INVARIANT_ID,
        instance=instance,
        outcome=Outcome.FAIL,
        premises=(Premise(PREMISE, True),),
        detail="sonarr[tv]: downloads in /downloads/tv are on mount /downloads, "
        "root folder /tv/shows is on mount /tv",
        conflict=CROSS_MOUNT,
    )


_HARDLINK_THEREFORE = (
    "every import from this client into that library is a full copy, not a hardlink, "
    "so each one doubles its disk use. Put downloads and library under one volume in "
    "the arr's container (TRaSH's `/data` layout), or turn hardlinks off to make the "
    "copying deliberate."
)


@pytest.mark.parametrize("instance", ["qbittorrent[main]", "fake[main]", "nosuch[main]"])
def test_render_shows_the_therefore_line_for_a_cross_mount_fail(instance):
    """A shared check's line is the same whichever backend produced the finding,
    and is found before the backend lookup gives up on an unregistered kind."""
    from lintarr.cli import _render_findings

    rendered = _render_findings([_cross_mount_fail(instance)])
    assert f"  Therefore: {_HARDLINK_THEREFORE}" in rendered
    assert "downloads in /downloads/tv are on mount /downloads" in rendered


def test_a_cross_mount_skip_has_no_therefore_line():
    from dataclasses import replace

    from lintarr.cli import _render_findings
    from lintarr.outcomes import Outcome

    skip = replace(_cross_mount_fail(), outcome=Outcome.SKIP)
    assert "Therefore:" not in _render_findings([skip])


# ---------------------------------------------------------------------------
# orphan-inevitability: one "Therefore" line per conflict, from qBittorrent's table.
# ---------------------------------------------------------------------------

_ORPHAN_THEREFORE = {
    "remove-keeps-files": (
        "when a seed limit is reached, qBittorrent drops the torrent but leaves its files, "
        "and nothing else knows they exist. They stay until someone deletes them by hand. "
        "Set the share-limit action to 'Remove torrent and its files', or use Stop and turn "
        "on the arr's Remove Completed."
    ),
    "nothing-reclaims": (
        "torrents reach their seed limit and then keep their files forever. qBittorrent "
        "stops or super-seeds them, and the arr either isn't set to remove completed "
        "downloads, can't see them after a post-import category, or doesn't recognise the "
        "limit that stopped them. Use 'Remove torrent and its files', or the Stop action "
        "with Remove Completed on, global or indexer seed limits, and no post-import category."
    ),
}


@pytest.mark.parametrize(
    ("conflict", "premise"),
    [
        ("remove-keeps-files", "arr.limit_fires_and_files_stay"),
        ("nothing-reclaims", "arr.limit_fires_and_nothing_reclaims"),
    ],
)
def test_render_shows_each_therefore_line(conflict, premise):
    from lintarr.cli import _render_findings
    from lintarr.outcomes import Finding, Outcome, Premise

    fail = Finding(
        invariant="orphan-inevitability",
        instance="qbittorrent[main]",
        outcome=Outcome.FAIL,
        premises=(Premise(premise, True),),
        detail="sonarr[tv] (category tv): a limit can fire",
        conflict=conflict,
    )
    rendered = _render_findings([fail])
    assert f"  Therefore: {_ORPHAN_THEREFORE[conflict]}" in rendered
    others = [text for name, text in _ORPHAN_THEREFORE.items() if name != conflict]
    assert all(text not in rendered for text in others)


# ---------------------------------------------------------------------------
# #66: the neutral facts and a backend's own facts shared one namespace.
# ---------------------------------------------------------------------------


def _client_with_colliding_facts():
    """One client whose backend reports a field named like a neutral one.

    No registered backend does this today — ``ClientFacts.save_paths`` against
    qBittorrent's ``save_path`` is as close as it gets — so this is built by
    hand rather than collected. The collision is the whole point: under the
    old ``{**common, **instance}`` merge the backend's value won and the
    neutral one left no trace in the output at all.
    """
    return {
        "clients": [
            {
                "name": "main",
                "kind": "fake",
                "version": "1.0.0",
                "common": {"save_paths": _fact({"": "/neutral"}, source="GET /neutral")},
                "save_paths": _fact({"": "/backend"}, source="GET /backend"),
            }
        ],
        "arrs": [],
        "errors": [],
    }


def test_a_backend_fact_sharing_a_neutral_name_does_not_hide_it():
    """Both facts reach the output, each under the name it was collected as.

    Asserting on the values and on both sources, not just on the key: the
    pre-fix renderer still printed one ``save_paths`` line, so a test that
    only looked for the key name passed over the defect.
    """
    out = _render_human(_client_with_colliding_facts())
    assert "'/neutral'" in out
    assert "'/backend'" in out
    assert "GET /neutral" in out
    assert "GET /backend" in out


def test_the_neutral_fact_is_the_one_carrying_the_namespace():
    """``common.save_paths`` is the neutral fact; bare ``save_paths`` is the
    backend's. Prefixing the neutral half is what makes a clash impossible,
    and which half carries the prefix is what tells an operator whose fact it
    is, so it is pinned rather than left to the merge order."""
    lines = _render_human(_client_with_colliding_facts()).splitlines()
    neutral = [line for line in lines if "common.save_paths" in line]
    backend = [line for line in lines if line.strip().startswith("save_paths")]
    assert len(neutral) == 1, lines
    assert len(backend) == 1, lines
    assert "'/neutral'" in neutral[0]
    assert "'/backend'" in backend[0]


def test_the_common_container_is_not_offered_to_the_renderers_as_a_fact():
    """The mapping holds facts only: the ``common`` dict itself is dropped.

    Asserted on the mapping rather than on the output because the output
    cannot see it. The old merge left the whole ``common`` dict in there and
    relied on both render conditions happening to reject a mapping of facts —
    true today, and a stray line the day a neutral field is called ``known``.
    """
    (client,) = _client_with_colliding_facts()["clients"]
    facts = _renderable_facts(client)
    assert "common" not in facts
    assert facts["common.save_paths"]["value"] == {"": "/neutral"}
    assert facts["save_paths"]["value"] == {"": "/backend"}
