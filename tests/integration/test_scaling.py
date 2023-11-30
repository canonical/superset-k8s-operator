# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Superset charm scaling integration tests."""

import logging

import pytest
import pytest_asyncio
from helpers import (
    get_access_token,
    get_unit_url,
    scale,
)
from pytest_operator.plugin import OpsTest

SCALABLE_SERVICES = ["superset-k8s-ui", "superset-k8s-worker"]

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestScaling:
    """Integration tests for Superset charm."""

    async def test_scaling_up(self, ops_test: OpsTest):
        """Scale Superset charms up to 2 units."""
        for service in SCALABLE_SERVICES:
            await scale(ops_test, app=service, units=2)
            assert len(ops_test.model.applications[service].units) == 2

        
    async def test_scaling_down(self, ops_test: OpsTest):
        """Scale Superset charm down to 1 unit."""
        for service in SCALABLE_SERVICES:
            await scale(ops_test, app=service, units=1)
            assert len(ops_test.model.applications[service].units) == 1
