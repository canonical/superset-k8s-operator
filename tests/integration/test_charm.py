#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration tests."""

import logging

import pytest
import requests
from integration.conftest import deploy  # noqa: F401, pylint: disable=W0611
from integration.helpers import (
    CA_CERT_PATH,
    CONFIG_FILES,
    CONFIG_PATH,
    POSTGRES_NAME,
    REDIS_NAME,
    TLS_NAME,
    UI_NAME,
    api_authentication,
    delete_chart,
    delete_unit_pod,
    get_chart_count,
    get_unit_url,
    read_workload_file,
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

    async def test_charm_crash(
        self, ops_test: OpsTest, charm: str, charm_image: str
    ):
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

        await simulate_crash(ops_test, charm, charm_image)

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

    async def test_certificates_relation(self, ops_test: OpsTest):
        """Relating a TLS provider installs its CA in the workload container."""
        await ops_test.model.deploy(TLS_NAME, channel="1/stable")
        await ops_test.model.wait_for_idle(
            apps=[TLS_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=1000,
        )

        await ops_test.model.integrate(
            f"{UI_NAME}:certificates", f"{TLS_NAME}:certificates"
        )
        await ops_test.model.wait_for_idle(
            apps=[UI_NAME, TLS_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=1000,
        )

        return_code, contents = await read_workload_file(
            ops_test, f"{UI_NAME}/0", CA_CERT_PATH
        )
        assert return_code == 0, f"{CA_CERT_PATH} was not created"
        assert "BEGIN CERTIFICATE" in contents

    async def test_certificates_relation_removal(self, ops_test: OpsTest):
        """Removing the TLS relation removes the CA from the container."""
        await ops_test.juju(
            "remove-relation",
            f"{UI_NAME}:certificates",
            f"{TLS_NAME}:certificates",
        )
        await ops_test.model.wait_for_idle(
            apps=[UI_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=1000,
        )

        return_code, _ = await read_workload_file(
            ops_test, f"{UI_NAME}/0", CA_CERT_PATH
        )
        assert return_code != 0, f"{CA_CERT_PATH} was not removed"

    async def test_pod_restart_is_stateless(self, ops_test: OpsTest):
        """A rescheduled pod recovers on its own with its state intact.

        The charm holds no state of its own, so everything the workload needs
        has to survive the container filesystem and the pebble plan being wiped:

        - Charts, which live in the metadata database.
        - The workload configuration files, which the charm pushes into the
          container filesystem on every reconcile.
        - The generated admin password, which lives in a peer secret and is
          what `api_authentication` logs in with.
        """
        url = await get_unit_url(
            ops_test, application=UI_NAME, unit=0, port=8088
        )
        session = await api_authentication(ops_test, url)
        charts_before = await get_chart_count(ops_test, url, session)

        await delete_unit_pod(ops_test, f"{UI_NAME}/0")

        await ops_test.model.wait_for_idle(
            apps=[UI_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=2000,
        )

        for file in CONFIG_FILES:
            path = f"{CONFIG_PATH}/{file}"
            return_code, contents = await read_workload_file(
                ops_test, f"{UI_NAME}/0", path
            )
            assert return_code == 0, f"{path} was not restored"
            assert contents.strip(), f"{path} is empty"

        url = await get_unit_url(
            ops_test, application=UI_NAME, unit=0, port=8088
        )
        session = await api_authentication(ops_test, url)
        assert await get_chart_count(ops_test, url, session) == charts_before

    async def test_redis_relation_removal(self, ops_test: OpsTest):
        """Removes Superset/Redis relation."""
        await ops_test.model.wait_for_idle(
            apps=[UI_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=2000,
        )

        await ops_test.model.applications[UI_NAME].remove_relation(
            f"{UI_NAME}:redis", f"{REDIS_NAME}:redis"
        )

        await ops_test.model.wait_for_idle(
            apps=[UI_NAME],
            status="blocked",
            raise_on_blocked=False,
            timeout=2000,
        )

    async def test_postgresql_relation_removal(self, ops_test: OpsTest):
        """Removes Superset/PostgreSQL relation."""
        await ops_test.model.applications[UI_NAME].remove_relation(
            f"{UI_NAME}:postgresql_db", f"{POSTGRES_NAME}:database"
        )

        await ops_test.model.wait_for_idle(
            apps=[UI_NAME],
            status="blocked",
            raise_on_blocked=False,
            timeout=2000,
        )
