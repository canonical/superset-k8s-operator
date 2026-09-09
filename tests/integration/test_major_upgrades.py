# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Superset charm upgrades integration tests."""

import asyncio
import logging

import pytest
import pytest_asyncio
import requests
from integration.helpers import (
    APP_NAME,
    POSTGRES_NAME,
    REDIS_NAME,
    SECRET_KEY,
    create_signing_keys_secret,
    get_unit_url,
    grant_signing_keys_secret,
    perform_superset_integrations,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest_asyncio.fixture(name="deploy-major-upgrade", scope="module")
async def deploy(ops_test: OpsTest):
    """Deploy the app."""
    if ops_test.request.config.getoption("--no-deploy") and ops_test.request.config.getoption(
        "--model"
    ):
        logger.info("Skipping base deploy; reusing existing model %s", ops_test.model_name)
        return
    await asyncio.gather(
        ops_test.model.deploy(POSTGRES_NAME, channel="14", trust=True),
        ops_test.model.deploy(REDIS_NAME, channel="edge", trust=True),
    )
    await ops_test.model.wait_for_idle(
        apps=[POSTGRES_NAME, REDIS_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=2000,
    )
    # The released charm still takes the secret key as plain config. The
    # refresh below is where the deployment moves onto the signing keys
    # secret, which is the documented migration.
    superset_config = {
        "superset-secret-key": SECRET_KEY,
        "load-examples": True,
    }
    await ops_test.model.deploy(
        APP_NAME,
        channel="5/stable",
        config=superset_config,
    )
    await perform_superset_integrations(ops_test, APP_NAME)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-major-upgrade")
class TestUpgrade:
    """Integration test for Superset charm upgrade from previous major release."""

    async def test_upgrade(
        self, ops_test: OpsTest, charm: str, charm_image: str
    ):
        """Builds the current charm and refreshes the current deployment."""
        resources = {"superset-image": charm_image}

        await ops_test.model.applications[APP_NAME].refresh(
            path=str(charm), resources=resources
        )

        # `superset-secret-key` is gone from the refreshed charm, so the unit
        # blocks until the signing keys secret is created and granted.
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status="blocked",
            raise_on_blocked=False,
            timeout=600,
        )

        signing_keys_secret_id = await create_signing_keys_secret(ops_test)
        await grant_signing_keys_secret(ops_test, APP_NAME)
        await ops_test.model.applications[APP_NAME].set_config(
            {"signing-keys-secret-id": signing_keys_secret_id}
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
