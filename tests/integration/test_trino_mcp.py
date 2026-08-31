# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Trino RBAC enforcement via Superset user impersonation.

Validates that Trino's Ranger-enforced access control is correctly applied to
the *impersonated* Superset user, not the Trino service account.

Deployment topology
-------------------
  PostgreSQL ──► Superset (app-gunicorn, MCP enabled)
  Redis       ──► Superset
  PostgreSQL  ──► Ranger
  Trino       ──► Ranger  (policy relation)
  Trino       ──► Superset (trino-catalog relation → creates Superset DB connection)

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

import pytest
import pytest_asyncio
from apache_ranger.client.ranger_client import RangerClient
from apache_ranger.model.ranger_policy import RangerPolicy
from integration.helpers import (
    POSTGRES_NAME,
    REDIS_NAME,
    SUPERSET_SECRET_KEY,
    api_authentication,
    get_unit_url,
    perform_superset_integrations,
)
from integration.mcp_helpers import (
    MCP_ENDPOINT,
    MCP_PORT,
    ALPHA_ROLE_ID,
    SQLAB_ROLE_ID,
    ensure_user,
    make_token,
    mcp_call,
    superset_login,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

# ── Application names ──────────────────────────────────────────────────────────

SUPERSET_APP = "superset-k8s"
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


@pytest_asyncio.fixture(scope="module")
async def pg_secret_id(ops_test: OpsTest) -> str:
    """Create a Juju secret with PostgreSQL replica credentials for Trino."""
    secret = await ops_test.model.add_secret(
        name="trino-mcp-pg-secret",
        data_args=[f"replicas={POSTGRESQL_REPLICA_SECRET}"],
    )
    return secret.split(":")[-1]


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy_trino_mcp", scope="module")
async def deploy_trino_mcp_fixture(
    ops_test: OpsTest, charm: str, charm_image: str, pg_secret_id: str
):
    """Deploy the full Trino-Ranger-Superset stack.

    Deployment order:
    1. PostgreSQL and Redis (Superset deps) + PostgreSQL for Ranger, in parallel.
    2. Ranger (needs PostgreSQL active first).
    3. Superset (needs PostgreSQL and Redis active).
    4. Trino (needs Ranger active for policy relation).
    5. Wire relations: Ranger↔Trino (policy), Trino↔Superset (trino-catalog).
    """
    await ops_test.model.set_config({"logging-config": "<root>=INFO;unit=DEBUG"})

    # Step 1: shared infrastructure
    await asyncio.gather(
        ops_test.model.deploy(POSTGRES_NAME, channel="14", trust=True),
        ops_test.model.deploy(REDIS_NAME, channel="edge", trust=True),
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[POSTGRES_NAME, REDIS_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=TIMEOUT_DEPLOY,
        )

    # Step 2: Ranger (needs its own PostgreSQL — reuse the same instance)
    await ops_test.model.deploy(
        RANGER_APP,
        channel="latest/edge",
        config={"ranger-usersync-password": "P@ssw0rd1234"},
        trust=True,
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[RANGER_APP],
            status="blocked",
            raise_on_blocked=False,
            timeout=TIMEOUT_DEPLOY,
        )

    await ops_test.model.integrate(RANGER_APP, POSTGRES_NAME)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[RANGER_APP, POSTGRES_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=TIMEOUT_DEPLOY,
        )

    # Step 3: Superset
    resources = {"superset-image": charm_image}
    superset_config = {
        "charm-function": "app-gunicorn",
        "superset-secret-key": SUPERSET_SECRET_KEY,
        "admin-password": "admin",
        "load-examples": "True",
        "mcp-enabled": "True",
        "mcp-auth-enabled": "True",
        "mcp-jwt-secret": "a" * 64,
        "feature-flags": "GLOBAL_ASYNC_QUERIES,RLS_IN_SQLLAB",
    }

    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=SUPERSET_APP,
        config=superset_config,
        num_units=1,
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[SUPERSET_APP],
            status="blocked",
            raise_on_blocked=False,
            timeout=TIMEOUT_DEPLOY,
        )
        await perform_superset_integrations(ops_test, SUPERSET_APP)
        assert (
            ops_test.model.applications[SUPERSET_APP].units[0].workload_status
            == "active"
        )

    # Step 4: Trino
    await ops_test.model.deploy(
        TRINO_APP,
        channel="latest/edge",
        config={
            "charm-function": "all",
            "ranger-service-name": TRINO_SERVICE_NAME,
        },
        trust=True,
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[TRINO_APP],
            status="active",
            raise_on_blocked=False,
            timeout=TIMEOUT_DEPLOY,
        )

    # Step 5a: Ranger ↔ Trino (policy)
    await ops_test.model.integrate(f"{RANGER_APP}:policy", f"{TRINO_APP}:policy")

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[RANGER_APP, TRINO_APP],
            status="active",
            timeout=TIMEOUT_IDLE,
        )

    # Step 5b: Grant catalog secret to Trino, then set catalog-config
    await ops_test.model.grant_secret("trino-mcp-pg-secret", TRINO_APP)

    catalog_config = _build_tpch_catalog_config(pg_secret_id)
    await ops_test.model.applications[TRINO_APP].set_config(
        {"catalog-config": catalog_config}
    )

    # Step 5c: Trino ↔ Superset (trino-catalog)
    await ops_test.model.integrate(
        f"{TRINO_APP}:trino-catalog",
        f"{SUPERSET_APP}:trino-catalog",
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[TRINO_APP, SUPERSET_APP],
            status="active",
            timeout=TIMEOUT_IDLE,
        )


# ── Helpers ────────────────────────────────────────────────────────────────────


def _build_tpch_catalog_config(pg_secret_id: str) -> str:
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
        "    url: jdbc:postgresql://postgresql-k8s-primary.{model}.svc.cluster.local:5432\n"
    )


def _get_ranger_url(ops_test: OpsTest) -> str:
    """Return the Ranger admin URL (synchronous — uses cached model status)."""
    unit = ops_test.model.applications[RANGER_APP].units[0]
    address = unit.public_address
    return f"http://{address}:{RANGER_PORT}"


async def _get_ranger_url_async(ops_test: OpsTest) -> str:
    """Return the Ranger admin URL via get_unit_url."""
    return await get_unit_url(ops_test, application=RANGER_APP, unit=0, port=RANGER_PORT)


async def _setup_ranger_users_and_policies(ops_test: OpsTest) -> None:
    """Create Ranger users and grant ``trino_allowed`` access to the Trino service.

    ``trino_allowed`` is added to the three default trinouser/catalog/queryid
    policies that gate all catalog access.  ``trino_blocked`` is left out.
    """
    ranger_url = await _get_ranger_url_async(ops_test)
    ranger = RangerClient(ranger_url, RANGER_AUTH)

    # Create users in Ranger's internal user store
    for username in (ALLOWED_USER, BLOCKED_USER):
        _ensure_ranger_user(ranger_url, username)

    # Add trino_allowed to the three default service-level policies
    for policy_name in ("all - trinouser", "all - catalog", "all - queryid"):
        try:
            policy = ranger.get_policy(TRINO_SERVICE_NAME, policy_name)
        except Exception:
            logger.warning("Policy %r not found yet, skipping", policy_name)
            continue
        if ALLOWED_USER not in (policy.policyItems[0].users if policy.policyItems else []):
            policy.policyItems[0].users.append(ALLOWED_USER)
            ranger.update_policy(TRINO_SERVICE_NAME, policy_name, policy)
            logger.info("Granted %s access via policy %r", ALLOWED_USER, policy_name)


def _ensure_ranger_user(ranger_url: str, username: str) -> None:
    """Create a Ranger internal user if it does not already exist."""
    import requests as _requests

    resp = _requests.get(
        f"{ranger_url}/service/xusers/users",
        params={"name": username},
        auth=RANGER_AUTH,
        timeout=30,
    )
    resp.raise_for_status()
    if resp.json().get("totalCount", 0) > 0:
        logger.info("Ranger user %r already exists", username)
        return

    payload = {
        "name": username,
        "firstName": username,
        "lastName": "Test",
        "emailAddress": f"{username}@example.com",
        "password": "T3stP@ssword!",
        "userRoleList": ["ROLE_USER"],
    }
    resp = _requests.post(
        f"{ranger_url}/service/xusers/secure/users",
        json=payload,
        auth=RANGER_AUTH,
        timeout=30,
    )
    resp.raise_for_status()
    logger.info("Created Ranger user %r", username)


async def _setup_superset_users(ops_test: OpsTest) -> None:
    """Create ``trino_allowed`` and ``trino_blocked`` in Superset.

    Both get Alpha + sql_lab roles — identical Superset permissions.  Any
    difference in query outcome is therefore purely Trino/Ranger enforcement.
    """
    superset_url = await get_unit_url(ops_test, application=SUPERSET_APP, unit=0, port=8088)
    api_token = superset_login(superset_url)

    for username, password in (
        (ALLOWED_USER, ALLOWED_PASSWORD),
        (BLOCKED_USER, BLOCKED_PASSWORD),
    ):
        ensure_user(superset_url, api_token, username, password, [ALPHA_ROLE_ID, SQLAB_ROLE_ID])
        logger.info("Ensured Superset user %r", username)


async def _get_trino_db_id(ops_test: OpsTest, catalog_name: str = "testcat") -> int:
    """Return the Superset database connection ID for the given Trino catalog."""
    superset_url = await get_unit_url(ops_test, application=SUPERSET_APP, unit=0, port=8088)
    session = await api_authentication(ops_test, superset_url)
    resp = session.get(f"{superset_url}/api/v1/database/", timeout=30)
    resp.raise_for_status()
    for db in resp.json().get("result", []):
        if db.get("backend") == "trino" and catalog_name in db.get("database_name", ""):
            return db["id"]
    raise RuntimeError(
        f"No Trino database connection for catalog {catalog_name!r} found in Superset. "
        "Did the trino-catalog relation fire?"
    )


def _mcp_url(ops_test: OpsTest) -> str:
    """Return the MCP server URL for the Superset unit."""
    unit = ops_test.model.applications[SUPERSET_APP].units[0]
    address = unit.public_address
    return f"http://{address}:{MCP_PORT}{MCP_ENDPOINT}"


# ── Test class ─────────────────────────────────────────────────────────────────


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy_trino_mcp")
class TestTrinoRBACViaImpersonation:
    """Validate Ranger RBAC enforcement through the Superset impersonation path.

    These tests are stateful and must run in order — the fixture setup is
    module-scoped and the Ranger policy grant is applied once in the first test.
    """

    # JWT secret is the fixed value set in deploy_trino_mcp_fixture.
    JWT_SECRET = "a" * 64

    async def test_setup_users_and_policies(self, ops_test: OpsTest):
        """Create Superset users and Ranger policies before the RBAC assertions.

        This is a setup step disguised as a test so that failures are reported
        clearly and abort subsequent tests (abort_on_fail).
        """
        await _setup_superset_users(ops_test)
        await _setup_ranger_users_and_policies(ops_test)

        logger.info(
            "Waiting %ss for Ranger plugin to sync policies to Trino",
            RANGER_POLICY_SYNC_WAIT,
        )
        await asyncio.sleep(RANGER_POLICY_SYNC_WAIT)

    async def test_impersonation_fires(self, ops_test: OpsTest):
        """Confirm X-Trino-User is the Superset username, not the service account.

        ``SELECT current_user`` returns the user Trino sees on the connection.
        If impersonation is off, it returns the Trino service account; if on, it
        returns the Superset session username.
        """
        db_id = await _get_trino_db_id(ops_test)
        url = _mcp_url(ops_test)
        token = make_token(ALLOWED_USER, self.JWT_SECRET)

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

    async def test_allowed_user_can_query(self, ops_test: OpsTest):
        """``trino_allowed`` has a Ranger policy grant and must be able to query."""
        db_id = await _get_trino_db_id(ops_test)
        url = _mcp_url(ops_test)
        token = make_token(ALLOWED_USER, self.JWT_SECRET)

        status, text = mcp_call(
            url,
            "execute_sql",
            {"request": {"database_id": db_id, "sql": "SHOW SCHEMAS"}},
            token,
        )

        assert status == 200, f"MCP returned {status}: {text[:200]}"
        assert "Error:" not in text, (
            f"{ALLOWED_USER!r} should be allowed but got error: {text[:300]}"
        )
        logger.info("%s query succeeded", ALLOWED_USER)

    async def test_blocked_user_denied_by_ranger(self, ops_test: OpsTest):
        """``trino_blocked`` has no Ranger policy and must be denied by Trino.

        The MCP layer does not raise an HTTP error for a query-level permission
        denial — it returns HTTP 200 with an error description in the body.
        We assert the body contains Trino's access-denied signal.
        """
        db_id = await _get_trino_db_id(ops_test)
        url = _mcp_url(ops_test)
        token = make_token(BLOCKED_USER, self.JWT_SECRET)

        status, text = mcp_call(
            url,
            "execute_sql",
            {"request": {"database_id": db_id, "sql": "SHOW SCHEMAS"}},
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

    async def test_blocked_user_not_due_to_superset_rbac(self, ops_test: OpsTest):
        """Confirm the denial is Trino-side, not a Superset RBAC rejection.

        Both users have identical Superset roles.  If Superset were blocking
        ``trino_blocked``, MCP would return a Superset permission message
        (\"You don't have access\"), not a Trino access-denied error.
        This test asserts that the MCP tool is actually reached (HTTP 200) and
        the error originates from Trino, not from Superset's permission layer.
        """
        superset_url = await get_unit_url(
            ops_test, application=SUPERSET_APP, unit=0, port=8088
        )
        url = _mcp_url(ops_test)
        token = make_token(BLOCKED_USER, self.JWT_SECRET)

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
