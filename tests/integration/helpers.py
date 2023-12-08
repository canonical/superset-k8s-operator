#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test helpers."""

import logging
from pathlib import Path

import requests
import yaml
from celery import Celery
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
NGINX_NAME = "nginx-ingress-integrator"
POSTGRES_NAME = "postgresql-k8s"
REDIS_NAME = "redis-k8s"
UI_NAME = "superset-k8s-ui"
CHARM_FUNCTIONS = {"app-gunicorn": "ui", "beat": "beat", "worker": "worker"}
SCALABLE_SERVICES = {"app-gunicorn": "ui", "worker": "worker"}
API_AUTH_PAYLOAD = {
    "username": "admin",
    "password": "admin",
    "provider": "db",
}
APP_NAME = "superset-k8s"
UI_CONFIG = {
    "charm-function": "app-gunicorn",
    "superset-secret-key": "juyIKSS7cFAqJlV",
}
SUPERSET_SECRET_KEY = "juyIKSS7cFAqJlV"  # nosec


async def deploy_and_relate_superset_charm(
    ops_test: OpsTest, app_name, config, charm, resources
):
    """Deploy Superset charm..

    Args:
        ops_test: PyTest object.
        app_name: Name of the application.
        config: Configuration of the charm.
        charm: The packed charm.
        resources: The OCI image.
    """
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


async def get_access_token(ops_test: OpsTest, base_url):
    """Get access token of the `admin` user.

    Args:
        ops_test: PyTest object.
        base_url: Superset URL.

    Returns:
        headers: Request headers with access token.
    """
    response = requests.post(
        base_url + "/api/v1/security/login", json=API_AUTH_PAYLOAD, timeout=30
    )
    access_token = response.json()
    headers = {"Authorization": "Bearer " + access_token["access_token"]}
    return headers


async def get_chart_count(ops_test: OpsTest, url, headers):
    """Count Superset charts.

    Args:
        ops_test: PyTest object.
        url: Superset URL.
        headers: Request headers with access token.

    Returns:
        Count of Superset charts.
    """
    chart_response = requests.get(
        url + "/api/v1/chart/", headers=headers, timeout=30
    )
    charts = chart_response.json()
    return charts["count"]


async def delete_chart(ops_test: OpsTest, url, headers):
    """Delete chart example chart `131`.

    Args:
        ops_test: PyTest object.
        url: Superset URL.
        headers: Request headers with access token.
    """
    try:
        response = requests.delete(
            url + "/api/v1/chart/131", headers=headers, timeout=30
        )
    except Exception as e:
        logger.error(f"Error deleting chart caused by: {e}")

    assert response.status_code == 200


async def simulate_crash(ops_test: OpsTest):
    """Simulate the crash of the Superset charm.

    Args:
        ops_test: PyTest object.
    """
    # Destroy charm
    await ops_test.model.applications[UI_NAME].destroy(force=True)
    await ops_test.model.block_until(
        lambda: UI_NAME not in ops_test.model.applications
    )

    # Deploy charms again
    charm = await ops_test.build_charm(".")
    resources = {
        "superset-image": METADATA["resources"]["superset-image"][
            "upstream-source"
        ]
    }
    await deploy_and_relate_superset_charm(
        ops_test, UI_NAME, UI_CONFIG, charm, resources
    )


async def scale(ops_test: OpsTest, app, units):
    """Scale the application to the provided number and wait for idle.

    Args:
        ops_test: PyTest object.
        app: Application to be scaled.
        units: Number of units required.
    """
    await ops_test.model.applications[app].scale(scale=units)

    # Wait for model to settle
    await ops_test.model.wait_for_idle(
        apps=[app],
        status="active",
        idle_period=30,
        raise_on_blocked=True,
        timeout=600,
        wait_for_exact_units=units,
    )


async def get_active_workers(ops_test: OpsTest):
    """Get active superset workers from Redis broker.

    Args:
        ops_test: PyTest object.

    Returns:
        active_workers: dictionary of active works and their jobs.
    """
    status = await ops_test.model.get_status()  # noqa: F821
    redis_ip = status["applications"][REDIS_NAME]["units"][f"{REDIS_NAME}/0"][
        "address"
    ]
    app = Celery("superset", broker=f"redis://{redis_ip}:6379/4")
    active_workers = app.control.inspect().active()
    return active_workers
