# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the Superset API client's session handling."""

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest

from superset_api import SupersetApiClient, SupersetApiError

SESSION = "session=csrf-bearing"
CSRF_TOKEN = "signed-csrf-token"  # nosec B105


class _SupersetStub(BaseHTTPRequestHandler):
    """Answer the calls a role grant makes, enforcing CSRF like Superset.

    The CSRF token is handed out with a Secure session cookie, and a write
    is refused unless that cookie comes back with the token.

    Attributes:
        writes: Whether each write carried both the cookie and the token.
    """

    writes: list = []

    def log_message(self, *args):  # pylint: disable=arguments-differ
        """Keep the test output quiet.

        Args:
            args: Ignored.
        """

    def _reply(self, status, body, cookie=None):
        """Send a JSON response.

        Args:
            status: HTTP status code.
            body: Object to serialise as the response body.
            cookie: A Set-Cookie header value to send, if any.
        """
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_POST(self):  # noqa: N802
        """Serve the login and the role permission write."""
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/api/v1/security/login":
            exp = datetime.now(timezone.utc) + timedelta(hours=1)
            token = jwt.encode({"exp": exp}, "k" * 32, algorithm="HS256")
            self._reply(200, {"access_token": token, "refresh_token": token})
            return
        has_session = SESSION in (self.headers.get("Cookie") or "")
        has_token = self.headers.get("X-CSRF-Token") == CSRF_TOKEN
        _SupersetStub.writes.append(has_session and has_token)
        if not has_session:
            self._reply(400, {"message": "The CSRF session token is missing."})
            return
        self._reply(200, {"result": "ok"})

    def do_GET(self):  # noqa: N802
        """Serve the CSRF token and the role's current permissions."""
        if self.path == "/api/v1/security/csrf_token/":
            self._reply(
                200,
                {"result": CSRF_TOKEN},
                cookie=f"{SESSION}; Secure; HttpOnly; Path=/; SameSite=Lax",
            )
            return
        self._reply(200, {"result": []})


@pytest.fixture(name="superset_url")
def superset_url_fixture():
    """Serve the Superset stub over plain HTTP on a free local port.

    Yields:
        The stub's base URL.
    """
    _SupersetStub.writes = []
    server = HTTPServer(("127.0.0.1", 0), _SupersetStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_writes_carry_the_secure_session_cookie_over_http(superset_url):
    """A CSRF-protected write sends back the Secure session cookie over HTTP."""
    api = SupersetApiClient("admin", "password", base_url=superset_url)

    api.update_role_permissions(role_id=1, permission_view_menu_id=7)

    assert _SupersetStub.writes == [True]


def test_a_write_without_the_session_cookie_is_refused(superset_url):
    """The stub refuses a write that lacks the session cookie, as Superset does."""
    api = SupersetApiClient("admin", "password", base_url=superset_url)
    api.update_role_permissions(role_id=1, permission_view_menu_id=7)
    api._session.cookies.clear()  # pylint: disable=protected-access

    with pytest.raises(SupersetApiError):
        api.update_role_permissions(role_id=1, permission_view_menu_id=7)


def test_an_https_client_keeps_its_cookies_secure():
    """A client on HTTPS stores Secure cookies with the flag intact."""
    api = SupersetApiClient(
        "admin", "password", base_url="https://superset.example"
    )

    api._session.cookies.set(  # pylint: disable=protected-access
        "session", "value", secure=True
    )

    assert all(
        cookie.secure
        for cookie in api._session.cookies  # pylint: disable=protected-access
    )
