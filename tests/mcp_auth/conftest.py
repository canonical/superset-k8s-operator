# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fixtures for testing templates/mcp_google_auth.py.

That module ships inside the workload image (pushed by load_superset_files())
rather than imported by the charm, so it is not on the charm's own
PYTHONPATH; it is added to sys.path here instead, mirroring how
datahub-mcp-k8s-operator's tests/serve/conftest.py imports its rock
entrypoint.
"""

import sys
from pathlib import Path

import pytest
from fastmcp import settings

TEMPLATES_DIR = Path(__file__).parents[2] / "templates"
sys.path.insert(0, str(TEMPLATES_DIR))

ALL_MCP_AUTH_VARS = (
    "MCP_AUTH_ISSUER",
    "MCP_AUTH_JWT_ACCESS_TOKEN",
    "MCP_AUTH_JWKS_URL",
    "MCP_AUTH_INTROSPECTION_URL",
    "MCP_AUTH_CLIENT_ID",
    "MCP_AUTH_CLIENT_SECRET",
    "MCP_AUTH_BASE_URL",
    "MCP_AUTH_CLIENT_REGISTRATION",
)


@pytest.fixture
def oauth_env(monkeypatch):
    """Return a helper that sets the MCP_AUTH_* environment the charm builds.

    Args:
        monkeypatch: The pytest monkeypatch fixture.

    Returns:
        A callable taking the variables to set, without their prefix.
    """

    def _set(**overrides):
        """Set the MCP_AUTH_* variables the charm's _get_mcp_auth_env() would.

        Args:
            overrides: Variables to set, without the `MCP_AUTH_` prefix.
        """
        for name in ALL_MCP_AUTH_VARS:
            monkeypatch.delenv(name, raising=False)
        for name, value in overrides.items():
            monkeypatch.setenv(f"MCP_AUTH_{name.upper()}", value)

    return _set


@pytest.fixture(autouse=True)
def proxy_storage(monkeypatch, tmp_path):
    """Keep the OAuth proxy's client registrations inside the test.

    The proxy persists what it registers under FastMCP's data directory,
    which would otherwise carry one test's clients into the next and into
    the home directory of whoever ran them.

    Args:
        monkeypatch: Fixture used to redirect the directory.
        tmp_path: Per-test directory to redirect it to.
    """
    monkeypatch.setattr(settings, "home", tmp_path)
