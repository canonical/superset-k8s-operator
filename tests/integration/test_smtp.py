# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for Superset's SMTP relation."""

import pytest
import pytest_asyncio
import yaml
from integration.helpers import (
    CHARM_FUNCTIONS,
    SMTP_CONFIG,
    SMTP_INTEGRATOR_NAME,
    UI_NAME,
    deploy_smtp_integrator,
)
from pytest_operator.plugin import OpsTest

WORKER_NAME = f"superset-k8s-{CHARM_FUNCTIONS['worker']}"
BEAT_NAME = f"superset-k8s-{CHARM_FUNCTIONS['beat']}"
SMTP_APPS = [UI_NAME, WORKER_NAME, BEAT_NAME]
EXTERNAL_URL = "https://superset.test"
REPORT_FEATURE_FLAGS = "ALERT_REPORTS,GLOBAL_ASYNC_QUERIES"


async def workload_environment(ops_test: OpsTest, unit: str) -> dict:
    """Return the environment of the Superset pebble service.

    Args:
        ops_test: Juju test model.
        unit: Name of the unit, e.g. `superset-k8s-worker/0`.

    Returns:
        The environment mapping of the `superset` service.
    """
    return_code, stdout, stderr = await ops_test.juju(
        "exec",
        "--unit",
        unit,
        "--",
        "PEBBLE_SOCKET=/charm/containers/superset/pebble.socket "
        "/charm/bin/pebble plan",
    )
    assert return_code == 0, stderr
    return yaml.safe_load(stdout)["services"]["superset"]["environment"]


@pytest_asyncio.fixture(name="deploy-smtp", scope="module")
async def deploy_smtp(ops_test: OpsTest, deploy) -> None:
    """Add an SMTP provider to the shared deployment.

    Args:
        ops_test: Juju test model.
        deploy: Shared deployment fixture from the integration conftest.
    """
    del deploy
    await deploy_smtp_integrator(ops_test)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-smtp")
class TestSmtp:
    """Verify the SMTP relation and the feature flag it is coupled to."""

    async def test_alert_reports_without_a_relation_blocks(
        self, ops_test: OpsTest
    ) -> None:
        """The feature flag alone renders reports that cannot be delivered."""
        for app_name in SMTP_APPS:
            await ops_test.model.applications[app_name].set_config(
                {
                    "feature-flags": REPORT_FEATURE_FLAGS,
                    "external-url": EXTERNAL_URL,
                }
            )

        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(
                apps=SMTP_APPS,
                status="blocked",
                timeout=1200,
            )

        for app_name in SMTP_APPS:
            unit = ops_test.model.applications[app_name].units[0]
            assert unit.workload_status_message == (
                "ALERT_REPORTS requires an smtp relation"
            )

    async def test_relation_maps_the_relay(self, ops_test: OpsTest) -> None:
        """The relay the provider publishes reaches the workload."""
        for app_name in SMTP_APPS:
            await ops_test.model.integrate(
                f"{app_name}:smtp", f"{SMTP_INTEGRATOR_NAME}:smtp"
            )

        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(
                apps=SMTP_APPS + [SMTP_INTEGRATOR_NAME],
                status="active",
                raise_on_blocked=False,
                timeout=1200,
            )

        environment = await workload_environment(ops_test, f"{WORKER_NAME}/0")
        assert environment["SMTP_HOST"] == SMTP_CONFIG["host"]
        assert environment["SMTP_PORT"] == str(SMTP_CONFIG["port"])
        assert environment["SMTP_USERNAME"] == SMTP_CONFIG["user"]
        # The provider publishes the password as a granted secret ID, so this
        # value only appears if the charm resolved the secret.
        assert environment["SMTP_PASSWORD"] == SMTP_CONFIG["password"]
        assert environment["SMTP_EMAIL"] == SMTP_CONFIG["smtp_sender"]
        assert environment["SMTP_STARTTLS"] == "true"
        assert environment["SMTP_SSL"] == "false"
        assert environment["SMTP_SSL_SERVER_AUTH"] == "true"
        assert environment["SMTP_SUPERSET_EXTERNAL_URL"] == EXTERNAL_URL
        assert environment["SMTP_EMAIL_SUBJECT_PREFIX"] == "[Superset] "

    async def test_relation_without_the_feature_flag_blocks(
        self, ops_test: OpsTest
    ) -> None:
        """The relation alone publishes a relay the workload never reads."""
        await ops_test.model.applications[WORKER_NAME].set_config(
            {"feature-flags": "GLOBAL_ASYNC_QUERIES"}
        )

        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(
                apps=[WORKER_NAME],
                status="blocked",
                timeout=1200,
            )

        unit = ops_test.model.applications[WORKER_NAME].units[0]
        assert unit.workload_status_message == (
            "the smtp relation requires the ALERT_REPORTS feature flag"
        )
