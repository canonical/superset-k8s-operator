# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test config."""

import logging

import pytest
import pytest_asyncio
from helpers import (
    CHARM_FUNCTIONS,
    METADATA,
    NGINX_NAME,
    POSTGRES_NAME,
    REDIS_NAME,
    UI_NAME,
    perform_superset_integrations,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy", scope="module")
async def deploy(ops_test: OpsTest):
    """Deploy the app."""
    charm = await ops_test.build_charm(".")
    resources = {
        "superset-image": METADATA["resources"]["superset-image"][
            "upstream-source"
        ]
    }
    await ops_test.model.deploy(POSTGRES_NAME, channel="14", trust=True)
    await ops_test.model.deploy(REDIS_NAME, channel="edge", trust=True)
    await ops_test.model.deploy(NGINX_NAME, trust=True)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[NGINX_NAME, POSTGRES_NAME, REDIS_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=600,
        )

    for function, alias in CHARM_FUNCTIONS.items():
        app_name = f"superset-k8s-{alias}"
        await ops_test.model.deploy(
            charm,
            resources=resources,
            application_name=app_name,
            config={"charm-function": function},
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
