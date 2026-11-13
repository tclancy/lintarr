"""R2: the harness image is pinned to what homelab runs."""

from tests.conformance.image import QBT_IMAGE


def test_pin_matches_homelab():
    assert QBT_IMAGE.endswith(":5.2.3_v2.0.14-ls476")
