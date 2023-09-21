#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test helpers."""

import logging
from pathlib import Path
import psycopg2
import itertools

import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
NGINX_NAME = "nginx-ingress-integrator"
POSTGRES_NAME = "postgresql-k8s"
REDIS_NAME = "redis-k8s"
UI_NAME = "superset-k8s-ui"
CHARM_FUNCTIONS = {"app-gunicorn": "ui", "beat": "beat", "worker": "worker"}


async def perform_superset_integrations(ops_test: OpsTest, app_name):
    """Integrate Superset charm with Nginx charm.

    Args:
        ops_test: PyTest object
        app_name: The name of the Superset application (ui, worker or beat)
    """
    await ops_test.model.integrate(app_name, POSTGRES_NAME)

    await ops_test.model.wait_for_idle(
        apps=[app_name], status="blocked", raise_on_blocked=False, timeout=180
    )

    await ops_test.model.integrate(app_name, REDIS_NAME)

    await ops_test.model.wait_for_idle(
        apps=[app_name], status="active", raise_on_blocked=False, timeout=1500
    )


async def get_unit_url(
    ops_test: OpsTest, application, unit, port, protocol="http"
):
    """Return unit URL from the model.

    Args:
        ops_test: PyTest object.
        application: Name of the application.
        unit: Number of the unit.
        port: Port number of the URL.
        protocol: Transfer protocol (default: https).

    Returns:
        Unit URL of the form {protocol}://{address}:{port}
    """
    status = await ops_test.model.get_status()  # noqa: F821
    address = status["applications"][application]["units"][
        f"{application}/{unit}"
    ]["address"]
    return f"{protocol}://{address}:{port}"


async def restart_application(ops_test: OpsTest):
    """Restart Superset application.

    Args:
        ops_test: PyTest object.
    """
    action = (
        await ops_test.model.applications[UI_NAME]
        .units[0]
        .run_action("restart")
    )
    await action.wait()


async def get_unit_address(ops_test: OpsTest, unit_name: str) -> str:
    """Get unit IP address.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit

    Returns:
        IP address of the unit
    """
    status = await ops_test.model.get_status()
    return status["applications"][unit_name.split("/")[0]].units[unit_name]["address"]


async def check_database_creation(ops_test: OpsTest):
    """Checks that database and tables are successfully created for the application.

    Args:
        ops_test: The ops test framework
        database: Name of the database that should have been created
        database_app_name: Application name of the database charm
    """
    password = "admin"

    for unit in ops_test.model.applications[POSTGRES_NAME].units:
        unit_address = await get_unit_address(ops_test, unit.name)

        # Ensure database exists in PostgreSQL.
        output = await execute_query_on_unit(
            unit_address,
            password,
            "SELECT datname FROM pg_database;",
        )
        assert "superset" in output

        # Ensure that application tables exist in the database
        output = await execute_query_on_unit(
            unit_address,
            password,
            "SELECT table_name FROM information_schema.tables;",
            database="superset",
        )
        assert len(output)

async def execute_query_on_unit(
    unit_address: str,
    password: str,
    query: str,
    database: str = "superset",
):
    """Execute given PostgreSQL query on a unit.

    Args:
        unit_address: The public IP address of the unit to execute the query on.
        password: The Superset admin password.
        query: Query to execute.
        database: Optional database to connect to (defaults to superset database).

    Returns:
        The result of the query.
    """
    with psycopg2.connect(
        f"dbname='{database}' user='operator' host='{unit_address}'"
        f"password='{password}'"
    ) as connection, connection.cursor() as cursor:
        cursor.execute(query)
        output = list(itertools.chain(*cursor.fetchall()))
    return output
