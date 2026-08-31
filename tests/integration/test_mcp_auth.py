#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""MCP authentication and RBAC integration tests."""

import json
import logging

import pytest
import pytest_asyncio
from integration.conftest import deploy  # noqa: F401, pylint: disable=W0611
from integration.helpers import UI_NAME, get_unit_url
from integration.mcp_helpers import (
    MCP_ENDPOINT,
    MCP_PORT,
    ensure_rls_rule,
    make_token,
    mcp_call,
    setup_mcp_fixtures,
    superset_login,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

JWT_SECRET = "a" * 64


@pytest_asyncio.fixture(scope="module")
async def mcp_fixtures(ops_test: OpsTest):
    """Set up MCP test fixtures once per module: users, roles, RLS seed."""
    superset_url = await get_unit_url(ops_test, UI_NAME, 0, 8088)
    api_token = superset_login(superset_url)
    return setup_mcp_fixtures(superset_url, api_token)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestMCPAuthentication:
    """MCP JWT authentication tests."""

    async def test_valid_admin_jwt(self, ops_test: OpsTest):
        """Verify valid admin JWT returns HTTP 200 with authenticated user."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"

        admin_token = make_token("admin", JWT_SECRET)
        status, text = mcp_call(mcp_endpoint, "get_instance_info", {}, admin_token)

        assert status == 200
        response = json.loads(text)
        assert response.get("current_user", {}).get("username") == "admin"

    async def test_health_check(self, ops_test: OpsTest):
        """Verify health_check endpoint works without auth context."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"

        admin_token = make_token("admin", JWT_SECRET)
        status, text = mcp_call(mcp_endpoint, "health_check", {}, admin_token)

        assert status == 200
        response = json.loads(text)
        assert response.get("status") == "healthy"

    @pytest.mark.parametrize("token_variant,description", [
        ("tampered", "JWT signature corrupted"),
        ("expired", "token expired 60 seconds"),
        ("missing", "no Authorization header"),
    ])
    async def test_invalid_token_rejected(
        self, ops_test: OpsTest, token_variant: str, description: str
    ):
        """Verify various invalid tokens return HTTP 401."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"

        if token_variant == "tampered":
            token = make_token("admin", JWT_SECRET) + "x"
        elif token_variant == "expired":
            token = make_token("admin", JWT_SECRET, exp_offset=-60)
        else:  # missing
            token = None

        status, _ = mcp_call(mcp_endpoint, "get_instance_info", {}, token)
        assert status == 401

    async def test_unknown_username_in_token(self, ops_test: OpsTest):
        """Verify valid JWT signature but unknown username returns 200 with error."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"

        ghost_token = make_token("ghost_user", JWT_SECRET)
        status, text = mcp_call(mcp_endpoint, "get_instance_info", {}, ghost_token)

        assert status == 200
        assert "No authenticated user found" in text


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy", "mcp_fixtures")
class TestMCPRBAC:
    """MCP role-based access control tests."""

    async def test_gamma_user_reduced_access(self, ops_test: OpsTest):
        """Verify Gamma role has limited menu access vs admin."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"

        admin_token = make_token("admin", JWT_SECRET)
        gamma_token = make_token("gamma_user", JWT_SECRET)

        _, admin_text = mcp_call(mcp_endpoint, "get_instance_info", {}, admin_token)
        admin_menus = len(
            json.loads(admin_text).get("feature_availability", {}).get("accessible_menus", [])
        )

        status, text = mcp_call(mcp_endpoint, "get_instance_info", {}, gamma_token)
        response = json.loads(text)
        gamma_user = response.get("current_user", {})
        gamma_menus = len(response.get("feature_availability", {}).get("accessible_menus", []))

        assert status == 200
        assert gamma_user.get("username") == "gamma_user"
        assert "Gamma" in gamma_user.get("roles", [])
        assert gamma_menus < admin_menus

    async def test_sqlab_user_authenticated(self, ops_test: OpsTest):
        """Verify sqlab_user (Alpha + sql_lab + SqlLabRLS) authenticated."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"

        sqlab_token = make_token("sqlab_user", JWT_SECRET)
        status, text = mcp_call(mcp_endpoint, "get_instance_info", {}, sqlab_token)

        assert status == 200
        response = json.loads(text)
        user = response.get("current_user", {})

        assert user.get("username") == "sqlab_user"
        assert "Alpha" in user.get("roles", [])
        assert "sql_lab" in user.get("roles", [])
        assert "SqlLabRLS" in user.get("roles", [])


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestMCPRLS:
    """MCP Row-Level Security enforcement tests."""

    @pytest.mark.parametrize("username,expected_genders", [
        ("sqlab_user", {"boy"}),
        ("admin", {"boy", "girl"}),
    ])
    async def test_rls_filtering(
        self, ops_test: OpsTest, mcp_fixtures: dict, username: str, expected_genders: set
    ):
        """Verify RLS filtering is applied correctly per role."""
        superset_url = await get_unit_url(ops_test, UI_NAME, 0, 8088)
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"

        api_token = superset_login(superset_url)

        ensure_rls_rule(
            superset_url, api_token,
            "demo-rls-birth-names-boy-only",
            "gender = 'boy'",
            [mcp_fixtures["rls_role_id"]],
            [mcp_fixtures["birth_names_id"]],
        )

        sql_query = "SELECT gender, COUNT(*) as cnt FROM birth_names GROUP BY gender ORDER BY gender"
        token = make_token(username, JWT_SECRET)
        status, text = mcp_call(
            mcp_endpoint,
            "execute_sql",
            {"request": {"database_id": 1, "sql": sql_query}},
            token,
        )

        assert status == 200
        rows = json.loads(text).get("rows", [])
        genders = {r["gender"] for r in rows}
        assert genders == expected_genders
