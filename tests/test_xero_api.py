import json
import urllib.parse

import pytest

from cardcoding import xero_api
from cardcoding.xero_api import XeroClient, XeroError


class Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw))
        return self.responses.pop(0)


def make(tmp_path, responses, now=1_000_000.0):
    http = FakeHTTP(responses)
    client = XeroClient("demo-client-id", "demo-client-pw", "http://localhost:8765/callback",
                        tmp_path / "tokens.json", http=http, clock=lambda: now)
    return client, http


def write_tokens(client, **kw):
    tokens = {"access_token": "old-access", "refresh_token": "old-refresh", "tenant_id": "tenant-demo",
              "tenant_name": "E100 Demo Co", "expires_at": 0}
    tokens.update(kw)
    client.token_file.write_text(json.dumps(tokens))


def test_valid_token_is_not_refreshed(tmp_path):
    client, http = make(tmp_path, [Resp(200, {"Contacts": []})])
    write_tokens(client, expires_at=2_000_000.0)
    client.call("GET", "Contacts")
    assert len(http.requests) == 1
    _, url, kw = http.requests[0]
    assert url.endswith("/api.xro/2.0/Contacts")
    assert kw["headers"]["Xero-tenant-id"] == "tenant-demo"


def test_expired_token_refreshes_and_saves_rotated_refresh_token(tmp_path):
    client, http = make(tmp_path, [
        Resp(200, {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 1800}),
        Resp(200, {"Contacts": []}),
    ])
    write_tokens(client, expires_at=999_000.0)
    client.call("GET", "Contacts")
    method, url, kw = http.requests[0]
    assert (method, url) == ("POST", xero_api.TOKEN_URL)
    assert kw["data"] == {"grant_type": "refresh_token", "refresh_token": "old-refresh"}
    assert kw["headers"]["Authorization"].startswith("Basic ")
    saved = json.loads(client.token_file.read_text())
    assert saved["refresh_token"] == "new-refresh"  # rotated token persisted
    assert saved["tenant_id"] == "tenant-demo"      # tenant carried forward
    assert saved["expires_at"] == 1_000_000.0 + 1800 - 60
    assert http.requests[1][2]["headers"]["Authorization"] == "Bearer new-access"


def test_failed_refresh_raises(tmp_path):
    client, _ = make(tmp_path, [Resp(400, {"error": "invalid_grant"})])
    write_tokens(client)
    with pytest.raises(XeroError, match="refresh failed"):
        client.call("GET", "Contacts")


def test_missing_tokens_tells_user_to_auth(tmp_path):
    client, _ = make(tmp_path, [])
    with pytest.raises(XeroError, match="auth"):
        client.tokens()


def test_api_error_raises(tmp_path):
    client, _ = make(tmp_path, [Resp(400, {"Message": "bad"})])
    write_tokens(client, expires_at=2_000_000.0)
    with pytest.raises(XeroError, match="400"):
        client.call("PUT", "BankTransactions", body={})


def test_authorize_url_has_scopes_and_state(tmp_path):
    client, _ = make(tmp_path, [])
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(client.authorize_url("abc123")).query)
    assert qs["state"] == ["abc123"] and qs["response_type"] == ["code"]
    scopes = qs["scope"][0].split()
    assert "offline_access" in scopes and "accounting.banktransactions" in scopes


def test_code_exchange_picks_organisation_tenant(tmp_path):
    client, _ = make(tmp_path, [
        Resp(200, {"access_token": "a", "refresh_token": "r", "expires_in": 1800}),
        Resp(200, [{"tenantType": "PRACTICE", "tenantId": "p1", "tenantName": "x"},
                   {"tenantType": "ORGANISATION", "tenantId": "org-demo", "tenantName": "E100 Demo Co"}]),
    ])
    tokens = client.exchange_code("code-demo")
    assert tokens["tenant_id"] == "org-demo"
    assert json.loads(client.token_file.read_text())["tenant_name"] == "E100 Demo Co"


def test_from_env_requires_variables(monkeypatch):
    for k in ("XERO_CLIENT_ID", "XERO_CLIENT_SECRET", "XERO_REDIRECT_URI"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(XeroError, match="XERO_CLIENT_ID"):
        XeroClient.from_env(http=FakeHTTP([]))
