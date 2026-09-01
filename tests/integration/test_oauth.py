# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for Superset's OAuth relation."""

import json
import logging
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
import requests
import yaml
from integration.helpers import (
    TLS_NAME,
    TRAEFIK_CONFIG,
    TRAEFIK_NAME,
    UI_NAME,
    get_unit_url,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

TRAEFIK_DOMAIN = TRAEFIK_CONFIG["external_hostname"]
OAUTH_INTEGRATOR_NAME = "oauth-external-idp-integrator"
OAUTH_STUB_CONFIG = {
    "issuer_url": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",  # nosec B105
    "introspection_endpoint": "https://oauth2.googleapis.com/tokeninfo",
    "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
    "jwks_endpoint": "https://www.googleapis.com/oauth2/v3/certs",
    "scope": "openid email profile",
    "client_id": "stub-google-client-id",
    "client_secret": "stub-google-client-secret",  # nosec B105
}


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy-oauth", scope="module")
async def deploy_oauth(ops_test: OpsTest, deploy) -> None:
    """Add a stub Google OAuth provider to the shared deployment.

    Args:
        ops_test: Pytest-operator test context.
        deploy: Shared deployment fixture from the integration conftest.
    """
    del deploy
    if ops_test.request.config.getoption("--no-deploy") and ops_test.request.config.getoption(
        "--model"
    ):
        logger.info("Skipping OAuth stub deploy; reusing existing model %s", ops_test.model_name)
        return
    await ops_test.model.deploy(TLS_NAME, channel="1/stable")
    await ops_test.model.wait_for_idle(
        apps=[TLS_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
    )
    await ops_test.model.integrate(
        f"{TRAEFIK_NAME}:certificates", f"{TLS_NAME}:certificates"
    )
    await ops_test.model.wait_for_idle(
        apps=[TRAEFIK_NAME, UI_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
    )
    await ops_test.model.deploy(
        OAUTH_INTEGRATOR_NAME,
        channel="latest/edge",
        config=OAUTH_STUB_CONFIG,
    )
    await ops_test.model.wait_for_idle(
        apps=[UI_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=2000,
    )
    await ops_test.model.wait_for_idle(
        apps=[OAUTH_INTEGRATOR_NAME],
        status="blocked",
        raise_on_blocked=False,
        timeout=1200,
    )

    await ops_test.model.integrate(
        f"{UI_NAME}:oauth",
        f"{OAUTH_INTEGRATOR_NAME}:oauth",
    )
    await ops_test.model.wait_for_idle(
        apps=[UI_NAME, OAUTH_INTEGRATOR_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
    )


async def _oauth_client_relation_data(ops_test: OpsTest) -> dict[str, str]:
    """Return the OAuth client application data visible to the provider."""
    return_code, stdout, stderr = await ops_test.juju(
        "show-unit", f"{OAUTH_INTEGRATOR_NAME}/0"
    )
    assert return_code == 0, stderr

    unit_data = yaml.safe_load(stdout)[f"{OAUTH_INTEGRATOR_NAME}/0"]
    for relation in unit_data.get("relation-info", []):
        application_data = relation.get("application-data", {})
        if (
            relation.get("endpoint") == "oauth"
            and "redirect_uri" in application_data
        ):
            return application_data
    raise AssertionError("OAuth client registration data was not published")


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-oauth")
class TestOAuth:
    """Verify OAuth registration and workload configuration."""

    async def test_client_registration(self, ops_test: OpsTest) -> None:
        """Register the HTTPS OIDC callback and requested client settings."""
        relation_data = await _oauth_client_relation_data(ops_test)

        expected_host = f"{ops_test.model_name}-{UI_NAME}.{TRAEFIK_DOMAIN}"
        assert relation_data["redirect_uri"] == (
            f"https://{expected_host}/oauth-authorized/oidc"
        )
        assert relation_data["scope"] == "openid email profile"
        assert json.loads(relation_data["grant_types"]) == [
            "authorization_code"
        ]

    async def test_login_redirects_to_google(self, ops_test: OpsTest) -> None:
        """Use relation data to build a Google authorization redirect."""
        unit_url = await get_unit_url(
            ops_test,
            application=UI_NAME,
            unit=0,
            port=8088,
        )
        response = requests.get(
            f"{unit_url}/login/oidc",
            allow_redirects=False,
            timeout=30,
        )

        assert response.status_code in (302, 303)
        location = response.headers["Location"]
        redirect = urlparse(location)
        query = parse_qs(redirect.query)
        assert redirect.hostname == "accounts.google.com"
        assert query["client_id"] == [OAUTH_STUB_CONFIG["client_id"]]
        assert query["scope"] == [OAUTH_STUB_CONFIG["scope"]]
