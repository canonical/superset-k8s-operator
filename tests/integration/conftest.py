# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test config."""

import logging

import pytest
import pytest_asyncio
from helpers import (
    APP_NAME,
    METADATA,
    NGINX_NAME,
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

    # Deploy superset, tls and nginx charms
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=APP_NAME,
        num_units=1,
    )

    await ops_test.model.deploy(NGINX_NAME, trust=True)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[NGINX_NAME, APP_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=600,
        )

        await perform_superset_integrations(ops_test)

        await ops_test.model.wait_for_idle(
            apps=[NGINX_NAME, APP_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=300,
        )
        assert (
            ops_test.model.applications[APP_NAME].units[0].workload_status
            == "active"
        )
