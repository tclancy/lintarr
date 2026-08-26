import httpx
import pytest

from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.collect.qbittorrent import AUTH_PATH, authenticate
from lintarr.config import QbtConfig

CFG = QbtConfig(name="main", url="http://qbt", username="admin", password="pw")


def _client(handler) -> ReadOnlyClient:
    return ReadOnlyClient("http://qbt", transport=httpx.MockTransport(handler), auth_path=AUTH_PATH)


def test_ok_body_authenticates():
    c = _client(lambda r: httpx.Response(200, text="Ok."))
    authenticate(c, CFG)
    assert c.methods_used == ("POST",)


def test_fails_body_is_unauthorised_not_banned():
    c = _client(lambda r: httpx.Response(200, text="Fails."))
    with pytest.raises(ServiceError) as e:
        authenticate(c, CFG)
    assert e.value.kind == "unauthorised"


def test_forbidden_is_reported_as_banned():
    """403 on login means the IP is banned, which is a different fix to bad creds."""
    c = _client(lambda r: httpx.Response(403, text="banned"))
    with pytest.raises(ServiceError) as e:
        authenticate(c, CFG)
    assert e.value.kind == "banned"


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
