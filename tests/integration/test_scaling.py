# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Superset charm scaling integration tests."""

import asyncio
import logging

import pytest
import pytest_asyncio
from integration.helpers import (
    POSTGRES_NAME,
    REDIS_NAME,
    SCALABLE_SERVICES,
    SUPERSET_SECRET_KEY,
    UI_NAME,
    deploy_and_relate_superset_charm,
    get_active_workers,
    scale,
)
from pytest_operator.plugin import OpsTest

SCALABLE_APPS = ["superset-k8s-ui", "superset-k8s-worker"]

logger = logging.getLogger(__name__)


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy-scale", scope="module")
async def deploy(ops_test: OpsTest, charm: str, charm_image: str):
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

        resources = {
            "superset-image": charm_image,
        }
        # Iterate through UI and worker charms
        for function, alias in SCALABLE_SERVICES.items():
            app_name = f"superset-k8s-{alias}"
            superset_config = {
                "charm-function": function,
                "superset-secret-key": SUPERSET_SECRET_KEY,
                "server-alias": UI_NAME,
            }

            await deploy_and_relate_superset_charm(
                ops_test, app_name, superset_config, charm, resources
            )

        assert (
            ops_test.model.applications[app_name].units[0].workload_status
            == "active"
        )


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-scale")
class TestScaling:
    """Integration tests for Superset charm."""

    async def test_scaling_up(self, ops_test: OpsTest):
        """Scale Superset charms up to 2 units."""
        for service in SCALABLE_APPS:
            await scale(ops_test, app=service, units=2)
            assert len(ops_test.model.applications[service].units) == 2

        active_workers = await get_active_workers(ops_test)
        assert len(active_workers) == 2

    async def test_scaling_down(self, ops_test: OpsTest):
        """Scale Superset charm down to 1 unit."""
        for service in SCALABLE_APPS:
            await scale(ops_test, app=service, units=1)
            assert len(ops_test.model.applications[service].units) == 1

        active_workers = await get_active_workers(ops_test)
        assert len(active_workers) == 1
