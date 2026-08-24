# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test config."""

import asyncio
import logging
from pathlib import Path

import pytest
import pytest_asyncio
from integration.helpers import (
    CHARM_FUNCTIONS,
    POSTGRES_NAME,
    REDIS_NAME,
    SUPERSET_SECRET_KEY,
    TRAEFIK_CONFIG,
    TRAEFIK_NAME,
    UI_NAME,
    deploy_and_relate_superset_charm,
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
    return Path(charms[0]).resolve()


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy", scope="module")
async def deploy(ops_test: OpsTest, charm: str, charm_image: str):
    """Deploy the app."""
    await asyncio.gather(
        ops_test.model.deploy(POSTGRES_NAME, channel="14", trust=True),
        ops_test.model.deploy(REDIS_NAME, channel="edge", trust=True),
        ops_test.model.deploy(
            TRAEFIK_NAME,
            channel="latest/stable",
            config=TRAEFIK_CONFIG,
            trust=True,
        ),
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[POSTGRES_NAME, REDIS_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=2000,
        )
        resources = {"superset-image": charm_image}

        # Iterate through UI, worker and beat charms
        for function, alias in CHARM_FUNCTIONS.items():
            app_name = f"superset-k8s-{alias}"
            superset_config = {
                "charm-function": function,
                "superset-secret-key": SUPERSET_SECRET_KEY,
                "server-alias": UI_NAME,
                "feature-flags": "GLOBAL_ASYNC_QUERIES",
            }

            # Load examples and enable MCP for the UI charm
            if app_name == UI_NAME:
                superset_config.update({
                    "load-examples": "True",
                    "mcp-enabled": "True",
                    "mcp-auth-enabled": "True",
                    "mcp-jwt-secret": "a" * 64,
                    "feature-flags": "GLOBAL_ASYNC_QUERIES,RLS_IN_SQLLAB",
                })

            await deploy_and_relate_superset_charm(
                ops_test, app_name, superset_config, charm, resources
            )

        await ops_test.model.integrate(
            f"{UI_NAME}:ingress", f"{TRAEFIK_NAME}:ingress"
        )
        await ops_test.model.wait_for_idle(
            apps=[TRAEFIK_NAME, UI_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=300,
        )

        assert (
            ops_test.model.applications[UI_NAME].units[0].workload_status
            == "active"
        )
