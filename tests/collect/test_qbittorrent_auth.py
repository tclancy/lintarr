# Measured against a live qBittorrent 5.2.3 instance on 2026-08-26:
#
#   correct password:    HTTP 204, empty body
#   wrong password:      HTTP 401, body "Unauthorized"
#   unauthenticated GET: HTTP 403, body "Forbidden"
#
# These fixtures encode that observation, not the documented protocol
# (HTTP 200 "Ok."/"Fails.") the implementation originally assumed — see
# issue #7. The 200-based fixtures below cover the older, documented
# protocol generation, which some releases still speak.

import httpx
import pytest

from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.collect.qbittorrent import AUTH_PATH, authenticate
from lintarr.config import QbtConfig

CFG = QbtConfig(name="main", url="http://qbt", username="admin", password="pw")


def _client(handler) -> ReadOnlyClient:
    return ReadOnlyClient("http://qbt", transport=httpx.MockTransport(handler), auth_path=AUTH_PATH)


def test_204_empty_body_authenticates():
    """The real qBittorrent 5.2.3 success response: 204, no body at all."""
    c = _client(lambda r: httpx.Response(204))
    authenticate(c, CFG)
    assert c.methods_used == ("POST",)


def test_ok_body_authenticates():
    """Legacy protocol generation: 200 with body 'Ok.'."""
    c = _client(lambda r: httpx.Response(200, text="Ok."))
    authenticate(c, CFG)
    assert c.methods_used == ("POST",)


def test_401_unauthorized_is_unauthorised():
    """The real qBittorrent 5.2.3 bad-credentials response: 401 'Unauthorized'."""
    c = _client(lambda r: httpx.Response(401, text="Unauthorized"))
    with pytest.raises(ServiceError) as e:
        authenticate(c, CFG)
    assert e.value.kind == "unauthorised"


def test_fails_body_is_unauthorised_not_banned():
    """Legacy protocol generation: 200 with body 'Fails.'."""
    c = _client(lambda r: httpx.Response(200, text="Fails."))
    with pytest.raises(ServiceError) as e:
        authenticate(c, CFG)
    assert e.value.kind == "unauthorised"


def test_forbidden_is_unauthorised_not_a_guessed_ban():
    """403 is reported as unauthorised, not banned.

    The real ban response shape has never been measured against a live
    instance (issue #7) — guessing one mapping already produced the exact
    defect this fixes (a wrong password reported as an hour-long ban that
    did not exist). The message must not claim a ban is in effect.
    """
    c = _client(lambda r: httpx.Response(403, text="Forbidden"))
    with pytest.raises(ServiceError) as e:
        authenticate(c, CFG)
    assert e.value.kind == "unauthorised"
    assert "banned" not in str(e.value)


def test_exactly_one_login_attempt_is_made_on_failure():
    """Retrying is what triggers qBittorrent's IP ban, so there must be no retry."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="Fails.")

    with pytest.raises(ServiceError):
        authenticate(_client(handler), CFG)
    assert len(calls) == 1


def test_password_absent_from_error_message():
    c = _client(lambda r: httpx.Response(200, text="Fails."))
    with pytest.raises(ServiceError) as e:
        authenticate(c, CFG)
    assert "pw" not in str(e.value)
