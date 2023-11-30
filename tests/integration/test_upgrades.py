# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Superset charm upgrades integration tests."""

import asyncio
import logging

import pytest
import pytest_asyncio
import requests
from helpers import (
    APP_NAME,
    METADATA,
    POSTGRES_NAME,
    REDIS_NAME,
    SUPERSET_SECRET_KEY,
    get_unit_url,
    perform_superset_integrations,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy-upgrade", scope="module")
async def deploy(ops_test: OpsTest):
    """Deploy the app."""
    asyncio.gather(
        ops_test.model.deploy(POSTGRES_NAME, channel="14", trust=True),
        ops_test.model.deploy(REDIS_NAME, channel="edge", trust=True),
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[POSTGRES_NAME, REDIS_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=2000,
        )
        superset_config = {
            "superset-secret-key": SUPERSET_SECRET_KEY,
        }
        await ops_test.model.deploy(
            APP_NAME, channel="stable", config=superset_config
        )
        await perform_superset_integrations(ops_test, APP_NAME)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-upgrade")
class TestUpgrade:
    """Integration test for Superset charm upgrade from previous release."""

    async def test_upgrade(self, ops_test: OpsTest):
        """Builds the current charm and refreshes the current deployment."""
        charm = await ops_test.build_charm(".")
        resources = {
            "superset-image": METADATA["resources"]["superset-image"][
                "upstream-source"
            ]
        }

        await ops_test.model.applications[APP_NAME].refresh(
            path=str(charm), resources=resources
        )
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=600,
        )

        assert (
            ops_test.model.applications[APP_NAME].units[0].workload_status
            == "active"
        )

    async def test_ui_relation(self, ops_test: OpsTest):
        """Perform GET request on the Superset UI host."""
        url = await get_unit_url(
            ops_test, application=APP_NAME, unit=0, port=8088
        )
        logger.info("curling app address: %s", url)

        response = requests.get(url, timeout=300)
        assert response.status_code == 200
