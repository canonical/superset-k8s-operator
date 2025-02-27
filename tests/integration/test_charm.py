#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration tests."""

import logging

import pytest
import requests
from integration.helpers import (
    UI_NAME,
    api_authentication,
    delete_chart,
    get_chart_count,
    get_unit_url,
    restart_application,
    simulate_crash,
)
from integration.conftest import deploy  # noqa: F401, pylint: disable=W0611
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

    async def test_charm_crash(self, ops_test: OpsTest):
        """Test backup and restore functionality.

        This should validate that the Superset charm itself is stateless
        and relies only on the postgreSQL database to store its chart values.
        """
        url = await get_unit_url(
            ops_test, application=UI_NAME, unit=0, port=8088
        )
        session = await api_authentication(ops_test, url)

        # Delete a chart
        original_charts = await get_chart_count(ops_test, url, session)
        await delete_chart(ops_test, url, session)

        await simulate_crash(ops_test)

        # Get chart count on re-deployment
        url = await get_unit_url(
            ops_test, application=UI_NAME, unit=0, port=8088
        )
        session = await api_authentication(ops_test, url)
        chart_count = await get_chart_count(ops_test, url, session)

        # Validate chart remains deleted
        logger.info("Validating state remains unchanged")
        assert chart_count == original_charts - 1

    async def test_restart_action(self, ops_test: OpsTest):
        """Restarts Superset application."""
        await restart_application(ops_test)
        assert (
            ops_test.model.applications[UI_NAME].units[0].workload_status
            == "maintenance"
        )
