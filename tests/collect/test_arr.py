import httpx
import pytest

from lintarr.collect.arr import _READ_FIELD_NAMES, _SEED_FIELDS, collect_arr
from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.config import ArrConfig
from lintarr.facts import is_known

CFG = ArrConfig(name="main", kind="sonarr", url="http://sonarr", api_key="k")

_UNSET = object()


def _field(name, value=_UNSET, **extra):
    """One entry of an arr indexer's ``fields`` list, shaped like the real thing.

    A live Sonarr/Radarr field carries ``order``/``label``/``type``/``advanced``
    alongside ``name``, and — crucially — omits ``value`` entirely when the
    setting has never been configured. Fixtures that always supply ``value``
    are what let a bug collapsing "no value key" into ``Known(None)`` hide.
    Pass no *value* to reproduce that real omission.
    """
    entry = {"name": name, "order": 0, "label": name, "type": "textbox", "advanced": True}
    entry.update(extra)
    if value is not _UNSET:
        entry["value"] = value
    return entry


def _indexer(name, *, protocol="torrent", fields=None, enable_keys=True):
    """Build a raw indexer payload.

    ``enable_keys=True``/``False`` includes all three top-level ``enable*``
    keys set to that value; ``enable_keys=None`` omits them entirely
    (simulating an arr version that doesn't expose them); a dict merges
    specific overrides on top of an all-``True`` default.
    """
    default_fields = [
        _field("minimumSeeders", 1, type="number", advanced=False),
        _field("seedCriteria.seedRatio", None, type="number"),
        _field("seedCriteria.seedTime", None, type="number"),
        _field("seedCriteria.seasonPackSeedTime", None, type="number"),
    ]
    indexer = {
        "id": 1,
        "name": name,
        "protocol": protocol,
        "implementation": "Torznab",
        "priority": 25,
        "fields": default_fields if fields is None else fields,
    }
    if enable_keys is None:
        return indexer
    base = {"enableRss": True, "enableAutomaticSearch": True, "enableInteractiveSearch": True}
    if isinstance(enable_keys, dict):
        base.update(enable_keys)
    else:
        base = dict.fromkeys(base, bool(enable_keys))
    indexer.update(base)
    return indexer


def _collect(indexers, version="4.0.15.2941", status=_UNSET):
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/api/v3/system/status":
                body = {"version": version} if status is _UNSET else status
                return httpx.Response(200, json=body)
            case "/api/v3/indexer":
                return httpx.Response(200, json=indexers)
            case other:
                return httpx.Response(404, text=other)

    client = ReadOnlyClient("http://sonarr", transport=httpx.MockTransport(handle))
    return collect_arr(client, CFG), client


def test_reads_version():
    arr, _ = _collect([])
    assert arr.version == "4.0.15.2941"


def test_unset_seed_ratio_is_known_none_not_unknown():
    """Field present with a null value means configured-but-unset."""
    arr, _ = _collect([_indexer("1337x")])
    ratio = arr.indexers[0].seed_ratio
    assert is_known(ratio)
    assert ratio.value is None


def test_set_seed_ratio_is_read():
    arr, _ = _collect([_indexer("EZTV", fields=[_field("seedCriteria.seedRatio", 2.0)])])
    assert arr.indexers[0].seed_ratio.value == 2.0


def test_missing_seed_field_entirely_is_unknown():
    arr, _ = _collect([_indexer("Old", fields=[_field("minimumSeeders", 1)])])
    assert not is_known(arr.indexers[0].seed_ratio)
    assert arr.indexers[0].seed_ratio.reason == "field-absent"


def test_field_entry_without_a_value_key_is_unknown_not_known_none():
    """A field listed with no ``value`` key was never read — it is not a null.

    This is the same shape as the ``enable`` defaulting bug: collapsing it to
    ``Known(None)`` makes an unread setting indistinguishable from one the
    user deliberately cleared, on the exact field the flagship check reads.
    """
    arr, _ = _collect([_indexer("NoValue", fields=[_field("seedCriteria.seedRatio")])])
    ratio = arr.indexers[0].seed_ratio
    assert not is_known(ratio)
    assert ratio.reason == "field-absent"


def test_field_entry_with_an_explicit_null_value_is_known_none():
    """The companion shape: ``value: null`` means configured-but-unset."""
    arr, _ = _collect([_indexer("Null", fields=[_field("seedCriteria.seedRatio", None)])])
    ratio = arr.indexers[0].seed_ratio
    assert is_known(ratio)
    assert ratio.value is None


@pytest.mark.parametrize("value", ["2.0", "not-a-number", {"a": 1}, [], False], ids=repr)
def test_a_seed_criterion_is_read_verbatim_however_unusable_its_value(value):
    """Collection does not coerce, drop or judge a value — the predicate does.

    This is what makes #10 reachable rather than theoretical. Any JSON value can
    land in this slot — a plugin, a future API revision, a reverse proxy
    answering 200 with its own body — and it arrives at the invariant as a
    ``Known`` whose type is the only thing distinguishing it from a real ratio.
    So the guard belongs at the point of use, beside ``_as_limit``, and this
    test pins that collection deliberately hands the value over untouched
    rather than silently cleaning it up here.
    """
    arr, _ = _collect([_indexer("Junk", fields=[_field("seedCriteria.seedRatio", value)])])
    ratio = arr.indexers[0].seed_ratio
    assert is_known(ratio)
    assert ratio.value == value
    # ``False == 0`` and ``0`` would be a legitimate goal, so the type is part
    # of the claim, not decoration.
    assert type(ratio.value) is type(value)


def test_usenet_indexer_is_kept_with_its_protocol():
    arr, _ = _collect([_indexer("News", protocol="usenet")])
    idx = arr.indexers[0]
    assert idx.name == "News"
    assert is_known(idx.protocol)
    assert idx.protocol.value == "usenet"


def test_missing_protocol_is_unknown_not_empty_string():
    """An unread protocol must not silently classify an indexer as not-a-torrent."""
    raw = _indexer("NoProto")
    del raw["protocol"]
    arr, _ = _collect([raw])
    protocol = arr.indexers[0].protocol
    assert not is_known(protocol)
    assert protocol.reason == "field-absent"


@pytest.mark.parametrize("name", [None, "", "   "])
def test_indexer_without_a_usable_name_is_bad_response(name):
    raw = _indexer("placeholder")
    if name is None:
        del raw["name"]
    else:
        raw["name"] = name
    with pytest.raises(ServiceError) as e:
        _collect([raw])
    assert e.value.kind == "bad-response"


@pytest.mark.parametrize("status", [{}, {"version": None}, {"version": ""}, {"version": "  "}])
def test_missing_or_null_version_is_bad_response(status):
    """A fabricated version ('' or the literal 'None') would be stamped on every fact."""
    with pytest.raises(ServiceError) as e:
        _collect([], status=status)
    assert e.value.kind == "bad-response"


@pytest.mark.parametrize("status", [["not", "a", "dict"], "a string", 7])
def test_non_object_status_payload_is_bad_response(status):
    with pytest.raises(ServiceError) as e:
        _collect([], status=status)
    assert e.value.kind == "bad-response"


@pytest.mark.parametrize("payload", [{"message": "Unauthorized"}, ["a string"], "nope"])
def test_non_list_of_objects_indexer_payload_is_bad_response(payload):
    """A 200 carrying an error object must not raise AttributeError out of the adapter."""
    with pytest.raises(ServiceError) as e:
        _collect(payload)
    assert e.value.kind == "bad-response"


def test_enable_fields_are_known_when_present():
    """Real, independent values for the three enable toggles — never collapsed."""
    arr, _ = _collect(
        [_indexer("Off", enable_keys={"enableRss": False, "enableAutomaticSearch": False})]
    )
    idx = arr.indexers[0]
    assert is_known(idx.enable_rss)
    assert idx.enable_rss.value is False
    assert is_known(idx.enable_automatic_search)
    assert idx.enable_automatic_search.value is False
    assert is_known(idx.enable_interactive_search)
    assert idx.enable_interactive_search.value is True


def test_missing_enable_fields_are_unknown_not_false():
    """An indexer with no enable* keys must not be silently read as disabled."""
    arr, _ = _collect([_indexer("Old", enable_keys=None)])
    idx = arr.indexers[0]
    assert not is_known(idx.enable_rss)
    assert idx.enable_rss.reason == "field-absent"
    assert not is_known(idx.enable_automatic_search)
    assert idx.enable_automatic_search.reason == "field-absent"
    assert not is_known(idx.enable_interactive_search)
    assert idx.enable_interactive_search.reason == "field-absent"


def test_issues_only_get_requests():
    _, client = _collect([])
    assert set(client.methods_used) == {"GET"}


@pytest.mark.parametrize("name", [None, "", "   "])
def test_fields_entry_without_a_usable_name_is_bad_response(name):
    """A nameless ``fields`` entry is a malformed payload, not an unnamed field.

    The three parametrisations were unguarded in *different* ways, measured
    against the pre-fix code rather than assumed:

    - a **missing** ``name`` key raised a bare ``KeyError`` out of the adapter,
      past ``collect_stack``'s per-instance ``except ServiceError``, killing
      every other service's facts and printing a traceback;
    - ``""`` and ``"   "`` raised **nothing at all**. The mapping was keyed on
      the empty or blank string, no real field name resolved, and the seed
      criteria came back ``Unknown("field-absent")`` — the instance reported
      as healthy on a payload whose field names were unreadable.

    The silent pair is the more dangerous one, and it is the reason this guard
    rejects a blank name rather than only a missing key.
    """
    entry = _field("seedCriteria.seedRatio", 1.0)
    if name is None:
        del entry["name"]
    else:
        entry["name"] = name
    with pytest.raises(ServiceError) as e:
        _collect([_indexer("Nameless", fields=[entry])])
    assert e.value.kind == "bad-response"


def test_nameless_fields_entry_is_bad_response_even_with_no_value_key():
    """Every entry's name is validated, not just the ones carrying a ``value``.

    ``value`` is legitimately absent on any never-configured setting, so a
    guard evaluated only for entries that have one leaves the commonest live
    shape unchecked — and admits a payload whose field names are unreadable
    while reporting the stack as healthy.
    """
    entry = _field("seedCriteria.seedRatio")
    del entry["name"]
    with pytest.raises(ServiceError) as e:
        _collect([_indexer("Nameless", fields=[entry])])
    assert e.value.kind == "bad-response"


@pytest.mark.parametrize(
    "fields",
    [
        {"seedCriteria.seedRatio": 1.0},  # object, not array — iterates as keys
        "seedCriteria.seedRatio",  # string — iterates as characters
        None,  # explicit null
        7,
        ["seedCriteria.seedRatio"],  # array of strings, not objects
        [7],
    ],
)
def test_non_list_of_objects_fields_payload_is_bad_response(fields):
    """``fields`` must be an array of objects; every other shape is a bad response.

    Three of these shapes are the dangerous ones: iterating a string, a dict or
    a list of strings yields strings, and ``"value" in "some string"`` is a
    *substring* test rather than a key test. It answers ``False``, so every
    entry is filtered out, the mapping comes back empty, and the seed facts
    become ``Unknown("field-absent")`` — a malformed payload reported as "this
    arr version does not expose seed criteria", which is exactly the defaulted
    fact masquerading as a real one that this project exists to refuse.

    The key is assigned directly rather than passed to ``_indexer``, because
    ``_indexer``'s *fields* parameter treats ``None`` as "use the default
    fields" — routing the explicit-null case through it tests the healthy
    payload under a malformed label.
    """
    raw = _indexer("Odd")
    raw["fields"] = fields
    with pytest.raises(ServiceError) as e:
        _collect([raw])
    assert e.value.kind == "bad-response"


def test_padded_field_name_still_resolves_its_value():
    """The name is a lookup key, so the guard must return it stripped.

    Validating with ``.strip()`` and returning the padded original would key
    the mapping on ``"  seedCriteria.seedRatio  "``, and a ratio the operator
    really had configured would come back ``Unknown("field-absent")`` — the
    guard re-introducing the exact lie it exists to refuse.
    """
    arr, _ = _collect([_indexer("Padded", fields=[_field("  seedCriteria.seedRatio  ", 2.0)])])
    ratio = arr.indexers[0].seed_ratio
    assert is_known(ratio)
    assert ratio.value == 2.0


def test_absent_fields_key_is_field_absent_not_bad_response():
    """The companion to the explicit-null case: a *missing* ``fields`` key is not malformed.

    Absence matches how the three top-level ``enable*`` keys behave on an
    older arr, so it falls through to ``read()``'s absent branch rather than
    erroring the whole instance.
    """
    raw = _indexer("NoFields")
    del raw["fields"]
    arr, _ = _collect([raw])
    ratio = arr.indexers[0].seed_ratio
    assert not is_known(ratio)
    assert ratio.reason == "field-absent"


# --- duplicate field names (#6's last P0a residual) ---


def test_duplicate_field_names_with_conflicting_values_is_bad_response():
    """Two values for one name is a payload nobody can read, so it must not be guessed.

    Recorded on #6 as the reproduction `[{"name": "seedCriteria.seedRatio",
    "value": 1.0}, {"name": "seedCriteria.seedRatio", "value": 9.0}]` yielding
    `Known(9.0)` — last-win, silently. A ratio of 9.0 and a ratio of 1.0 are
    different operator intents and the collector has no way to tell which was
    configured, so admitting either is the fabrication this module's other
    guards exist to refuse. It reads as *more* trustworthy than
    `Unknown("field-absent")` precisely because it is `Known`.
    """
    dupes = [
        _field("seedCriteria.seedRatio", 1.0),
        _field("seedCriteria.seedRatio", 9.0),
    ]
    with pytest.raises(ServiceError) as e:
        _collect([_indexer("Dupe", fields=dupes)])
    assert e.value.kind == "bad-response"
    assert "seedCriteria.seedRatio" in e.value.detail


def test_a_null_duplicate_conflicting_with_a_set_one_is_bad_response():
    """The worst pair of the lot, because both readings are meaningful.

    `Known(None)` is this collector's "configured but unset" and `Known(2.0)`
    is "set to 2.0" — opposite answers to the question the seed-criteria axiom
    asks. Last-win would decide it on list order.
    """
    dupes = [
        _field("seedCriteria.seedRatio", None),
        _field("seedCriteria.seedRatio", 2.0),
    ]
    with pytest.raises(ServiceError) as e:
        _collect([_indexer("Dupe", fields=dupes)])
    assert e.value.kind == "bad-response"


def test_duplicate_field_names_that_agree_are_read_not_rejected():
    """A repeated name carrying one value is unambiguous, so it stays readable.

    Deliberately narrower than "all duplicates are malformed": first-win and
    last-win return the same answer here, so there is nothing for the collector
    to be wrong about. Rejecting it would turn a readable payload into an ERROR
    for a shape that costs nothing to read, and no measurement says a real arr
    never repeats a field.
    """
    dupes = [
        _field("seedCriteria.seedRatio", 2.0),
        _field("seedCriteria.seedRatio", 2.0),
    ]
    arr, _ = _collect([_indexer("Agree", fields=dupes)])
    ratio = arr.indexers[0].seed_ratio
    assert is_known(ratio)
    assert ratio.value == 2.0


def test_agreeing_null_duplicates_stay_known_none():
    # The agreeing case at the value that is easiest to confuse with absence:
    # `None` must still come back `Known(None)`, not `Unknown("field-absent")`.
    dupes = [
        _field("seedCriteria.seedRatio", None),
        _field("seedCriteria.seedRatio", None),
    ]
    arr, _ = _collect([_indexer("AgreeNull", fields=dupes)])
    ratio = arr.indexers[0].seed_ratio
    assert is_known(ratio)
    assert ratio.value is None


@pytest.mark.parametrize("valued_first", [True, False])
def test_an_entry_without_a_value_key_never_conflicts_with_one_that_has_it(valued_first):
    """A missing `value` is "never configured", not a competing value.

    Both orders, because the two are not symmetric under the old last-win rule
    and a guard that only looked at adjacent pairs could pass one and fail the
    other. The entry carrying a value is the only one that says anything about
    what the operator set, so it wins without that being a guess.
    """
    valued = _field("seedCriteria.seedRatio", 2.0)
    bare = _field("seedCriteria.seedRatio")
    fields = [valued, bare] if valued_first else [bare, valued]
    arr, _ = _collect([_indexer("Mixed", fields=fields)])
    assert arr.indexers[0].seed_ratio.value == 2.0


def test_names_differing_only_by_padding_are_the_same_field_for_conflict_purposes():
    """The dedup key is the *stripped* name, as `_field_name` already returns.

    `_field_name` strips so that a padded name does not become an unfindable
    key. That makes `"  seedCriteria.seedRatio  "` and `"seedCriteria.seedRatio"`
    one field, so two different values across them is the same unreadable
    payload as any other duplicate — checking before stripping would let it
    through.
    """
    dupes = [
        _field("seedCriteria.seedRatio", 1.0),
        _field("  seedCriteria.seedRatio  ", 9.0),
    ]
    with pytest.raises(ServiceError) as e:
        _collect([_indexer("Padded", fields=dupes)])
    assert e.value.kind == "bad-response"


def test_distinct_names_are_not_treated_as_duplicates():
    # The reachability control for every assertion above: an ordinary
    # multi-field payload must still collect. Without it, a guard that rejected
    # *all* repeated reads would pass the six tests above and break every real
    # indexer.
    fields = [
        _field("minimumSeeders", 3, type="number", advanced=False),
        _field("seedCriteria.seedRatio", 1.5, type="number"),
    ]
    arr, _ = _collect([_indexer("Normal", fields=fields)])
    assert arr.indexers[0].seed_ratio.value == 1.5


def test_a_conflict_is_found_when_only_the_last_of_three_duplicates_disagrees():
    """Three entries, two agreeing — the case that separates `any` from `all`.

    With two entries a "do they all differ from the first?" check and a "does
    any differ?" check give the same verdict, so the pairwise tests above cannot
    tell a correct guard from one that only fires when *every* duplicate
    disagrees. This payload is a conflict under the right rule and readable
    under the wrong one, which would return 1.0 — silently, and by position
    again.
    """
    dupes = [
        _field("seedCriteria.seedRatio", 1.0),
        _field("seedCriteria.seedRatio", 1.0),
        _field("seedCriteria.seedRatio", 9.0),
    ]
    with pytest.raises(ServiceError) as e:
        _collect([_indexer("Three", fields=dupes)])
    assert e.value.kind == "bad-response"


@pytest.mark.parametrize("order", [(0, False), (False, 0), (1, True), (True, 1)])
def test_values_that_are_equal_but_not_the_same_fact_are_a_conflict(order):
    """`False == 0` in Python, and the invariants layer draws its line in that gap.

    `_is_a_seed_goal` excludes `bool` explicitly — `isinstance(True, int)` —
    while `0` reads as a goal of zero. So `[0, False]` and `[False, 0]` are
    opposite verdicts on the flagship premise, decided by list order, which is
    the defect this whole guard exists to remove. A plain `!=` comparison called
    these pairs *agreeing* and returned whichever came first.

    Both orders of both pairs, because an `==`-only guard passes all four
    identically and a `return values[-1]` mutant is invisible without them.
    """
    dupes = [
        _field("seedCriteria.seedRatio", order[0]),
        _field("seedCriteria.seedRatio", order[1]),
    ]
    with pytest.raises(ServiceError) as e:
        _collect([_indexer("BoolInt", fields=dupes)])
    assert e.value.kind == "bad-response"


@pytest.mark.parametrize("order", [(2, 2.0), (2.0, 2)])
def test_an_int_and_an_equal_float_are_one_fact_not_a_conflict(order):
    """The other side of the same line, and why this tests bool-ness not `type()`.

    `_is_a_seed_goal` admits `int` and `float` alike, so `2` and `2.0` cannot
    produce different verdicts and splitting them would raise a conflict over a
    payload nobody could misread. A `type(a) is not type(b)` guard — the obvious
    way to fix the bool case — fails exactly here.
    """
    dupes = [
        _field("seedCriteria.seedRatio", order[0]),
        _field("seedCriteria.seedRatio", order[1]),
    ]
    arr, _ = _collect([_indexer("IntFloat", fields=dupes)])
    assert arr.indexers[0].seed_ratio.value == 2


def test_a_conflict_in_a_field_lintarr_never_reads_does_not_discard_the_instance():
    """Scope: the guard covers the names read out of the mapping, and no others.

    `_fields_as_mapping` resolves every name, so an unscoped guard fired on
    `categories` too — discarding a perfectly readable `seedRatio`, and with it
    every indexer on that instance, over ambiguity in data this collector does
    not consume and cannot report on. That trades a certain loss of facts for a
    misreading that cannot happen.
    """
    fields = [
        _field("categories", [5000], type="select"),
        _field("categories", [5030], type="select"),
        _field("seedCriteria.seedRatio", 2.0, type="number"),
    ]
    arr, _ = _collect([_indexer("Unread", fields=fields)])
    assert arr.indexers[0].seed_ratio.value == 2.0


def test_the_guarded_names_are_derived_from_the_fields_actually_read():
    """`_READ_FIELD_NAMES` must stay derived, not become a second hand-kept list.

    If it is ever restated as a literal, adding a seed criterion to
    `_SEED_FIELDS` silently leaves the new field on last-win — the original bug,
    reintroduced for one field only and invisible to every test above. The
    non-empty assertion is the reachability control: `frozenset()` would satisfy
    a subset check and guard nothing.
    """
    assert _READ_FIELD_NAMES == frozenset(_SEED_FIELDS.values())
    assert len(_READ_FIELD_NAMES) == len(_SEED_FIELDS) >= 3


def test_duplicates_in_one_indexer_do_not_conflict_with_another_indexers_fields():
    """Grouping is per-indexer, as `_indexer_facts` calls it.

    Two indexers legitimately disagree about their own seed ratios — that is the
    normal case, not a malformed payload. Pinned because hoisting the grouping to
    the payload level to "do it once" would break every multi-indexer stack while
    leaving the rest of this file green.
    """
    indexers = [
        _indexer("A", fields=[_field("seedCriteria.seedRatio", 1.0)]),
        _indexer("B", fields=[_field("seedCriteria.seedRatio", 9.0)]),
    ]
    arr, _ = _collect(indexers)
    assert [i.seed_ratio.value for i in arr.indexers] == [1.0, 9.0]


def test_an_unreadable_name_is_reported_before_a_duplicate_conflict():
    """Name validation still runs over every entry before any value is compared.

    `_fields_as_mapping` drains its loop before the output comprehension, so a
    nameless entry raises even when a duplicate conflict sits earlier in the
    list. Pinned because folding the loop back into a comprehension would invert
    that order silently, and the two errors send an operator to different places.
    """
    nameless = _field("seedCriteria.seedTime", 10)
    del nameless["name"]
    fields = [
        _field("seedCriteria.seedRatio", 1.0),
        _field("seedCriteria.seedRatio", 9.0),
        nameless,
    ]
    with pytest.raises(ServiceError) as e:
        _collect([_indexer("Both", fields=fields)])
    assert "no 'name' string" in e.value.detail
