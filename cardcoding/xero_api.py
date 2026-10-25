"""Minimal Xero Accounting API connector.

OAuth2 authorization-code flow with a local callback, granular scopes, and
automatic access-token refresh. Xero rotates the refresh token on every
refresh, so the new token pair is always written back to the token file.

    py -m cardcoding.xero_api auth                       one-time browser approval
    py -m cardcoding.xero_api whoami                     show the connected organisation
    py -m cardcoding.xero_api get <Endpoint> [k=v ...]   e.g.  get Contacts  or  get Invoices Statuses=DRAFT
    py -m cardcoding.xero_api post <Endpoint> <file>     create-or-update from a JSON file
    py -m cardcoding.xero_api put <Endpoint> <file>      create from a JSON file

Configuration comes only from environment variables (see .env.example).
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

AUTH_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
API_BASE = "https://api.xero.com/api.xro/2.0/"

# Granular scopes (Xero apps created after March 2026 receive these instead of
# the broad accounting.transactions scope). Request only what the tool needs.
SCOPES = " ".join([
    "offline_access", "openid", "profile", "email",
    "accounting.settings",
    "accounting.contacts",
    "accounting.banktransactions",
])

REFRESH_MARGIN_SECONDS = 60


class XeroError(RuntimeError):
    """Raised for any non-2xx response from Xero or a failed token exchange."""


class XeroClient:
    """Thin wrapper around the Xero REST API.

    `http` is anything with a requests-compatible ``request(method, url, **kw)``
    method; tests pass a fake so nothing ever touches the network.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_file: Path,
        http: Any = None,
        clock: Callable[[], float] = time.time,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_file = Path(token_file)
        if http is None:
            import requests  # imported lazily so offline paths never need it

            http = requests.Session()
        self.http = http
        self.clock = clock

    # ---- construction -------------------------------------------------
    @classmethod
    def from_env(cls, **kw) -> "XeroClient":
        missing = [k for k in ("XERO_CLIENT_ID", "XERO_CLIENT_SECRET", "XERO_REDIRECT_URI") if not os.environ.get(k)]
        if missing:
            raise XeroError(f"Missing environment variables: {', '.join(missing)} (see .env.example)")
        return cls(
            os.environ["XERO_CLIENT_ID"].strip(),
            os.environ["XERO_CLIENT_SECRET"].strip(),
            os.environ["XERO_REDIRECT_URI"].strip(),
            Path(os.environ.get("XERO_TOKEN_FILE", ".xero_tokens.json")),
            **kw,
        )

    # ---- tokens -------------------------------------------------------
    def _basic_auth(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def save_tokens(self, tokens: dict) -> None:
        tokens["expires_at"] = self.clock() + tokens.get("expires_in", 1800) - REFRESH_MARGIN_SECONDS
        self.token_file.write_text(json.dumps(tokens, indent=2))

    def load_tokens(self) -> dict:
        if not self.token_file.exists():
            raise XeroError("Not connected yet. Run:  py -m cardcoding.xero_api auth")
        return json.loads(self.token_file.read_text())

    def refresh(self, tokens: dict) -> dict:
        r = self.http.request("POST", TOKEN_URL, headers={"Authorization": self._basic_auth()}, data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
        })
        if r.status_code != 200:
            raise XeroError(f"Token refresh failed ({r.status_code}): {r.text}. Run auth again.")
        new = r.json()
        # The token endpoint doesn't return tenant info; carry it forward.
        new["tenant_id"] = tokens.get("tenant_id")
        new["tenant_name"] = tokens.get("tenant_name")
        self.save_tokens(new)  # refresh token is single-use: persist the rotated one immediately
        return new

    def tokens(self) -> dict:
        tokens = self.load_tokens()
        if self.clock() >= tokens.get("expires_at", 0):
            tokens = self.refresh(tokens)
        return tokens

    # ---- OAuth2 authorization-code flow -------------------------------
    def authorize_url(self, state: str) -> str:
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "scope": SCOPES, "state": state,
        })

    def exchange_code(self, code: str) -> dict:
        r = self.http.request("POST", TOKEN_URL, headers={"Authorization": self._basic_auth()}, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
        })
        if r.status_code != 200:
            raise XeroError(f"Token exchange failed ({r.status_code}): {r.text}")
        tokens = r.json()
        conns = self.http.request("GET", CONNECTIONS_URL, headers={
            "Authorization": "Bearer " + tokens["access_token"]}).json()
        orgs = [c for c in conns if c.get("tenantType") == "ORGANISATION"]
        if not orgs:
            raise XeroError("No Xero organisation was connected.")
        tokens["tenant_id"] = orgs[0]["tenantId"]
        tokens["tenant_name"] = orgs[0]["tenantName"]
        self.save_tokens(tokens)
        return tokens

    def auth(self, timeout: int = 300) -> dict:
        """Open the browser, catch the redirect on localhost, exchange the code."""
        state = secrets.token_urlsafe(16)
        result: dict = {}
        port = urllib.parse.urlparse(self.redirect_uri).port or 80

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                ok = qs.get("state", [""])[0] == state and "code" in qs  # CSRF check
                result["code"] = qs["code"][0] if ok else None
                result["error"] = qs.get("error", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                msg = "Connected to Xero. You can close this tab." if ok else "Authorization failed."
                self.wfile.write(f"<h2 style='font-family:sans-serif'>{msg}</h2>".encode())

            def log_message(self, *args):
                pass

        server = HTTPServer(("localhost", port), Handler)
        threading.Thread(target=server.handle_request, daemon=True).start()
        url = self.authorize_url(state)
        print("Opening Xero in your browser to approve access...")
        print("If it doesn't open, paste this link into your browser:\n" + url)
        webbrowser.open(url)

        deadline = time.time() + timeout
        while "code" not in result and time.time() < deadline:
            time.sleep(0.5)
        server.server_close()
        if not result.get("code"):
            raise XeroError(f"Approval didn't complete ({result.get('error') or 'timed out or state mismatch'}).")
        tokens = self.exchange_code(result["code"])
        print(f"Connected to: {tokens['tenant_name']}")
        return tokens

    # ---- API calls ----------------------------------------------------
    def call(self, method: str, endpoint: str, params: dict | None = None, body: Any = None) -> dict:
        tokens = self.tokens()
        r = self.http.request(method, API_BASE + endpoint.lstrip("/"), params=params, json=body, headers={
            "Authorization": "Bearer " + tokens["access_token"],
            "Xero-tenant-id": tokens["tenant_id"],
            "Accept": "application/json",
        })
        if r.status_code >= 400:
            raise XeroError(f"Xero error {r.status_code}: {r.text}")
        return r.json() if r.text else {}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__)
        return 2
    from .config import load_dotenv

    load_dotenv()
    client = XeroClient.from_env()
    cmd = args[0].lower()
    if cmd == "auth":
        client.auth()
    elif cmd == "whoami":
        t = client.tokens()
        print(f"Connected to: {t['tenant_name']} ({t['tenant_id']})")
    elif cmd == "get":
        params = dict(a.split("=", 1) for a in args[2:])
        print(json.dumps(client.call("GET", args[1], params=params), indent=2))
    elif cmd in ("post", "put"):
        body = json.loads(Path(args[2]).read_text())
        print(json.dumps(client.call(cmd.upper(), args[1], body=body), indent=2))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except XeroError as e:
        sys.exit(str(e))
