#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: scaling a Superset deployment.

The UI, the worker, and mcp are the applications a deployment scales. The
UI serves requests from every unit, and every worker unit is a Celery
daemon that has to join the broker to be doing anything at all. mcp's
Google OAuth proxy keeps registered clients in Redis instead of a
pod-local file, so scaling it doesn't fragment that state across units.
"""

import logging
from pathlib import Path

import jubilant
import pytest
import requests
import steps
from bdd import and_, given, then, when

from literals import MCP_PORT

logger = logging.getLogger(__name__)

REGISTER_REDIRECT_URI = "https://example.com/callback"


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


def _register_client(url: str) -> str:
    """Register a DCR client against one mcp unit's own OAuth proxy.

    Args:
        url: The mcp unit's own URL (its pod address, not the ingress).

    Returns:
        The registered client_id.
    """
    response = requests.post(
        f"{url}/register",
        json={"redirect_uris": [REGISTER_REDIRECT_URI]},
        timeout=10,
    )
    assert response.status_code == 201, response.text
    return response.json()["client_id"]


def _authorize_recognizes(url: str, client_id: str) -> bool:
    """Return whether a unit's OAuth proxy recognizes a client_id.

    `/authorize` redirects (302) once the proxy's `get_client()` finds the
    client, and answers a 400 "not found" error when it doesn't.
    Recognizing a client registered on a different unit therefore
    requires the two units to share the same Redis-backed, decryptable
    `client_storage`.

    Args:
        url: The mcp unit's own URL (its pod address, not the ingress).
        client_id: The client_id to check.

    Returns:
        True if the proxy redirects (the client was found), otherwise False.
    """
    response = requests.get(
        f"{url}/authorize",
        params={
            "client_id": client_id,
            "response_type": "code",
            "code_challenge": "test-challenge",
            "redirect_uri": REGISTER_REDIRECT_URI,
        },
        timeout=10,
        allow_redirects=False,
    )
    return response.status_code == 302


def _deploy_mcp_with_google_oauth(
    juju: jubilant.Juju, charm: Path, charm_image: str
) -> None:
    """Add mcp to a deployment, behind Traefik, fronting a Google-shaped relation.

    mcp can't go active until both the oauth relation and HTTPS exist,
    so only Traefik itself is waited on here, not mcp.
    `steps.deploy_traefik()` waits on both, which would stall since mcp
    isn't active yet.

    Args:
        juju: Jubilant object.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.
    """
    mcp_name = steps.deploy_superset_application(
        juju, charm, charm_image, "mcp"
    )
    steps.integrate_dependencies(juju, mcp_name)

    juju.deploy(
        steps.TRAEFIK_NAME,
        channel=steps.TRAEFIK_CHANNEL,
        config=steps.TRAEFIK_CONFIG,
        trust=True,
    )
    juju.integrate(f"{mcp_name}:ingress", f"{steps.TRAEFIK_NAME}:ingress")
    steps.wait_for_active(
        juju, [steps.TRAEFIK_NAME], timeout=steps.SETTLE_TIMEOUT
    )

    steps.deploy_oauth_integrator(juju)
    juju.integrate(f"{mcp_name}:oauth", f"{steps.OAUTH_INTEGRATOR_NAME}:oauth")
    steps.add_ingress_tls(juju, mcp_name, steps.OAUTH_INTEGRATOR_NAME)


@pytest.fixture(scope="module")
def mcp_with_google_oauth(
    request: pytest.FixtureRequest,
    superset_deployment: jubilant.Juju,
    charm: Path,
    charm_image: str,
) -> jubilant.Juju:
    """Mcp deployed behind an HTTPS ingress, fronting a Google-shaped relation.

    Args:
        request: Pytest request object.
        superset_deployment: The active deployment mcp is layered onto.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.

    Returns:
        The model, with mcp active and fronting its own Google OAuth proxy.
    """
    return steps.adopt_or_build(
        request,
        superset_deployment,
        _deploy_mcp_with_google_oauth,
        charm,
        charm_image,
    )


def test_the_mcp_oauth_proxy_scales_out_and_shares_registrations(
    mcp_with_google_oauth: jubilant.Juju,
):
    """Scenario: mcp's OAuth proxy is scaled out while serving registrations.

    Given a single mcp unit fronting a Google-shaped oauth relation
    When mcp is scaled out to two units
    Then a client registered against unit 0 is recognized by unit 1
    """
    juju = mcp_with_google_oauth

    with given("a single mcp unit fronting a Google-shaped oauth relation"):
        assert len(juju.status().apps[steps.MCP_NAME].units) == 1

    with when("mcp is scaled out to two units"):
        steps.scale(juju, steps.MCP_NAME, 2)

    with then("a client registered against unit 0 is recognized by unit 1"):
        client_id = _register_client(
            steps.get_unit_url(juju, steps.MCP_NAME, unit=0, port=MCP_PORT)
        )
        recognized = _authorize_recognizes(
            steps.get_unit_url(juju, steps.MCP_NAME, unit=1, port=MCP_PORT),
            client_id,
        )
        assert recognized, (
            f"unit 1 did not recognize client {client_id} registered "
            "on unit 0"
        )


def test_the_mcp_oauth_proxy_scaling_back_in_does_not_lose_a_registration(
    mcp_with_google_oauth: jubilant.Juju,
):
    """Scenario: the unit that registered a client is the one scaled away.

    Scaling removes the highest-numbered unit, so registering against
    unit 1 is what makes this test meaningful. A client registered on
    unit 0 would survive regardless, which wouldn't tell apart a real
    Redis-backed store from a pod-local one that got lucky.

    Given mcp scaled out to two units, with a client registered against the
        one about to be removed
    When mcp is scaled back in to one unit
    Then the surviving unit still recognizes the registered client
    """
    juju = mcp_with_google_oauth

    with given(
        "mcp scaled out to two units, with a client registered against "
        "the one about to be removed"
    ):
        steps.scale(juju, steps.MCP_NAME, 2)
        client_id = _register_client(
            steps.get_unit_url(juju, steps.MCP_NAME, unit=1, port=MCP_PORT)
        )

    with when("mcp is scaled back in to one unit"):
        steps.scale(juju, steps.MCP_NAME, 1)

    with then("the registered client is still recognized"):
        recognized = _authorize_recognizes(
            steps.get_unit_url(juju, steps.MCP_NAME, unit=0, port=MCP_PORT),
            client_id,
        )
        assert recognized, f"client {client_id} was lost scaling back in"
