#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: the order a deployment's applications are related in.

Only the UI migrates the metadata database. Each application holds its own
PostgreSQL relation, so its own database user, and a PostgreSQL table belongs
to whoever created it: a worker that reached an empty database first used to
have Flask-AppBuilder create the `ab_*` tables under the worker's user, and
the UI's migration then failed on `must be owner of table ab_view_menu`. The
readiness gate is what makes the order irrelevant.
"""

import logging
from pathlib import Path

import jubilant
import pytest
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)

CELERY_APPS = (steps.WORKER_NAME, steps.BEAT_NAME)
# Applications that migrate nothing themselves and so wait on the UI's
# migration through `_metadata_database_status()` — the worker and the beat
# scheduler because they run Celery straight away, and mcp because it serves
# tool calls against the same metadata database.
NON_UI_APPS = CELERY_APPS + (steps.MCP_NAME,)


def _deploy_non_ui_applications(
    juju: jubilant.Juju, charm: Path, charm_image: str
) -> None:
    """Deploy the worker, the beat scheduler and mcp, and relate them.

    Args:
        juju: Jubilant object.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.
    """
    steps.deploy_dependencies(juju)
    for function in ("worker", "beat"):
        name = steps.deploy_superset_application(
            juju, charm, charm_image, function
        )
        steps.integrate_dependencies(juju, name)

    mcp_name = steps.deploy_superset_application(
        juju, charm, charm_image, "mcp", config={"mcp-dev-username": "admin"}
    )
    steps.integrate_dependencies(juju, mcp_name)


@pytest.fixture(scope="module")
def non_ui_applications_ahead_of_the_ui(
    request: pytest.FixtureRequest,
    model: jubilant.Juju,
    charm: Path,
    charm_image: str,
) -> jubilant.Juju:
    """Deploy the worker, the beat scheduler and mcp with no UI in the model.

    Args:
        request: Pytest request object.
        model: The module's model.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.

    Returns:
        The model, holding a worker, a beat scheduler and mcp on an
        unmigrated metadata database.
    """
    logger.info("Deploying the worker, beat scheduler and mcp before any UI")
    return steps.adopt_or_build(
        request, model, _deploy_non_ui_applications, charm, charm_image
    )


def test_a_worker_waits_for_the_ui_to_migrate_the_database(
    non_ui_applications_ahead_of_the_ui: jubilant.Juju,
    charm: Path,
    charm_image: str,
):
    """Scenario: applications that do not migrate are related before the UI.

    Given a worker, a beat scheduler and mcp on a metadata database no UI
      has migrated
    When the UI is deployed and related to the same database
    Then the UI migrates the database and becomes active
    And the worker, the beat scheduler and mcp stop waiting and become active
    And the worker's Celery daemon answers on the broker
    """
    juju = non_ui_applications_ahead_of_the_ui

    with given(
        "a worker, a beat scheduler and mcp on a metadata database no UI "
        "has migrated"
    ):
        steps.assert_waiting_with(
            juju,
            NON_UI_APPS,
            "waiting for the UI to initialise the database",
        )

    with when("the UI is deployed and related to the same database"):
        steps.deploy_superset_application(
            juju, charm, charm_image, "app-gunicorn"
        )
        steps.integrate_dependencies(juju, steps.UI_NAME)

    with then("the UI migrates the database and becomes active"):
        steps.wait_for_active(
            juju, [steps.UI_NAME], timeout=steps.DEPLOY_TIMEOUT
        )
        steps.assert_ui_serves(juju)

    # Juju has no signal from one application to another here, so the gate is
    # released by `update-status`, whose interval the test model shortens.
    with and_(
        "the worker, the beat scheduler and mcp stop waiting and become active"
    ):
        steps.wait_for_active(juju, NON_UI_APPS, timeout=steps.DEPLOY_TIMEOUT)

    with and_("the worker's Celery daemon answers on the broker"):
        steps.wait_for_celery_workers(juju, 1)
