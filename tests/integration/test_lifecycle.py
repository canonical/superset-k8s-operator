#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: a Superset deployment survives its own infrastructure.

The charm holds no state of its own. What the workload needs is either
re-derived by the charm on every reconcile or kept in the metadata database,
so nothing a scenario here does to the deployment should lose any of it.
"""

import logging
import uuid
from pathlib import Path

import jubilant
import pytest
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def a_saved_chart(superset_deployment: jubilant.Juju) -> str:
    """Create a chart on the metadata database and return its name.

    The chart is the state a scenario checks is still there afterwards. It is
    backed by the metadata database rather than by Superset's bundled
    examples, which live in a SQLite file on the UI unit alone.

    Args:
        superset_deployment: The active deployment.

    Returns:
        The name of the chart created.
    """
    juju = superset_deployment
    session, url = steps.api_session(juju)
    environment = steps.workload_environment(juju, f"{steps.UI_NAME}/0")

    name = f"saved-chart-{uuid.uuid4().hex[:8]}"
    steps.create_metadata_backed_chart(session, url, environment, name)
    logger.info("Created chart %s", name)
    return name


def _relate_tls(juju: jubilant.Juju) -> None:
    """Deploy a TLS provider and relate it to the UI.

    Args:
        juju: Jubilant object.
    """
    steps.deploy_tls(juju)
    juju.integrate(
        f"{steps.UI_NAME}:certificates", f"{steps.TLS_NAME}:certificates"
    )
    steps.wait_for_active(juju, [steps.UI_NAME, steps.TLS_NAME])


@pytest.fixture(scope="module")
def superset_deployment_with_certificates(
    request: pytest.FixtureRequest, superset_deployment: jubilant.Juju
) -> jubilant.Juju:
    """A Superset deployment whose UI is related to a TLS provider.

    Args:
        request: Pytest request object.
        superset_deployment: The active deployment.

    Returns:
        The model, with the certificates relation in place.
    """
    logger.info("Relating a TLS provider to the UI")
    return steps.adopt_or_build(request, superset_deployment, _relate_tls)


def test_the_restart_action_restarts_the_server(
    superset_deployment: jubilant.Juju,
):
    """Scenario: an operator restarts the application server by hand.

    Given a Superset deployment whose UI is active
    When the restart action is run on the UI
    Then the UI comes back and serves again
    """
    juju = superset_deployment

    with given("a Superset deployment whose UI is active"):
        steps.assert_active(juju, [steps.UI_NAME])

    with when("the restart action is run on the UI"):
        steps.restart_application(juju)

    with then("the UI comes back and serves again"):
        steps.wait_for_active(juju, [steps.UI_NAME])
        steps.assert_ui_serves(juju)


def test_a_certificate_authority_is_installed_in_the_workload(
    superset_deployment_with_certificates: jubilant.Juju,
):
    """Scenario: a TLS provider's CA reaches the workload container.

    Given a Superset deployment
    When a TLS provider is related to the UI
    Then its CA is written to the path the charm documents
    """
    juju = superset_deployment_with_certificates

    with given("a Superset deployment"):
        pass

    with when("a TLS provider is related to the UI"):
        pass

    with then("its CA is written to the path the charm documents"):
        return_code, contents = steps.read_workload_file(
            juju, f"{steps.UI_NAME}/0", steps.CA_CERT_PATH
        )
        assert return_code == 0, f"{steps.CA_CERT_PATH} was not created"
        assert "BEGIN CERTIFICATE" in contents


def test_removing_the_certificates_relation_removes_the_authority(
    superset_deployment_with_certificates: jubilant.Juju,
):
    """Scenario: withdrawing a TLS provider withdraws its trust.

    Given a Superset deployment whose UI trusts a TLS provider's CA
    When the certificates relation is removed
    Then the CA is gone from the workload container
    """
    juju = superset_deployment_with_certificates

    with given("a Superset deployment whose UI trusts a TLS provider's CA"):
        return_code, _ = steps.read_workload_file(
            juju, f"{steps.UI_NAME}/0", steps.CA_CERT_PATH
        )
        assert return_code == 0, f"{steps.CA_CERT_PATH} is not installed"

    with when("the certificates relation is removed"):
        steps.remove_relation(
            juju,
            steps.UI_NAME,
            "certificates",
            steps.TLS_NAME,
            "certificates",
        )
        steps.wait_for_active(juju, [steps.UI_NAME])

    with then("the CA is gone from the workload container"):
        return_code, _ = steps.read_workload_file(
            juju, f"{steps.UI_NAME}/0", steps.CA_CERT_PATH
        )
        assert return_code != 0, f"{steps.CA_CERT_PATH} was not removed"


def test_a_rescheduled_pod_recovers_on_its_own(
    superset_deployment: jubilant.Juju, a_saved_chart: str
):
    """Scenario: Kubernetes reschedules a unit and wipes its container.

    Given a Superset deployment holding a saved chart
    When the UI unit's pod is deleted
    Then the charm restores every workload configuration file
    And the saved chart is still there
    """
    juju = superset_deployment

    with given("a Superset deployment holding a saved chart"):
        session, url = steps.api_session(juju)
        assert a_saved_chart in steps.chart_names(session, url)

    with when("the UI unit's pod is deleted"):
        steps.delete_unit_pod(juju, f"{steps.UI_NAME}/0")
        steps.wait_for_active(juju, [steps.UI_NAME])

    with then("the charm restores every workload configuration file"):
        for name in steps.CONFIG_FILES:
            path = f"{steps.CONFIG_PATH}/{name}"
            return_code, contents = steps.read_workload_file(
                juju, f"{steps.UI_NAME}/0", path
            )
            assert return_code == 0, f"{path} was not restored"
            assert contents.strip(), f"{path} is empty"

    with and_("the saved chart is still there"):
        session, url = steps.api_session(juju)
        assert a_saved_chart in steps.chart_names(session, url)


@pytest.mark.parametrize(
    ("endpoint", "provider", "provider_endpoint", "missing"),
    [
        ("redis", steps.REDIS_NAME, "redis", "Redis"),
        ("postgresql_db", steps.POSTGRES_NAME, "database", "PostgreSQL"),
    ],
    ids=["redis", "postgresql"],
)
def test_a_required_relation_blocks_when_removed_and_recovers(
    superset_deployment: jubilant.Juju,
    endpoint: str,
    provider: str,
    provider_endpoint: str,
    missing: str,
):
    """Scenario: a required relation is removed and then put back.

    Given a Superset deployment whose UI is active
    When one of its required relations is removed
    Then the UI blocks naming the relation it lost
    And re-adding the relation returns it to active
    """
    juju = superset_deployment

    with given("a Superset deployment whose UI is active"):
        steps.wait_for_active(juju, [steps.UI_NAME])

    with when("one of its required relations is removed"):
        steps.remove_relation(
            juju, steps.UI_NAME, endpoint, provider, provider_endpoint
        )

    with then("the UI blocks naming the relation it lost"):
        steps.assert_blocked_with(
            juju,
            [steps.UI_NAME],
            f"Required relations missing: {missing}",
        )

    with and_("re-adding the relation returns it to active"):
        juju.integrate(
            f"{steps.UI_NAME}:{endpoint}", f"{provider}:{provider_endpoint}"
        )
        steps.wait_for_active(
            juju, [steps.UI_NAME], timeout=steps.DEPLOY_TIMEOUT
        )
        steps.assert_ui_serves(juju)


def test_the_deployment_survives_losing_its_application(
    superset_deployment: jubilant.Juju,
    a_saved_chart: str,
    charm: Path,
    charm_image: str,
):
    """Scenario: the UI application is destroyed and deployed again.

    The charm keeps nothing of its own, so a deployment that loses an
    application entirely gets everything back from the metadata database and
    the signing keys secret it is pointed at again.

    Given a Superset deployment holding a saved chart
    When the UI application is destroyed and deployed again
    Then the saved chart is still there
    """
    juju = superset_deployment

    with given("a Superset deployment holding a saved chart"):
        session, url = steps.api_session(juju)
        assert a_saved_chart in steps.chart_names(session, url)

    with when("the UI application is destroyed and deployed again"):
        juju.remove_application(
            steps.UI_NAME, force=True, destroy_storage=True
        )
        juju.wait(
            lambda status: steps.UI_NAME not in status.apps,
            timeout=steps.SETTLE_TIMEOUT,
        )

        steps.deploy_superset_application(
            juju, charm, charm_image, "app-gunicorn"
        )
        steps.integrate_dependencies(juju, steps.UI_NAME)
        steps.wait_for_active(
            juju, [steps.UI_NAME], timeout=steps.DEPLOY_TIMEOUT
        )

    with then("the saved chart is still there"):
        session, url = steps.api_session(juju)
        assert a_saved_chart in steps.chart_names(session, url)
