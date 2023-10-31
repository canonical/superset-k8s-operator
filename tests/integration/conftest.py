# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test config."""

import logging

import pytest
import pytest_asyncio
from helpers import (
    CHARM_FUNCTIONS,
    NGINX_NAME,
    POSTGRES_NAME,
    REDIS_NAME,
    UI_NAME,
    deploy_and_relate_superset_charm,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy", scope="module")
async def deploy(ops_test: OpsTest):
    """Deploy the app."""
    await ops_test.model.deploy(POSTGRES_NAME, channel="14", trust=True)
    await ops_test.model.deploy(REDIS_NAME, channel="edge", trust=True)
    await ops_test.model.deploy(NGINX_NAME, trust=True)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[NGINX_NAME, POSTGRES_NAME, REDIS_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=2000,
        )

    for function, alias in CHARM_FUNCTIONS.items():
        app_name = f"superset-k8s-{alias}"
        superset_config = {"charm-function": function}
        if app_name == UI_NAME:
            superset_config.update({"load-examples": True})
        await deploy_and_relate_superset_charm(
            ops_test, app_name, superset_config
        )

    await ops_test.model.integrate(UI_NAME, NGINX_NAME)
    await ops_test.model.wait_for_idle(
        apps=[NGINX_NAME, UI_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=300,
    )

    assert (
        ops_test.model.applications[UI_NAME].units[0].workload_status
        == "active"
    )
