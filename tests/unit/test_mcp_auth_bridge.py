# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the get_user_from_request JWT-identity bridge.

The bridge lives in templates/mcp_jwt_identity.py and is loaded by every
Superset process at startup via PYTHONPATH. These tests exec the module in
isolation with no installed Superset package or running Juju model required.
"""

import pathlib
import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Bootstrap: exec the module without importing Superset or FastMCP
# ---------------------------------------------------------------------------


def _load_module_ns():
    """Exec templates/mcp_jwt_identity.py into a fresh namespace.

    Returns:
        Dict of names defined by the module: _identity_candidates and
        get_user_from_request_with_jwt.

    Raises:
        FileNotFoundError: If mcp_jwt_identity.py cannot be located.
    """
    module_path = (
        pathlib.Path(__file__).parent.parent.parent
        / "templates"
        / "mcp_jwt_identity.py"
    )
    if not module_path.exists():
        raise FileNotFoundError(
            f"mcp_jwt_identity.py not found at {module_path}"
        )

    ns: dict = {}
    exec(
        module_path.read_text(), ns
    )  # nosec B102  # pylint: disable=exec-used
    return ns


_P = _load_module_ns()
_identity_candidates = _P["_identity_candidates"]
_get_user_from_request_with_jwt = _P["get_user_from_request_with_jwt"]


class _StubUser:
    """Stand-in Superset user.

    Attrs:
        username: The user's username.
    """

    def __init__(self, username):
        """Record the username.

        Args:
            username: The user's username.
        """
        self.username = username


class _AccessToken:
    """Stand-in FastMCP AccessToken.

    Attrs:
        claims: The verified JWT's claims.
        client_id: The token's client_id claim.
    """

    def __init__(self, claims=None, client_id=None):
        """Record the claims and client_id.

        Args:
            claims: The verified JWT's claims, or None for an empty dict.
            client_id: The token's client_id claim.
        """
        self.claims = claims or {}
        self.client_id = client_id


def _stub_dependencies(
    *,
    access_token=None,
    dev_username="",
    g_user=None,
    users=None,
    prefer_email=False,
):
    """Install stub fastmcp/flask/superset modules the patch imports.

    Args:
        access_token: The AccessToken get_access_token() should return, or
            None to model an unauthenticated call.
        dev_username: The value MCP_DEV_USERNAME should carry.
        g_user: The value g.user should carry, or None.
        users: Mapping of username to the user load_user_with_relationships
            should resolve for it.
        prefer_email: What _should_prefer_email() should report.
    """
    users = users or {}

    fastmcp_deps = types.ModuleType("fastmcp.server.dependencies")
    fastmcp_deps.get_access_token = lambda: access_token
    sys.modules.setdefault("fastmcp", types.ModuleType("fastmcp"))
    sys.modules.setdefault(
        "fastmcp.server", types.ModuleType("fastmcp.server")
    )
    sys.modules["fastmcp.server.dependencies"] = fastmcp_deps

    flask_module = types.ModuleType("flask")
    flask_module.current_app = types.SimpleNamespace(
        config={"MCP_DEV_USERNAME": dev_username}
    )
    flask_module.g = types.SimpleNamespace(user=g_user)
    sys.modules["flask"] = flask_module

    auth_module = types.ModuleType("superset.mcp_service.auth")
    auth_module.load_user_with_relationships = lambda username: users.get(
        username
    )
    sys.modules.setdefault("superset", types.ModuleType("superset"))
    sys.modules.setdefault(
        "superset.mcp_service", types.ModuleType("superset.mcp_service")
    )
    sys.modules["superset.mcp_service.auth"] = auth_module

    _P["_should_prefer_email"] = lambda: prefer_email


# ---------------------------------------------------------------------------
# _identity_candidates
# ---------------------------------------------------------------------------


class TestIdentityCandidates(unittest.TestCase):
    """Tests for the JWT-claim-to-username priority order."""

    def test_sub_tried_before_email(self):
        """Hydra's client_credentials tokens set sub to the client_id itself."""
        candidates = _identity_candidates(
            {"sub": "alice", "email": "alice@example.com"}
        )
        self.assertEqual(candidates, ["alice", "alice@example.com"])

    def test_client_id_is_the_last_resort(self):
        """client_id is only tried once sub and email are absent."""
        candidates = _identity_candidates({}, client_id="my-client")
        self.assertEqual(candidates, ["my-client"])

    def test_duplicates_are_dropped(self):
        """The same value appearing twice is only tried once."""
        candidates = _identity_candidates({"sub": "alice"}, client_id="alice")
        self.assertEqual(candidates, ["alice"])

    def test_empty_claims_produce_no_candidates(self):
        """No sub, email or client_id leaves nothing to try."""
        self.assertEqual(_identity_candidates({}), [])

    def test_google_tries_email_before_sub(self):
        """Google's sub is a numeric account id, never a Superset username."""
        candidates = _identity_candidates(
            {"sub": "108247694", "email": "alice@example.com"},
            prefer_email=True,
        )
        self.assertEqual(candidates, ["alice@example.com", "108247694"])


# ---------------------------------------------------------------------------
# _get_user_from_request_with_jwt
# ---------------------------------------------------------------------------


class TestGetUserFromRequestWithJwt(unittest.TestCase):
    """Tests for the resolved-user priority chain."""

    def test_jwt_claim_resolves_the_user(self):
        """A verified token's sub claim resolves an existing user."""
        alice = _StubUser("alice")
        _stub_dependencies(
            access_token=_AccessToken(claims={"sub": "alice"}),
            users={"alice": alice},
        )

        self.assertIs(_get_user_from_request_with_jwt(), alice)

    def test_unknown_jwt_identity_fails_closed(self):
        """A token resolving no known user raises rather than falling through."""
        _stub_dependencies(
            access_token=_AccessToken(claims={"sub": "ghost"}),
            dev_username="admin",
        )

        with self.assertRaises(ValueError):
            _get_user_from_request_with_jwt()

    def test_dev_username_used_without_a_token(self):
        """No JWT and no g.user falls back to MCP_DEV_USERNAME."""
        admin = _StubUser("admin")
        _stub_dependencies(dev_username="admin", users={"admin": admin})

        self.assertIs(_get_user_from_request_with_jwt(), admin)

    def test_dev_username_wins_over_a_stale_g_user(self):
        """MCP_DEV_USERNAME is tried before g.user, not after.

        g.user can hold a stale value from a previous tool call on the same
        worker, which would otherwise impersonate whoever the current call
        actually belongs to.
        """
        admin = _StubUser("admin")
        stale = _StubUser("someone-else")
        _stub_dependencies(
            dev_username="admin", g_user=stale, users={"admin": admin}
        )

        self.assertIs(_get_user_from_request_with_jwt(), admin)

    def test_g_user_used_as_the_last_resort(self):
        """With no token and no dev username, g.user is used."""
        stale = _StubUser("someone-else")
        _stub_dependencies(g_user=stale)

        self.assertIs(_get_user_from_request_with_jwt(), stale)

    def test_fails_closed_with_no_identity_source(self):
        """No token, no dev username and no g.user raises."""
        _stub_dependencies()

        with self.assertRaises(ValueError):
            _get_user_from_request_with_jwt()

    def test_google_prefers_the_email_matched_user_over_sub(self):
        """Proves get_user_from_request_with_jwt() wires prefer_email through.

        Unlike test_google_tries_email_before_sub, which only checks
        _identity_candidates in isolation, this checks the caller actually
        passes _should_prefer_email()'s result into it.
        """
        by_sub = _StubUser("108247694")
        by_email = _StubUser("alice@example.com")
        _stub_dependencies(
            access_token=_AccessToken(
                claims={"sub": "108247694", "email": "alice@example.com"}
            ),
            users={"108247694": by_sub, "alice@example.com": by_email},
            prefer_email=True,
        )

        self.assertIs(_get_user_from_request_with_jwt(), by_email)
