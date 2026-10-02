#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: mcp verifying bearer tokens.

mcp's own oauth relation is independent of the web UI's: it carries the
MCP_AUTH_* environment the FastMCP JWTVerifier uses to validate access
tokens, in a namespace of its own so mcp never receives the web UI's OIDC
credentials. This covers the plain JWKS verification path only.
"""

import logging
from pathlib import Path

import jubilant
import pytest
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)


def _deploy_mcp_behind_ingress(
    juju: jubilant.Juju, charm: Path, charm_image: str
) -> None:
    """Deploy a migrated UI, then mcp behind Traefik, with no identity provider.

    Each test scenario configures mcp's identity provider itself, as its
    own When step, so mcp is expected to stay Blocked here. Only Traefik
    has to settle before scenarios run, so only Traefik is waited on.

    Args:
        juju: Jubilant object.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.
    """
    steps.deploy_dependencies(juju)

    ui_name = steps.deploy_superset_application(
        juju, charm, charm_image, "app-gunicorn"
    )
    steps.integrate_dependencies(juju, ui_name)
    steps.wait_for_active(juju, [ui_name], timeout=steps.DEPLOY_TIMEOUT)

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


@pytest.fixture(scope="module")
def mcp_behind_ingress(
    request: pytest.FixtureRequest,
    model: jubilant.Juju,
    charm: Path,
    charm_image: str,
) -> jubilant.Juju:
    """MCP deployed with no auth source, reachable through Traefik.

    Args:
        request: Pytest request object.
        model: The module's model.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.

    Returns:
        The model, with mcp blocked behind a plain HTTP ingress.
    """
    return steps.adopt_or_build(
        request, model, _deploy_mcp_behind_ingress, charm, charm_image
    )


def _relate_idp(juju: jubilant.Juju) -> None:
    """Deploy the stub identity provider and relate it to mcp.

    Args:
        juju: Jubilant object.
    """
    steps.deploy_oauth_integrator(juju)
    juju.integrate(
        f"{steps.MCP_NAME}:oauth", f"{steps.OAUTH_INTEGRATOR_NAME}:oauth"
    )


@pytest.fixture(scope="module")
def mcp_with_oauth(
    request: pytest.FixtureRequest, mcp_behind_ingress: jubilant.Juju
) -> jubilant.Juju:
    """MCP related to a stub identity provider, still over plain HTTP.

    Args:
        request: Pytest request object.
        mcp_behind_ingress: The blocked deployment.

    Returns:
        The model, with the oauth relation in place and no TLS anywhere.
    """
    return steps.adopt_or_build(request, mcp_behind_ingress, _relate_idp)


def _add_ingress_tls(juju: jubilant.Juju) -> None:
    """Put a TLS provider behind Traefik and settle the deployment.

    Args:
        juju: Jubilant object.
    """
    steps.deploy_tls(juju)
    juju.integrate(
        f"{steps.TRAEFIK_NAME}:certificates", f"{steps.TLS_NAME}:certificates"
    )
    steps.wait_for_active(
        juju,
        [steps.MCP_NAME, steps.TRAEFIK_NAME, steps.OAUTH_INTEGRATOR_NAME],
        timeout=steps.SETTLE_TIMEOUT,
    )


@pytest.fixture(scope="module")
def mcp_with_oauth_over_https(
    request: pytest.FixtureRequest, mcp_with_oauth: jubilant.Juju
) -> jubilant.Juju:
    """MCP related to a stub identity provider behind an HTTPS ingress.

    Args:
        request: Pytest request object.
        mcp_with_oauth: The deployment with the oauth relation in place.

    Returns:
        The model, with mcp active behind an HTTPS ingress.
    """
    return steps.adopt_or_build(request, mcp_with_oauth, _add_ingress_tls)


def test_mcp_oauth_without_https_blocks(mcp_with_oauth: jubilant.Juju):
    """Scenario: an identity provider is related before the ingress has TLS.

    Given mcp behind a plain HTTP ingress
    When an identity provider is related to mcp
    Then mcp blocks saying OAuth requires an HTTPS ingress URL
    """
    juju = mcp_with_oauth

    with given("mcp behind a plain HTTP ingress"):
        assert steps.proxied_url(juju, steps.MCP_NAME).startswith("http://")

    with when("an identity provider is related to mcp"):
        pass

    with then("mcp blocks saying OAuth requires an HTTPS ingress URL"):
        steps.assert_blocked_with(
            juju, [steps.MCP_NAME], "OAuth requires an HTTPS ingress URL"
        )


def test_mcp_auth_config_populates_once_https_is_added(
    mcp_with_oauth_over_https: jubilant.Juju,
):
    """Scenario: TLS is added and mcp configures its bearer-auth namespace.

    Given mcp behind an HTTPS ingress with an identity provider related
    Then mcp is active
    And its MCP_AUTH_* environment carries the provider's data
    """
    juju = mcp_with_oauth_over_https

    with given(
        "mcp behind an HTTPS ingress with an identity provider related"
    ):
        pass

    with then("mcp is active"):
        steps.assert_active(juju, [steps.MCP_NAME])

    with and_("its MCP_AUTH_* environment carries the provider's data"):
        environment = steps.workload_environment(juju, f"{steps.MCP_NAME}/0")
        assert environment["MCP_AUTH_ISSUER"] == (
            steps.OAUTH_STUB_CONFIG["issuer_url"]
        )
        assert environment["MCP_AUTH_INTROSPECTION_URL"] == (
            steps.OAUTH_STUB_CONFIG["introspection_endpoint"]
        )
        assert environment["MCP_AUTH_CLIENT_ID"] == (
            steps.OAUTH_STUB_CONFIG["client_id"]
        )
        assert environment["MCP_AUTH_CLIENT_SECRET"] == (
            steps.OAUTH_STUB_CONFIG["client_secret"]
        )
