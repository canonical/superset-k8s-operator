# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Temporal charm upgrades integration tests."""

import logging

import pytest
import requests
from helpers import (
    METADATA,
    SUPERSET_SECRET_KEY,
    UI_NAME,
    get_access_token,
    get_chart_count,
    get_unit_url,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestUpgrade:
    """Integration test for Superset charm upgrade from previous release."""

    async def test_upgrade(self, ops_test: OpsTest):
        """Builds the current charm and refreshes the current deployment."""
        charm = await ops_test.build_charm(".")
        resources = {
            "superset-image": METADATA["containers"]["superset-image"][
                "upstream-source"
            ]
        }
        superset_config = {
            "superset-secret-key": SUPERSET_SECRET_KEY,
            "load-examples": False,
        }

        await ops_test.model.wait_for_idle(
            apps=[UI_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=600,
        )

        await ops_test.model.applications[UI_NAME].refresh(
            path=str(charm), resources=resources, config=superset_config
        )
        await ops_test.model.wait_for_idle(
            apps=[UI_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=600,
        )

        assert (
            ops_test.model.applications[UI_NAME].units[0].workload_status
            == "active"
        )

    async def test_ui_relation(self, ops_test: OpsTest):
        """Perform GET request on the Superset UI host."""
        url = await get_unit_url(
            ops_test, application=UI_NAME, unit=0, port=8088
        )
        logger.info("curling app address: %s", url)

        response = requests.get(url, timeout=300)
        assert response.status_code == 200

        headers = await get_access_token(ops_test, url)
        charts = await get_chart_count(ops_test, url, headers)
        assert charts
