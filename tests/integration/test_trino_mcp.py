#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Trino RBAC enforcement via Superset user impersonation.

Validates that Trino's Ranger-enforced access control is correctly applied to
the *impersonated* Superset user, not the Trino service account.

Deployment topology
-------------------
  PostgreSQL ──► Superset (multi-app: ui, worker, beat; MCP enabled)
  Redis       ──► Superset
  PostgreSQL  ──► Ranger
  PostgreSQL  ──► Hydra
  Trino       ──► Ranger  (policy relation)
  Trino       ──► Superset (trino-catalog relation → creates Superset DB connection)
  TLS         ──► Traefik (external hostname for Hydra public route)
  Hydra       ──► Traefik (public-route)
  Hydra       ──► Login UI (ui-endpoint-info)
  Superset UI ──► Hydra   (oauth relation)

The Trino charm sets ``impersonate_user: True`` on every Superset DB connection,
so every SQL query routed through Superset carries ``X-Trino-User: <superset_user>``.
Ranger evaluates that header against its policies.

Test strategy
-------------
Two Superset users are created with identical Superset roles (Alpha + sql_lab),
so any difference in query results is purely due to Trino-side enforcement:

  ``trino_allowed`` — added to the Ranger Trino-service ``all - catalog`` and
                      ``all - trinouser`` default policies → can query.
  ``trino_blocked`` — not in any Ranger policy → access denied by Ranger.

The assertion is that ``execute_sql`` via MCP succeeds for ``trino_allowed`` and
returns an ``Access Denied`` error body for ``trino_blocked``.

A third ``current_user`` probe confirms impersonation is actually firing: the
Trino ``current_user`` session function must return the Superset username, not
the Trino service account.
"""

import asyncio
import json
import logging
import time
import uuid

import pytest
import pytest_asyncio
import requests

from integration.conftest import deploy  # noqa: F401, pylint: disable=W0611
from integration.helpers import (
    POSTGRES_NAME,
    TLS_NAME,
    TRAEFIK_NAME,
    UI_NAME,
    api_authentication,
    get_unit_url,
)
from integration.mcp_helpers import (
    HYDRA_APP,
    HYDRA_ADMIN_PORT,
    HYDRA_CHANNEL,
    HYDRA_PUBLIC_PORT,
    LOGIN_UI_APP,
    LOGIN_UI_CHANNEL,
    MCP_ENDPOINT,
    MCP_PORT,
    ALPHA_ROLE_ID,
    SQLAB_ROLE_ID,
    create_hydra_client,
    ensure_user,
    get_hydra_token,
    get_mcp_oauth_client_id,
    superset_login,
    mcp_call,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

# ── Application names ──────────────────────────────────────────────────────────

TRINO_APP = "trino-k8s"
RANGER_APP = "ranger-k8s"

# ── Ranger constants ───────────────────────────────────────────────────────────

RANGER_PORT = 6080
RANGER_ADMIN_USER = "admin"
RANGER_ADMIN_PASSWORD = "rangerR0cks!"
RANGER_AUTH = (RANGER_ADMIN_USER, RANGER_ADMIN_PASSWORD)
TRINO_SERVICE_NAME = "trino-service"

# ── Trino test users ───────────────────────────────────────────────────────────

ALLOWED_USER = "trino_allowed"
ALLOWED_PASSWORD = "allowed_pass123"
BLOCKED_USER = "trino_blocked"
BLOCKED_PASSWORD = "blocked_pass123"

# ── PostgreSQL replica secret for Trino catalog ────────────────────────────────

POSTGRESQL_REPLICA_SECRET = """\
rw:
  user: trino
  password: pwd1
"""  # nosec

# ── Timeouts ───────────────────────────────────────────────────────────────────

TIMEOUT_DEPLOY = 2000
TIMEOUT_IDLE = 1000
# Ranger plugin polls for policy updates on a ~30s cycle; wait long enough.
RANGER_POLICY_SYNC_WAIT = 60


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _reusing_existing_model(ops_test: OpsTest) -> bool:
    """True when invoked as ``--model <existing> --no-deploy``.

    skip_if_deployed on a fixture is not honoured by pytest-operator's own
    skip check (it only inspects test-item keywords), so fixtures in this
    file enforce the --no-deploy contract explicitly using this helper.
    """
    return bool(
        ops_test.request.config.getoption("--no-deploy")
        and ops_test.request.config.getoption("--model")
    )


@pytest_asyncio.fixture(scope="module")
async def pg_secret_id(ops_test: OpsTest) -> str:
    """Create a Juju secret with PostgreSQL replica credentials for Trino."""
    if _reusing_existing_model(ops_test):
        for secret in await ops_test.model.list_secrets():
            if secret.label == "trino-mcp-pg-secret":
                return secret.uri.split(":")[-1]
        raise RuntimeError(
            "Reusing existing model but no 'trino-mcp-pg-secret' secret found"
        )
    secret = await ops_test.model.add_secret(
        name="trino-mcp-pg-secret",
        data_args=[f"replicas={POSTGRESQL_REPLICA_SECRET}"],
    )
    return secret.split(":")[-1]


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy_trino_mcp", scope="module")
async def deploy_trino_mcp_fixture(
    ops_test: OpsTest, deploy, pg_secret_id: str
) -> None:
    """Extend the base deployment with Trino, Ranger, and Hydra for MCP OAuth.

    Order matters: Trino's catalog relation must create the Superset database
    connection *before* Ranger's policy relation is wired.  If the policy
    relation is added first, Ranger denies the service account used to validate
    the connection and the Superset database is never created.
    """
    del deploy  # wait for base fixture to finish

    if _reusing_existing_model(ops_test):
        logger.info(
            "Skipping Trino/Ranger/Hydra deploy; reusing existing model %s",
            ops_test.model_name,
        )
        return

    await ops_test.model.set_config({"logging-config": "<root>=INFO;unit=DEBUG"})

    async with ops_test.fast_forward():
        # TLS → Traefik so an external hostname is published for Hydra's route.
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

    # Deploy Trino before Ranger so the catalog connection can be validated
    # without any Ranger policies active.
    await ops_test.model.deploy(
        TRINO_APP,
        channel="latest/edge",
        config={"charm-function": "all"},
        trust=True,
    )
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[TRINO_APP],
            status="active",
            raise_on_blocked=False,
            timeout=TIMEOUT_DEPLOY,
        )

    # Ranger uses the same PostgreSQL application already deployed for Superset.
    await ops_test.model.deploy(
        RANGER_APP,
        channel="latest/edge",
        config={"ranger-usersync-password": "P@ssw0rd1234"},
        trust=True,
    )
    await ops_test.model.integrate(RANGER_APP, POSTGRES_NAME)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[RANGER_APP, POSTGRES_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=TIMEOUT_DEPLOY,
        )

    # Configure the catalog and create the Superset relation before enabling
    # Ranger's policy manager for Trino.
    await ops_test.model.grant_secret("trino-mcp-pg-secret", TRINO_APP)
    catalog_config = _build_tpch_catalog_config(ops_test, pg_secret_id)
    await ops_test.model.applications[TRINO_APP].set_config(
        {"catalog-config": catalog_config}
    )
    await ops_test.model.integrate(
        f"{TRINO_APP}:trino-catalog", f"{UI_NAME}:trino-catalog"
    )
    await _wait_for_trino_database(ops_test)

    # Now enforce Ranger policies.
    await ops_test.model.integrate(f"{RANGER_APP}:policy", f"{TRINO_APP}:policy")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[RANGER_APP, TRINO_APP],
            status="active",
            raise_on_blocked=False,
            timeout=TIMEOUT_IDLE,
        )

    # Deploy Hydra + Login UI outside fast_forward (Hydra's update-status hook
    # can briefly set maintenance under fast_forward).
    await ops_test.model.deploy(LOGIN_UI_APP, channel=LOGIN_UI_CHANNEL, trust=True)
    await ops_test.model.deploy(HYDRA_APP, channel=HYDRA_CHANNEL, trust=True)
    await ops_test.model.integrate(f"{HYDRA_APP}:pg-database", f"{POSTGRES_NAME}:database")
    await ops_test.model.integrate(
        f"{HYDRA_APP}:public-route", f"{TRAEFIK_NAME}:traefik-route"
    )
    await ops_test.model.integrate(
        f"{HYDRA_APP}:ui-endpoint-info", f"{LOGIN_UI_APP}:ui-endpoint-info"
    )
    await ops_test.model.wait_for_idle(
        apps=[HYDRA_APP, LOGIN_UI_APP],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
    )

    # Wire OAuth relation and enable MCP authentication.
    await ops_test.model.integrate(f"{UI_NAME}:oauth", f"{HYDRA_APP}:oauth")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[UI_NAME, HYDRA_APP],
            status="active",
            raise_on_blocked=False,
            timeout=600,
        )

    await ops_test.model.applications[UI_NAME].set_config({"mcp-auth-enabled": "True"})
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[UI_NAME], status="active", raise_on_blocked=False, timeout=600,
        )


# ── Helpers ────────────────────────────────────────────────────────────────────


def _build_tpch_catalog_config(ops_test: OpsTest, pg_secret_id: str) -> str:
    """Build a minimal catalog-config YAML for Trino using PostgreSQL as backend.

    The catalog is named ``testcat`` to match the zone name Ranger creates.
    We use the existing PostgreSQL instance already deployed for Superset/Ranger.
    The actual backend data is irrelevant — we only need a real catalog so Ranger
    zones are created and SQL queries are routable through the MCP execute_sql tool.
    """
    return (
        "catalogs:\n"
        "  testcat:\n"
        "    backend: pg\n"
        f"    secret-id: {pg_secret_id}\n"
        "    database: superset\n"
        "backends:\n"
        "  pg:\n"
        "    connector: postgresql\n"
        f"    url: jdbc:postgresql://postgresql-k8s-primary.{ops_test.model.name}.svc.cluster.local:5432\n"
    )


async def _get_ranger_url_async(ops_test: OpsTest) -> str:
    """Return the Ranger admin URL via get_unit_url."""
    return await get_unit_url(
        ops_test, application=RANGER_APP, unit=0, port=RANGER_PORT
    )


async def _setup_ranger_users_and_policies(ops_test: OpsTest) -> None:
    """Create Ranger users and grant ``trino_allowed`` access to Trino."""
    ranger_url = await _get_ranger_url_async(ops_test)

    # Create users in Ranger's internal user store.
    for username in (ALLOWED_USER, BLOCKED_USER):
        response = requests.get(
            f"{ranger_url}/service/xusers/users",
            params={"name": username},
            auth=RANGER_AUTH,
            timeout=30,
        )
        response.raise_for_status()
        if response.json().get("totalCount", 0) == 0:
            response = requests.post(
                f"{ranger_url}/service/xusers/secure/users",
                json={
                    "name": username,
                    "firstName": username,
                    "lastName": "Test",
                    "emailAddress": f"{username}@example.com",
                    "password": "T3stP@ssword!",
                    "userRoleList": ["ROLE_USER"],
                },
                auth=RANGER_AUTH,
                timeout=30,
            )
            response.raise_for_status()

    # Grant ``trino_allowed`` access to every service-level Trino policy.
    # SHOW SCHEMAS and similar metadata queries need schema/table/column grants
    # in addition to the impersonation and catalog policies.
    response = requests.get(
        f"{ranger_url}/service/public/v2/api/service/{TRINO_SERVICE_NAME}/policy",
        auth=RANGER_AUTH,
        timeout=30,
    )
    response.raise_for_status()
    for policy in response.json():
        policy_items = policy.setdefault("policyItems", [{}])
        users = policy_items[0].setdefault("users", [])
        if ALLOWED_USER not in users:
            users.append(ALLOWED_USER)
            put_response = requests.put(
                f"{ranger_url}/service/public/v2/api/policy/{policy['id']}",
                json=policy,
                auth=RANGER_AUTH,
                timeout=30,
            )
            put_response.raise_for_status()
            logger.info(
                "Granted %s access via policy %r", ALLOWED_USER, policy.get("name")
            )


async def _wait_for_trino_database(
    ops_test: OpsTest, timeout: int = TIMEOUT_DEPLOY
) -> None:
    """Wait until the Trino catalog relation creates a Superset database."""
    superset_url = await get_unit_url(
        ops_test, application=UI_NAME, unit=0, port=8088
    )
    session = await api_authentication(ops_test, superset_url)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = session.get(f"{superset_url}/api/v1/database/", timeout=30)
        response.raise_for_status()
        if any(
            db.get("backend") == "trino"
            for db in response.json().get("result", [])
        ):
            return
        await asyncio.sleep(20)
    raise TimeoutError("Superset did not create the Trino database connection")


async def _setup_superset_users(ops_test: OpsTest) -> None:
    """Create ``trino_allowed`` and ``trino_blocked`` in Superset.

    Both get Alpha + sql_lab roles — identical Superset permissions.  Any
    difference in query outcome is therefore purely Trino/Ranger enforcement.
    """
    superset_url = await get_unit_url(
        ops_test, application=UI_NAME, unit=0, port=8088
    )
    api_token = superset_login(superset_url)

    for username, password in (
        (ALLOWED_USER, ALLOWED_PASSWORD),
        (BLOCKED_USER, BLOCKED_PASSWORD),
    ):
        ensure_user(
            superset_url, api_token, username, password, [ALPHA_ROLE_ID, SQLAB_ROLE_ID]
        )
        logger.info("Ensured Superset user %r", username)


async def _get_trino_db_id(
    ops_test: OpsTest, catalog_name: str = "testcat"
) -> int:
    """Return the Superset database connection ID for the given Trino catalog."""
    superset_url = await get_unit_url(
        ops_test, application=UI_NAME, unit=0, port=8088
    )
    session = await api_authentication(ops_test, superset_url)
    resp = session.get(f"{superset_url}/api/v1/database/", timeout=30)
    resp.raise_for_status()
    for db in resp.json().get("result", []):
        if db.get("backend") == "trino" and catalog_name in db.get(
            "database_name", ""
        ):
            return db["id"]
    raise RuntimeError(
        f"No Trino database connection for catalog {catalog_name!r} found in Superset. "
        "Did the trino-catalog relation fire?"
    )


async def _mcp_url(ops_test: OpsTest) -> str:
    """Return the MCP server URL for the Superset unit.

    ``unit.public_address`` is None for this k8s app, so use the same
    status-based address lookup as ``get_unit_url``.
    """
    base = await get_unit_url(ops_test, application=UI_NAME, unit=0, port=MCP_PORT)
    return f"{base}{MCP_ENDPOINT}"


# Hydra client secrets for the Trino test users (registered by token_url fixture).
_CLIENT_SECRET_SUFFIX = "_trino_secret"


def _secret(username: str) -> str:
    return f"{username}{_CLIENT_SECRET_SUFFIX}"


async def _setup_hydra_clients(ops_test: OpsTest) -> str:
    """Register ALLOWED_USER and BLOCKED_USER as Hydra clients.

    Returns the Hydra public token endpoint URL.
    """
    admin_url = await get_unit_url(
        ops_test, application=HYDRA_APP, unit=0, port=HYDRA_ADMIN_PORT
    )
    mcp_client_id = await get_mcp_oauth_client_id(ops_test, UI_NAME)
    for username in (ALLOWED_USER, BLOCKED_USER):
        create_hydra_client(
            admin_url,
            username,
            _secret(username),
            audience=[mcp_client_id],
        )
    hydra_base = await get_unit_url(
        ops_test, application=HYDRA_APP, unit=0, port=HYDRA_PUBLIC_PORT
    )
    return f"{hydra_base}/oauth2/token"


@pytest_asyncio.fixture(scope="module")
async def token_url(ops_test: OpsTest, deploy_trino_mcp) -> str:
    """Create Superset users, Hydra clients, and Ranger policies once per module.

    pytest gives each test *method* its own fresh class instance, so state set
    on ``self`` in one test (e.g. a prior ``self._token_url = ...``) is not
    visible in later tests — this must live in a fixture instead.
    """
    del deploy_trino_mcp
    await _setup_superset_users(ops_test)
    await _setup_ranger_users_and_policies(ops_test)
    url = await _setup_hydra_clients(ops_test)

    logger.info(
        "Waiting %ss for Ranger plugin to sync policies to Trino",
        RANGER_POLICY_SYNC_WAIT,
    )
    await asyncio.sleep(RANGER_POLICY_SYNC_WAIT)
    return url


# ── Test class ─────────────────────────────────────────────────────────────────


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy_trino_mcp")
class TestTrinoRBACViaImpersonation:
    """Validate Ranger RBAC enforcement through the Superset impersonation path.

    These tests are stateful and must run in order — the fixture setup is
    module-scoped and the Ranger policy grant is applied once in the first test.
    """

    async def test_setup_users_and_policies(self, token_url: str):
        """Create Superset users, Hydra clients, and Ranger policies."""
        assert token_url.endswith("/oauth2/token")

    async def test_impersonation_fires(self, ops_test: OpsTest, token_url: str):
        """Confirm X-Trino-User is the Superset username, not the service account.

        ``SELECT current_user`` returns the user Trino sees on the connection.
        If impersonation is off, it returns the Trino service account; if on, it
        returns the Superset session username.
        """
        db_id = await _get_trino_db_id(ops_test)
        url = await _mcp_url(ops_test)
        token = get_hydra_token(token_url, ALLOWED_USER, _secret(ALLOWED_USER))

        status, text = mcp_call(
            url,
            "execute_sql",
            {"request": {"database_id": db_id, "sql": "SELECT current_user"}},
            token,
        )

        assert status == 200, f"MCP returned {status}: {text[:200]}"
        try:
            rows = json.loads(text).get("rows", [])
        except Exception:
            rows = []

        # The single-column result must contain the Superset username.
        row_values = [list(r.values())[0] for r in rows if r]
        assert any(ALLOWED_USER in str(v) for v in row_values), (
            f"current_user did not return {ALLOWED_USER!r}; got {row_values}. "
            "Impersonation may be off — check impersonate_user on the DB connection."
        )

    async def test_allowed_user_can_query(self, ops_test: OpsTest, token_url: str):
        """``trino_allowed`` has a Ranger policy grant and must be able to query."""
        db_id = await _get_trino_db_id(ops_test)
        url = await _mcp_url(ops_test)
        token = get_hydra_token(token_url, ALLOWED_USER, _secret(ALLOWED_USER))

        # Cache-bust: execute_sql's result cache is keyed by SQL text, not
        # user , and would collide with the identical
        # query in test_blocked_user_denied_by_ranger otherwise.
        cache_buster = uuid.uuid4().hex
        status, text = mcp_call(
            url,
            "execute_sql",
            {
                "request": {
                    "database_id": db_id,
                    "sql": f"SHOW SCHEMAS -- allowed {cache_buster}",
                }
            },
            token,
        )

        assert status == 200, f"MCP returned {status}: {text[:200]}"
        assert "Error:" not in text, (
            f"{ALLOWED_USER!r} should be allowed but got error: {text[:300]}"
        )
        logger.info("%s query succeeded", ALLOWED_USER)

    async def test_blocked_user_denied_by_ranger(self, ops_test: OpsTest, token_url: str):
        """``trino_blocked`` has no Ranger policy and must be denied by Trino.

        The MCP layer does not raise an HTTP error for a query-level permission
        denial — it returns HTTP 200 with an error description in the body.
        We assert the body contains Trino's access-denied signal.
        """
        db_id = await _get_trino_db_id(ops_test)
        url = await _mcp_url(ops_test)
        token = get_hydra_token(token_url, BLOCKED_USER, _secret(BLOCKED_USER))

        # Cache-bust: must not reuse test_allowed_user_can_query's SQL text,
        # or this reads back that test's cached allowed result instead of a
        # fresh Ranger denial .
        cache_buster = uuid.uuid4().hex
        status, text = mcp_call(
            url,
            "execute_sql",
            {
                "request": {
                    "database_id": db_id,
                    "sql": f"SHOW SCHEMAS -- blocked {cache_buster}",
                }
            },
            token,
        )

        assert status == 200, f"Unexpected MCP transport error {status}: {text[:200]}"
        # Trino surfaces Ranger denials as "Access Denied" in the query error.
        assert any(
            phrase in text for phrase in ("Access Denied", "access denied", "Permission denied")
        ), (
            f"{BLOCKED_USER!r} should be denied but no access-denied signal in response: "
            f"{text[:400]}"
        )
        logger.info("%s correctly denied by Ranger", BLOCKED_USER)

    async def test_blocked_user_not_due_to_superset_rbac(
        self, ops_test: OpsTest, token_url: str
    ):
        """Confirm the denial is Trino-side, not a Superset RBAC rejection.

        Both users have identical Superset roles.  If Superset were blocking
        ``trino_blocked``, MCP would return a Superset permission message
        ("You don't have access"), not a Trino access-denied error.
        This test asserts that the MCP tool is actually reached (HTTP 200) and
        the error originates from Trino, not from Superset's permission layer.
        """
        url = await _mcp_url(ops_test)
        token = get_hydra_token(token_url, BLOCKED_USER, _secret(BLOCKED_USER))

        # get_instance_info does not touch Trino — must succeed for blocked user
        status, text = mcp_call(url, "get_instance_info", {}, token)
        assert status == 200, f"get_instance_info failed for {BLOCKED_USER}: {status}"

        try:
            info = json.loads(text)
            username = info.get("current_user", {}).get("username", "")
        except Exception:
            username = ""

        assert username == BLOCKED_USER, (
            f"Expected {BLOCKED_USER!r} authenticated in Superset, got {username!r}. "
            "The user may not exist or have wrong credentials."
        )
        logger.info(
            "%s is authenticated in Superset (Superset RBAC is not the blocker)",
            BLOCKED_USER,
        )
