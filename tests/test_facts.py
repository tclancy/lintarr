from datetime import UTC, datetime

from lintarr.facts import Known, Unknown, is_known, read


def test_known_carries_provenance():
    f = Known(value=5, source="GET /api/v2/app/preferences",
              read_at=datetime.now(UTC), service_version="v5.2.3")
    assert is_known(f)
    assert f.value == 5


def test_unknown_is_not_known():
    assert not is_known(Unknown(reason="field-absent", detail="max_ratio absent"))


def test_read_present_key_is_known():
    f = read({"max_ratio": 1.5}, "max_ratio", source="GET /prefs", version="v5.2.3")
    assert is_known(f)
    assert f.value == 1.5
    assert f.source == "GET /prefs"


def test_read_present_key_with_null_value_is_known_not_unknown():
    """A genuinely-read None must be distinguishable from an unread field."""
    f = read({"max_ratio": None}, "max_ratio", source="GET /prefs", version="v5.2.3")
    assert is_known(f)
    assert f.value is None


def test_read_absent_key_is_unknown_field_absent():
    f = read({}, "max_ratio", source="GET /prefs", version="v5.2.3")
    assert not is_known(f)
    assert f.reason == "field-absent"
    assert "max_ratio" in f.detail
