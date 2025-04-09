# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Superset charm upgrades integration tests."""

import asyncio
import logging
from pathlib import Path

import pytest
import pytest_asyncio
import requests
from integration.helpers import (
    APP_NAME,
    POSTGRES_NAME,
    REDIS_NAME,
    SUPERSET_SECRET_KEY,
    get_unit_url,
    perform_superset_integrations,
)
from pytest import FixtureRequest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module", name="charm_image")
def charm_image_fixture(request: FixtureRequest) -> str:
    """The OCI image for charm."""
    charm_image = request.config.getoption("--superset-image")
    assert (
        charm_image
    ), "--superset-image argument is required which should contain the name of the OCI image."
    return charm_image


@pytest_asyncio.fixture(scope="module", name="charm")
async def charm_fixture(
    request: FixtureRequest, ops_test: OpsTest
) -> str | Path:
    """Fetch the path to charm."""
    charms = request.config.getoption("--charm-file")
    if not charms:
        charm = await ops_test.build_charm(".")
        assert charm, "Charm not built"
        return charm
    return charms[0]


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy-upgrade", scope="module")
async def deploy(ops_test: OpsTest):
    """Deploy the app."""
    asyncio.gather(
        ops_test.model.deploy(POSTGRES_NAME, channel="14", trust=True),
        ops_test.model.deploy(REDIS_NAME, channel="edge", trust=True),
    )
    await ops_test.model.wait_for_idle(
        apps=[POSTGRES_NAME, REDIS_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=2000,
    )
    superset_config = {"superset-secret-key": SUPERSET_SECRET_KEY}
    await ops_test.model.deploy(
        APP_NAME,
        channel="edge",
        config=superset_config,
    )
    await perform_superset_integrations(ops_test, APP_NAME)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-upgrade")
class TestUpgrade:
    """Integration test for Superset charm upgrade from previous release."""

    async def test_upgrade(
        self, ops_test: OpsTest, charm: str, charm_image: str
    ):
        """Builds the current charm and refreshes the current deployment."""
        resources = {"superset-image": charm_image}

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
