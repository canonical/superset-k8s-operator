#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test helpers."""

import logging
from pathlib import Path

import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
NGINX_NAME = "nginx-ingress-integrator"


async def perform_superset_integrations(ops_test: OpsTest):
    """Integrate Superset charm with Nginx charm.

    Args:
        ops_test: PyTest object.
    """
    await ops_test.model.integrate(APP_NAME, NGINX_NAME)


async def get_unit_url(
    ops_test: OpsTest, application, unit, port, protocol="https"
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
        await ops_test.model.applications[APP_NAME]
        .units[0]
        .run_action("restart")
    )
    await action.wait()
