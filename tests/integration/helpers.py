#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test helpers."""

import logging
from pathlib import Path
import json
import requests
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
NGINX_NAME = "nginx-ingress-integrator"
POSTGRES_NAME = "postgresql-k8s"
REDIS_NAME = "redis-k8s"
UI_NAME = "superset-k8s-ui"
CHARM_FUNCTIONS = {"app-gunicorn": "ui", "beat": "beat", "worker": "worker"}
API_AUTH_PAYLOAD = {
    "username": "admin",
    "password": "admin",
    "provider": "db",
}


async def deploy_and_relate_superset_charm(
    ops_test: OpsTest, app_name, config
):
    charm = await ops_test.build_charm(".")
    resources = {
        "superset-image": METADATA["resources"]["superset-image"][
            "upstream-source"
        ]
    }
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=app_name,
        config=config,
        num_units=1,
    )

    await ops_test.model.wait_for_idle(
        apps=[app_name],
        status="blocked",
        raise_on_blocked=False,
        timeout=600,
    )

    await perform_superset_integrations(ops_test, app_name)

    assert (
        ops_test.model.applications[app_name].units[0].workload_status
        == "active"
    )


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


async def get_access_token(ops_test: OpsTest, base_url, headers):
    response = requests.post(
        base_url + "/api/v1/security/login", json=API_AUTH_PAYLOAD
    )
    access_token = response.json()
    headers = {"Authorization": "Bearer " + access_token["access_token"]}
    return headers


async def get_chart_count(ops_test: OpsTest, base_url, headers):
    chart_response = requests.get(base_url + "/api/v1/chart/", headers=headers)
    charts = chart_response.json()
    chart_count = charts["count"]
    return chart_count


async def simulate_crash(ops_test: OpsTest):
    # Destroy charms
    for function, alias in CHARM_FUNCTIONS.items():
        app_name = f"superset-k8s-{alias}"
        await ops_test.model.applications[app_name].destroy(force=True)
        await ops_test.model.block_until(
            lambda: app_name not in ops_test.model.applications
        )

    # Deploy charms again
    for function, alias in CHARM_FUNCTIONS.items():
        superset_config = {"charm-function": function}
        app_name = f"superset-k8s-{alias}"
        deploy_and_relate_superset_charm(ops_test, app_name, superset_config)
