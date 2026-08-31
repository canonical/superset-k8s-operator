#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""MCP authentication and RBAC integration tests."""

import json
import logging

import pytest
import pytest_asyncio
from integration.conftest import deploy  # noqa: F401, pylint: disable=W0611
from integration.helpers import TLS_NAME, TRAEFIK_NAME, UI_NAME, get_unit_url
from integration.mcp_helpers import (
    HYDRA_APP,
    HYDRA_ADMIN_PORT,
    HYDRA_CHANNEL,
    HYDRA_PUBLIC_PORT,
    LOGIN_UI_APP,
    LOGIN_UI_CHANNEL,
    MCP_ENDPOINT,
    MCP_PORT,
    create_hydra_client,
    ensure_rls_rule,
    get_hydra_token,
    get_mcp_oauth_client_id,
    mcp_call,
    setup_hydra_test_clients,
    setup_mcp_fixtures,
    superset_login,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

# Usernames registered as Hydra clients so sub == username in issued tokens.
_TEST_USERS = ["admin", "gamma_user", "sqlab_user"]
_CLIENT_SECRET_SUFFIX = "_mcp_secret"


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy_mcp_oauth", scope="module")
async def deploy_mcp_oauth_fixture(ops_test: OpsTest, deploy) -> None:
    """Extend the base deployment with Hydra and wire the oauth relation.

    Steps (mirrors test_oauth.py pattern):
    1. Deploy self-signed-certificates and wire to Traefik so it gets an
       external hostname — required by Hydra's public-route.
    2. Deploy Hydra; integrate pg-database and public-route.
    3. Wait for Hydra active, then wire superset-k8s-ui:oauth → hydra:oauth.
    4. Flip mcp-auth-enabled=True and register test Hydra clients.
    """
    del deploy  # wait for base fixture

    async with ops_test.fast_forward():
        # Step 1: TLS → Traefik so external hostname is published.
        await ops_test.model.deploy(TLS_NAME, channel="1/stable")
        await ops_test.model.wait_for_idle(
            apps=[TLS_NAME], status="active", raise_on_blocked=False, timeout=1200,
        )
        await ops_test.model.integrate(
            f"{TRAEFIK_NAME}:certificates", f"{TLS_NAME}:certificates"
        )
        await ops_test.model.wait_for_idle(
            apps=[TRAEFIK_NAME, UI_NAME], status="active", raise_on_blocked=False, timeout=600,
        )

    # Step 2: Deploy Hydra + login UI outside fast_forward — Hydra's update-status
    # hook fires every few seconds inside fast_forward, briefly setting maintenance,
    # which prevents wait_for_idle from ever completing.
    await ops_test.model.deploy(LOGIN_UI_APP, channel=LOGIN_UI_CHANNEL, trust=True)
    await ops_test.model.deploy(HYDRA_APP, channel=HYDRA_CHANNEL, trust=True)
    await ops_test.model.integrate(
        f"{HYDRA_APP}:pg-database", "postgresql-k8s:database"
    )
    await ops_test.model.integrate(
        f"{HYDRA_APP}:public-route", f"{TRAEFIK_NAME}:traefik-route"
    )
    await ops_test.model.integrate(
        f"{HYDRA_APP}:ui-endpoint-info", f"{LOGIN_UI_APP}:ui-endpoint-info"
    )
    await ops_test.model.wait_for_idle(
        apps=[HYDRA_APP, LOGIN_UI_APP], status="active", raise_on_blocked=False, timeout=1200,
    )

    # Step 3: Wire oauth relation and wait for both sides active.
    await ops_test.model.integrate(f"{UI_NAME}:oauth", f"{HYDRA_APP}:oauth")
    await ops_test.model.wait_for_idle(
        apps=[UI_NAME, HYDRA_APP], status="active", raise_on_blocked=False, timeout=600,
    )

    # Step 4: Flip auth-enabled, wait, register test clients.
    await ops_test.model.applications[UI_NAME].set_config({"mcp-auth-enabled": "True"})
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[UI_NAME], status="active", raise_on_blocked=False, timeout=300,
        )

    mcp_client_id = await get_mcp_oauth_client_id(ops_test, UI_NAME)
    await setup_hydra_test_clients(
        ops_test,
        _TEST_USERS,
        mcp_client_id,
        client_secret_suffix=_CLIENT_SECRET_SUFFIX,
    )


@pytest_asyncio.fixture(scope="module")
async def mcp_fixtures(ops_test: OpsTest, deploy_mcp_oauth):
    """Set up MCP test fixtures once per module: users, roles, RLS seed."""
    superset_url = await get_unit_url(ops_test, UI_NAME, 0, 8088)
    api_token = superset_login(superset_url)
    return setup_mcp_fixtures(superset_url, api_token)


async def _token_url(ops_test: OpsTest) -> str:
    """Return Hydra's public token endpoint URL."""
    base = await get_unit_url(ops_test, HYDRA_APP, 0, HYDRA_PUBLIC_PORT)
    return f"{base}/oauth2/token"


def _client_secret(username: str) -> str:
    return f"{username}{_CLIENT_SECRET_SUFFIX}"


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy_mcp_oauth")
class TestMCPAuthentication:
    """MCP OAuth 2.1 authentication tests."""

    async def test_valid_admin_token(self, ops_test: OpsTest):
        """Valid admin client-credentials token → HTTP 200, username == admin."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"
        token_url = await _token_url(ops_test)

        admin_token = get_hydra_token(token_url, "admin", _client_secret("admin"))
        status, text = mcp_call(mcp_endpoint, "get_instance_info", {}, admin_token)

        assert status == 200
        response = json.loads(text)
        assert response.get("current_user", {}).get("username") == "admin"

    async def test_health_check(self, ops_test: OpsTest):
        """health_check tool works with a valid token."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"
        token_url = await _token_url(ops_test)

        admin_token = get_hydra_token(token_url, "admin", _client_secret("admin"))
        status, text = mcp_call(mcp_endpoint, "health_check", {}, admin_token)

        assert status == 200
        response = json.loads(text)
        assert response.get("status") == "healthy"

    @pytest.mark.parametrize("token_variant,description", [
        ("tampered", "JWT signature corrupted"),
        ("expired", "token expired — Hydra-issued but manually expired"),
        ("missing", "no Authorization header"),
    ])
    async def test_invalid_token_rejected(
        self, ops_test: OpsTest, token_variant: str, description: str
    ):
        """Invalid / missing tokens are rejected with HTTP 401."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"
        token_url = await _token_url(ops_test)

        if token_variant == "tampered":
            valid = get_hydra_token(token_url, "admin", _client_secret("admin"))
            token: str | None = valid + "corrupted"
        elif token_variant == "expired":
            # Hydra does not issue expired tokens; send a structurally valid
            # but obviously wrong JWT (wrong algorithm / signature).
            token = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJhZG1pbiJ9."
        else:  # missing
            token = None

        status, _ = mcp_call(mcp_endpoint, "get_instance_info", {}, token)
        assert status == 401

    async def test_unknown_username_in_token(self, ops_test: OpsTest):
        """Token for a client_id not matching any Superset user → 200 with error body."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"
        token_url = await _token_url(ops_test)

        hydra_admin_url = await get_unit_url(
            ops_test, HYDRA_APP, 0, HYDRA_ADMIN_PORT
        )
        # Register a one-off client whose id has no matching Superset user.
        mcp_client_id = await get_mcp_oauth_client_id(ops_test, UI_NAME)
        create_hydra_client(
            hydra_admin_url,
            "ghost_user_no_match",
            "ghost_secret",
            audience=[mcp_client_id],
        )
        ghost_token = get_hydra_token(
            token_url, "ghost_user_no_match", "ghost_secret"
        )

        status, text = mcp_call(mcp_endpoint, "get_instance_info", {}, ghost_token)
        assert status == 200
        assert "no Superset user found" in text


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy_mcp_oauth", "mcp_fixtures")
class TestMCPRBAC:
    """MCP role-based access control tests."""

    async def test_gamma_user_reduced_access(self, ops_test: OpsTest):
        """Gamma role has fewer accessible menus than Admin."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"
        token_url = await _token_url(ops_test)

        admin_token = get_hydra_token(token_url, "admin", _client_secret("admin"))
        gamma_token = get_hydra_token(token_url, "gamma_user", _client_secret("gamma_user"))

        _, admin_text = mcp_call(mcp_endpoint, "get_instance_info", {}, admin_token)
        admin_menus = len(
            json.loads(admin_text)
            .get("feature_availability", {})
            .get("accessible_menus", [])
        )

        status, text = mcp_call(mcp_endpoint, "get_instance_info", {}, gamma_token)
        response = json.loads(text)
        gamma_user = response.get("current_user", {})
        gamma_menus = len(
            response.get("feature_availability", {}).get("accessible_menus", [])
        )

        assert status == 200
        assert gamma_user.get("username") == "gamma_user"
        assert "Gamma" in gamma_user.get("roles", [])
        assert gamma_menus < admin_menus

    async def test_sqlab_user_authenticated(self, ops_test: OpsTest):
        """sqlab_user (Alpha + sql_lab + SqlLabRLS) is authenticated with correct roles."""
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"
        token_url = await _token_url(ops_test)

        sqlab_token = get_hydra_token(
            token_url, "sqlab_user", _client_secret("sqlab_user")
        )
        status, text = mcp_call(mcp_endpoint, "get_instance_info", {}, sqlab_token)

        assert status == 200
        response = json.loads(text)
        user = response.get("current_user", {})

        assert user.get("username") == "sqlab_user"
        assert "Alpha" in user.get("roles", [])
        assert "sql_lab" in user.get("roles", [])
        assert "SqlLabRLS" in user.get("roles", [])


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy_mcp_oauth")
class TestMCPRLS:
    """MCP Row-Level Security enforcement tests."""

    @pytest.mark.parametrize("username,expected_genders", [
        ("sqlab_user", {"boy"}),
        ("admin", {"boy", "girl"}),
    ])
    async def test_rls_filtering(
        self, ops_test: OpsTest, mcp_fixtures: dict, username: str, expected_genders: set
    ):
        """RLS filter is applied correctly per role."""
        superset_url = await get_unit_url(ops_test, UI_NAME, 0, 8088)
        mcp_url = await get_unit_url(ops_test, UI_NAME, 0, MCP_PORT)
        mcp_endpoint = f"{mcp_url}{MCP_ENDPOINT}"
        token_url = await _token_url(ops_test)

        api_token = superset_login(superset_url)

        ensure_rls_rule(
            superset_url, api_token,
            "demo-rls-birth-names-boy-only",
            "gender = 'boy'",
            [mcp_fixtures["rls_role_id"]],
            [mcp_fixtures["birth_names_id"]],
        )

        sql_query = (
            "SELECT gender, COUNT(*) as cnt FROM birth_names "
            "GROUP BY gender ORDER BY gender"
        )
        token = get_hydra_token(token_url, username, _client_secret(username))
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
