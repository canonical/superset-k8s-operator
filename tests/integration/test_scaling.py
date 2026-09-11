#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: scaling a Superset deployment.

The UI and the worker are the two applications a deployment scales. The UI
serves requests from every unit, and every worker unit is a Celery daemon that
has to join the broker to be doing anything at all.
"""

import logging

import jubilant
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)


def test_the_ui_scales_out_and_every_unit_serves(
    superset_deployment: jubilant.Juju,
):
    """Scenario: the UI is scaled out to carry more traffic.

    Given a Superset deployment with one UI unit
    When the UI is scaled out to two units
    Then both units serve Superset
    And both report the same generated admin password
    """
    juju = superset_deployment

    with given("a Superset deployment with one UI unit"):
        assert len(juju.status().apps[steps.UI_NAME].units) == 1
        password = steps.get_admin_password(juju, steps.UI_NAME, unit=0)

    with when("the UI is scaled out to two units"):
        steps.scale(juju, steps.UI_NAME, 2)

    with then("both units serve Superset"):
        for unit in range(2):
            steps.assert_ui_serves(juju, steps.UI_NAME, unit)

    with and_("both report the same generated admin password"):
        for unit in range(2):
            assert (
                steps.get_admin_password(juju, steps.UI_NAME, unit) == password
            ), f"unit {unit} reports a different admin password"


def test_the_worker_scales_out_and_every_daemon_joins_the_broker(
    superset_deployment: jubilant.Juju,
):
    """Scenario: the worker is scaled out to run more Celery tasks.

    Given a Superset deployment with one worker answering on the broker
    When the worker is scaled out to two units
    Then two Celery daemons answer on the broker
    """
    juju = superset_deployment

    with given(
        "a Superset deployment with one worker answering on the broker"
    ):
        steps.wait_for_celery_workers(juju, 1)

    with when("the worker is scaled out to two units"):
        steps.scale(juju, steps.WORKER_NAME, 2)

    with then("two Celery daemons answer on the broker"):
        workers = steps.wait_for_celery_workers(juju, 2)
        logger.info("Celery workers on the broker: %s", list(workers))


def test_scaling_back_in_leaves_a_working_deployment(
    superset_deployment: jubilant.Juju,
):
    """Scenario: the deployment is scaled back down after the load passes.

    Given a Superset deployment scaled out to two UI and two worker units
    When both applications are scaled back in to one unit
    Then the surviving UI unit serves Superset
    And one Celery daemon answers on the broker
    """
    juju = superset_deployment

    with given(
        "a Superset deployment scaled out to two UI and two worker units"
    ):
        for app in steps.SCALABLE_APPS:
            steps.scale(juju, app, 2)

    with when("both applications are scaled back in to one unit"):
        for app in steps.SCALABLE_APPS:
            steps.scale(juju, app, 1)

    with then("the surviving UI unit serves Superset"):
        steps.assert_ui_serves(juju)

    with and_("one Celery daemon answers on the broker"):
        steps.wait_for_celery_workers(juju, 1)
