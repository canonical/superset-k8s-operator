#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration tests."""

import logging

import pytest
import requests
from conftest import deploy  # noqa: F401, pylint: disable=W0611
from helpers import (
    UI_NAME,
    get_access_token,
    get_chart_count,
    get_unit_url,
    restart_application,
    simulate_crash,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestDeployment:
    """Integration tests for charm."""

    async def test_ui(self, ops_test: OpsTest):
        """Perform GET request on the Superset UI host."""
        url = await get_unit_url(
            ops_test, application=UI_NAME, unit=0, port=8088
        )
        logger.info("curling app address: %s", url)

        response = requests.get(url, timeout=300, verify=False)  # nosec
        assert response.status_code == 200

    async def test_restart_action(self, ops_test: OpsTest):
        """Removes an existing connector confirms database removed."""
        await restart_application(ops_test)
        assert (
            ops_test.model.applications[UI_NAME].units[0].workload_status
            == "maintenance"
        )

    async def test_charm_crash(self, ops_test: OpsTest):
        """Test backup and restore functionality.

        This should validate that the Superset charm itself is stateless
        and relies only on the postgreSQL database to store its chart values.
        """
        url = await get_unit_url(
            ops_test, application=UI_NAME, unit=0, port=8088
        )
        # Get number of charts
        headers = await get_access_token(ops_test, url)
        chart_count = await get_chart_count(ops_test, url, headers)
        assert chart_count == 102

        # Delete chart
        requests.delete(url + "/api/v1/chart/131", headers=headers)
        chart_count = await get_chart_count(ops_test, url, headers)
        assert chart_count == 101

        await simulate_crash(ops_test)

        chart_count = get_chart_count(ops_test, url, headers)
        assert chart_count == 101
