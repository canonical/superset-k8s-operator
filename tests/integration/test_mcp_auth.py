#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: mcp verifying bearer tokens.

mcp's own oauth relation is independent of the web UI's: it carries the
MCP_AUTH_* environment the FastMCP JWTVerifier uses to validate access
tokens, in a namespace of its own so mcp never receives the web UI's OIDC
credentials. mcp-jwt-secret-id is the alternative for deployments with no
external identity provider at all: a shared HS256 secret, verified the same
way. This only wires the plain JWKS/shared-secret verification paths — the
Google-specific auth proxy is a separate stage.
"""

import logging
import time
from pathlib import Path

import jubilant
import jwt
import pytest
import requests
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)

MCP_PORT = 5008
JWT_SHARED_SECRET = "test-hs256-shared-secret-at-least-32-bytes-long"  # nosec B105
JWT_SECRET_NAME = "mcp-jwt-secret"  # nosec B105
JWT_MCP_NAME = f"{steps.MCP_NAME}-jwt"


def _sign_token(subject: str) -> str:
    """Sign an HS256 token against JWT_SHARED_SECRET.

    Args:
        subject: The `sub` claim — the Superset username to authenticate as.

    Returns:
        The encoded JWT.
    """
    now = int(time.time())
    return jwt.encode(
        {"sub": subject, "iat": now, "exp": now + 300},
        JWT_SHARED_SECRET,
        algorithm="HS256",
    )


def _call_tool(url: str, token: str | None) -> requests.Response:
    """Call the list_dashboards tool once over the streamable-HTTP transport.

    Args:
        url: The mcp endpoint URL.
        token: Bearer token to authenticate with, or None to send none.

    Returns:
        The raw HTTP response.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return requests.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "call_tool",
                "arguments": {
                    "name": "list_dashboards",
                    "arguments": {"request": {}},
                },
            },
        },
        headers=headers,
        timeout=30,
    )


def _deploy_mcp_behind_ingress(
    juju: jubilant.Juju, charm: Path, charm_image: str
) -> None:
    """Deploy a migrated UI, then mcp behind Traefik, with no auth source yet.

    mcp is left blocked here — relating the identity provider is each
    scenario's own When — but Traefik itself still has to settle, so only it
    is waited on.

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

    mcp_name = steps.deploy_superset_application(juju, charm, charm_image, "mcp")
    steps.integrate_dependencies(juju, mcp_name)

    juju.deploy(
        steps.TRAEFIK_NAME,
        channel=steps.TRAEFIK_CHANNEL,
        config=steps.TRAEFIK_CONFIG,
        trust=True,
    )
    juju.integrate(f"{mcp_name}:ingress", f"{steps.TRAEFIK_NAME}:ingress")
    steps.wait_for_active(juju, [steps.TRAEFIK_NAME], timeout=steps.SETTLE_TIMEOUT)


@pytest.fixture(scope="module")
def mcp_behind_ingress(
    request: pytest.FixtureRequest,
    model: jubilant.Juju,
    charm: Path,
    charm_image: str,
) -> jubilant.Juju:
    """mcp deployed with no auth source, reachable through Traefik.

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
    """mcp related to a stub identity provider, still over plain HTTP.

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
    """mcp related to a stub identity provider behind an HTTPS ingress.

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

    with given("mcp behind an HTTPS ingress with an identity provider related"):
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


def _add_mcp_with_jwt_secret(
    juju: jubilant.Juju, charm: Path, charm_image: str
) -> None:
    """Deploy a second mcp instance authenticating off a shared secret.

    Reuses the UI and dependencies mcp_behind_ingress already deployed in
    this module's shared model — deploying them again would fail, since they
    already exist — and gives this instance its own app name so it cannot
    collide with the oauth-relation mcp instance the other scenarios build.

    Args:
        juju: Jubilant object.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.
    """
    secret_uri = juju.add_secret(
        name=JWT_SECRET_NAME, content={"secret": JWT_SHARED_SECRET}
    )
    secret_id = str(secret_uri).rsplit(":", maxsplit=1)[-1]

    mcp_name = steps.deploy_superset_application(
        juju,
        charm,
        charm_image,
        "mcp",
        app_name=JWT_MCP_NAME,
        config={"mcp-jwt-secret-id": secret_id},
    )
    juju.grant_secret(JWT_SECRET_NAME, mcp_name)
    steps.integrate_dependencies(juju, mcp_name)
    steps.wait_for_active(juju, [mcp_name], timeout=steps.DEPLOY_TIMEOUT)


@pytest.fixture(scope="module")
def mcp_with_jwt_secret(
    request: pytest.FixtureRequest,
    mcp_behind_ingress: jubilant.Juju,
    charm: Path,
    charm_image: str,
) -> jubilant.Juju:
    """A second mcp instance, authenticating bearer tokens off a shared secret.

    Args:
        request: Pytest request object.
        mcp_behind_ingress: The deployment mcp's own dependencies are already
            in, with an unrelated first mcp instance sitting blocked.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.

    Returns:
        The model, with a second mcp application active and reachable
        directly (no ingress).
    """
    return steps.adopt_or_build(
        request, mcp_behind_ingress, _add_mcp_with_jwt_secret, charm, charm_image
    )


def test_a_valid_token_resolves_a_real_user(mcp_with_jwt_secret: jubilant.Juju):
    """Scenario: a token signed with the shared secret authenticates a call.

    Given mcp authenticating off a shared secret
    When a tool is called with a token naming an existing Superset user
    Then the call succeeds
    """
    juju = mcp_with_jwt_secret
    url = f"{steps.get_unit_url(juju, JWT_MCP_NAME, port=MCP_PORT)}/mcp"

    with given("mcp authenticating off a shared secret"):
        steps.assert_active(juju, [JWT_MCP_NAME])

    with when("a tool is called with a token naming an existing Superset user"):
        response = _call_tool(url, _sign_token("admin"))

    with then("the call succeeds"):
        assert response.status_code == 200, response.text
        assert '"isError":false' in response.text, response.text


@pytest.mark.parametrize(
    "token",
    [None, "garbage.not.a.jwt"],
    ids=["no token", "garbage token"],
)
def test_an_unverifiable_token_is_rejected(
    mcp_with_jwt_secret: jubilant.Juju, token: str | None
):
    """Scenario: a call with no token, or one the shared secret can't verify.

    Given mcp authenticating off a shared secret
    When a tool is called with no bearer token or one that fails verification
    Then the call is rejected before it reaches any tool
    """
    juju = mcp_with_jwt_secret
    url = f"{steps.get_unit_url(juju, JWT_MCP_NAME, port=MCP_PORT)}/mcp"

    with given("mcp authenticating off a shared secret"):
        steps.assert_active(juju, [JWT_MCP_NAME])

    with when("a tool is called with no bearer token or one that fails verification"):
        response = _call_tool(url, token)

    with then("the call is rejected before it reaches any tool"):
        assert response.status_code == 401, response.text
