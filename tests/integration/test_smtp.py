#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: the mail relay alerts and reports are delivered through.

`ALERT_REPORTS` makes Superset mail the reports it renders, so without the
`smtp` relation they fail at send time. The charm refuses to run in that
configuration, and the relay a provider publishes has to reach the workload
environment for the send to work.
"""

import logging

import jubilant
import pytest
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)

REPORT_FLAGS = "ALERT_REPORTS"
EXTERNAL_URL = "https://superset.test"


def _enable_alert_reports(juju: jubilant.Juju) -> None:
    """Set the report feature flag on every application.

    Args:
        juju: Jubilant object.
    """
    steps.set_config(
        juju,
        steps.SUPERSET_APPS,
        {"feature-flags": REPORT_FLAGS, "external-url": EXTERNAL_URL},
        wait_for="blocked",
    )


@pytest.fixture(scope="module")
def alert_reports_without_a_relay(
    request: pytest.FixtureRequest, superset_deployment: jubilant.Juju
) -> jubilant.Juju:
    """Enable `ALERT_REPORTS` on every application, with no SMTP provider.

    Args:
        request: Pytest request object.
        superset_deployment: The active deployment.

    Returns:
        The model, with the flag set and no `smtp` relation.
    """
    logger.info("Enabling ALERT_REPORTS with no smtp relation in the model")
    return steps.adopt_or_build(
        request, superset_deployment, _enable_alert_reports
    )


def test_alert_reports_without_a_relay_blocks_every_application(
    alert_reports_without_a_relay: jubilant.Juju,
):
    """Scenario: reports are turned on with nothing to deliver them.

    Given a Superset deployment with no smtp relation
    When ALERT_REPORTS is enabled on all three applications
    Then every application blocks naming the relation it needs
    """
    juju = alert_reports_without_a_relay

    with given("a Superset deployment with no smtp relation"):
        assert steps.SMTP_INTEGRATOR_NAME not in juju.status().apps

    with when("ALERT_REPORTS is enabled on all three applications"):
        pass

    with then("every application blocks naming the relation it needs"):
        steps.assert_blocked_with(
            juju,
            steps.SUPERSET_APPS,
            "ALERT_REPORTS requires an smtp relation",
        )


def test_a_dry_run_needs_no_relay(
    alert_reports_without_a_relay: jubilant.Juju,
):
    """Scenario: reports are rendered but deliberately delivered nowhere.

    `report-dry-run` renders screenshots and sends nothing, so there is
    nothing for a relay to carry and the charm has no reason to block.

    Given a deployment blocked on ALERT_REPORTS without an smtp relation
    When report-dry-run is turned on
    Then every application becomes active without any relay
    """
    juju = alert_reports_without_a_relay

    with given(
        "a deployment blocked on ALERT_REPORTS without an smtp relation"
    ):
        steps.assert_blocked_with(
            juju,
            steps.SUPERSET_APPS,
            "ALERT_REPORTS requires an smtp relation",
        )

    with when("report-dry-run is turned on"):
        steps.set_config(juju, steps.SUPERSET_APPS, {"report-dry-run": "true"})

    with then("every application becomes active without any relay"):
        steps.wait_for_active(juju, steps.SUPERSET_APPS)
        assert steps.SMTP_INTEGRATOR_NAME not in juju.status().apps


def test_the_relation_carries_the_relay_into_the_workload(
    alert_reports_without_a_relay: jubilant.Juju,
):
    """Scenario: an SMTP provider is related and its relay reaches Superset.

    Given a Superset deployment with ALERT_REPORTS enabled
    When an SMTP provider is related to all three applications
    Then the relay it published is in the worker's workload environment
    And the password it published as a granted secret was resolved
    """
    juju = alert_reports_without_a_relay

    with given("a Superset deployment with ALERT_REPORTS enabled"):
        # The dry run is over; this scenario tests delivery.
        steps.set_config(
            juju,
            steps.SUPERSET_APPS,
            {"report-dry-run": "false"},
            wait_for="blocked",
        )

    with when("an SMTP provider is related to all three applications"):
        steps.deploy_smtp_integrator(juju)
        for app in steps.SUPERSET_APPS:
            juju.integrate(f"{app}:smtp", f"{steps.SMTP_INTEGRATOR_NAME}:smtp")
        steps.wait_for_active(
            juju,
            [*steps.SUPERSET_APPS, steps.SMTP_INTEGRATOR_NAME],
            timeout=steps.SETTLE_TIMEOUT,
        )

    with then(
        "the relay it published is in the worker's workload environment"
    ):
        environment = steps.workload_environment(
            juju, f"{steps.WORKER_NAME}/0"
        )
        assert environment["SMTP_HOST"] == steps.SMTP_CONFIG["host"]
        assert environment["SMTP_PORT"] == str(steps.SMTP_CONFIG["port"])
        assert environment["SMTP_USERNAME"] == steps.SMTP_CONFIG["user"]
        assert environment["SMTP_EMAIL"] == steps.SMTP_CONFIG["smtp_sender"]
        assert environment["SMTP_STARTTLS"] == "true"
        assert environment["SMTP_SSL"] == "false"
        assert environment["SMTP_SSL_SERVER_AUTH"] == "true"
        assert environment["SMTP_SUPERSET_EXTERNAL_URL"] == EXTERNAL_URL
        assert environment["SMTP_EMAIL_SUBJECT_PREFIX"] == "[Superset] "

    # The provider publishes the password as a granted secret ID, so this
    # value only appears if the charm resolved the secret.
    with and_("the password it published as a granted secret was resolved"):
        assert environment["SMTP_PASSWORD"] == steps.SMTP_CONFIG["password"]
