#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: authenticating Superset users against an identity provider.

The charm registers itself as an OAuth client over the `oauth` relation. The
callback it registers has to be the address a browser is sent back to, so it
is derived from the ingress URL and the relation is refused without HTTPS.
"""

import json
import logging
from urllib.parse import parse_qs, urlparse

import jubilant
import pytest
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)


def _relate_idp(juju: jubilant.Juju) -> None:
    """Deploy the stub identity provider and relate it to the UI.

    Args:
        juju: Jubilant object.
    """
    steps.deploy_oauth_integrator(juju)
    juju.integrate(
        f"{steps.UI_NAME}:oauth", f"{steps.OAUTH_INTEGRATOR_NAME}:oauth"
    )


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
        [steps.UI_NAME, steps.TRAEFIK_NAME, steps.OAUTH_INTEGRATOR_NAME],
        timeout=steps.SETTLE_TIMEOUT,
    )


@pytest.fixture(scope="module")
def an_oauth_provider_over_plain_http(
    request: pytest.FixtureRequest,
    superset_deployment_with_ingress: jubilant.Juju,
) -> jubilant.Juju:
    """Relate an OAuth provider to a UI whose ingress is still plain HTTP.

    Args:
        request: Pytest request object.
        superset_deployment_with_ingress: The deployment behind Traefik.

    Returns:
        The model, with the oauth relation in place and no TLS anywhere.
    """
    logger.info("Relating a stub identity provider with no TLS on the ingress")
    return steps.adopt_or_build(
        request, superset_deployment_with_ingress, _relate_idp
    )


@pytest.fixture(scope="module")
def an_oauth_provider_over_https(
    request: pytest.FixtureRequest,
    an_oauth_provider_over_plain_http: jubilant.Juju,
) -> jubilant.Juju:
    """Put TLS on the ingress so the OAuth relation can be satisfied.

    Args:
        request: Pytest request object.
        an_oauth_provider_over_plain_http: The blocked deployment.

    Returns:
        The model, with the UI active behind an HTTPS ingress.
    """
    logger.info("Putting TLS on the ingress")
    return steps.adopt_or_build(
        request, an_oauth_provider_over_plain_http, _add_ingress_tls
    )


def test_an_oauth_relation_without_https_blocks_the_ui(
    an_oauth_provider_over_plain_http: jubilant.Juju,
):
    """Scenario: an identity provider is related before the ingress has TLS.

    A callback served over plain HTTP is one an identity provider will not
    redirect a browser back to, so the charm refuses to register it.

    Given a Superset deployment behind a plain HTTP ingress
    When an identity provider is related to the UI
    Then the UI blocks saying OAuth requires an HTTPS ingress URL
    """
    juju = an_oauth_provider_over_plain_http

    with given("a Superset deployment behind a plain HTTP ingress"):
        assert steps.proxied_url(juju).startswith("http://")

    with when("an identity provider is related to the UI"):
        pass

    with then("the UI blocks saying OAuth requires an HTTPS ingress URL"):
        steps.assert_blocked_with(
            juju, [steps.UI_NAME], "OAuth requires an HTTPS ingress URL"
        )


def test_the_charm_registers_an_https_callback(
    an_oauth_provider_over_https: jubilant.Juju,
):
    """Scenario: TLS is added and the charm registers itself as a client.

    Given a Superset deployment behind an HTTPS ingress with an identity
      provider related
    Then the UI is active
    And it published an HTTPS callback under its own ingress hostname
    And it asked for the scope and grant types Superset needs
    """
    juju = an_oauth_provider_over_https

    with given(
        "a Superset deployment behind an HTTPS ingress with an identity "
        "provider related"
    ):
        pass

    with then("the UI is active"):
        steps.assert_active(juju, [steps.UI_NAME])

    # `juju show-unit` reports the REMOTE application's databag, so what the
    # charm published is read from the provider's unit.
    with and_("it published an HTTPS callback under its own ingress hostname"):
        data = steps.published_relation_data(
            juju,
            f"{steps.OAUTH_INTEGRATOR_NAME}/0",
            "oauth",
            "redirect_uri",
        )
        model = steps.model_short_name(juju.model or "")
        expected_host = f"{model}-{steps.UI_NAME}.{steps.TRAEFIK_DOMAIN}"
        assert data["redirect_uri"] == (
            f"https://{expected_host}/oauth-authorized/oidc"
        ), data["redirect_uri"]

    with and_("it asked for the scope and grant types Superset needs"):
        assert data["scope"] == "openid email profile"
        assert json.loads(data["grant_types"]) == ["authorization_code"]


def test_the_login_route_redirects_to_the_identity_provider(
    an_oauth_provider_over_https: jubilant.Juju,
):
    """Scenario: a user starts a login against the configured provider.

    Given a Superset deployment registered with an identity provider
    When the OIDC login route is requested
    Then it redirects to the provider's authorization endpoint
    And it carries the client identifier and scope the provider published
    """
    juju = an_oauth_provider_over_https

    with given("a Superset deployment registered with an identity provider"):
        steps.assert_active(juju, [steps.UI_NAME])

    with when("the OIDC login route is requested"):
        url = steps.get_unit_url(juju, steps.UI_NAME)
        response = steps.request_until(
            None,
            "GET",
            f"{url}/login/oidc",
            expected_status=302,
            allow_redirects=False,
        )

    with then("it redirects to the provider's authorization endpoint"):
        assert response.status_code in (302, 303), response.status_code
        redirect = urlparse(response.headers["Location"])
        assert redirect.hostname == "accounts.google.com", redirect.hostname

    with and_(
        "it carries the client identifier and scope the provider published"
    ):
        query = parse_qs(redirect.query)
        assert query["client_id"] == [steps.OAUTH_STUB_CONFIG["client_id"]]
        assert query["scope"] == [steps.OAUTH_STUB_CONFIG["scope"]]
