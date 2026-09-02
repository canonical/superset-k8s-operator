# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for templates/mcp_google_auth.py.

Ported (and pared down to what this module actually contains) from
datahub-mcp-k8s-operator's tests/serve/test_serve.py, which tests the
source this was itself ported from.
"""

import asyncio

import httpx
import mcp_google_auth as m
import pytest
import respx

BASE_URL = "https://mcp.superset.example"
CLIENT_ID = "superset-mcp"
GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
HYDRA_INTROSPECTION = "https://hydra.example.com/admin/oauth2/introspect"


def _verify(verifier, token="a-token"):  # nosec B107
    """Run a verifier's async check from a synchronous test.

    Args:
        verifier: The TokenVerifier under test.
        token: The bearer token to check.

    Returns:
        Whatever verify_token() returns.
    """
    return asyncio.run(verifier.verify_token(token))


class TestUsesGoogleTokeninfo:
    """Tests for detecting a Google-backed oauth relation."""

    def test_matches_googles_own_endpoint(self):
        """This is the literal value the charm sets for Google mode."""
        assert m._uses_google_tokeninfo(GOOGLE_TOKENINFO) is True

    def test_does_not_match_a_standard_provider(self):
        """Hydra (or any other OIDC provider) must not be mistaken for Google."""
        assert m._uses_google_tokeninfo(HYDRA_INTROSPECTION) is False

    def test_does_not_match_an_empty_value(self):
        """No oauth relation yet means no introspection URL at all."""
        assert m._uses_google_tokeninfo("") is False


class TestGoogleIssuedTokenVerifier:
    """Tests for the check on a token a caller obtained from Google itself."""

    def _verifier(self):
        """Return a verifier demanding the scope the proxy demands.

        Returns:
            A GoogleIssuedTokenVerifier.
        """
        return m.GoogleIssuedTokenVerifier(
            client_id=CLIENT_ID, required_scopes=["openid"]
        )

    def _tokeninfo(self, **claims):
        """Answer the tokeninfo endpoint with a token issued to our client.

        Args:
            claims: Claims overriding the defaults.

        Returns:
            The mocked route.
        """
        body = {
            "aud": CLIENT_ID,
            "azp": CLIENT_ID,
            "sub": "a-user",
            "scope": "openid",
        }
        body.update(claims)
        return respx.get(GOOGLE_TOKENINFO).mock(
            return_value=httpx.Response(200, json=body)
        )

    @respx.mock
    def test_accepts_a_token_issued_to_our_client(self):
        """This is the caller an operator gave this deployment's client to."""
        self._tokeninfo()

        access = _verify(self._verifier())

        assert access is not None
        assert access.client_id == CLIENT_ID

    @respx.mock
    def test_accepts_a_token_naming_our_client_as_the_authorized_party(self):
        """`azp` and `aud` are separate claims; either one naming us is enough."""
        self._tokeninfo(aud="some-other-app")

        assert _verify(self._verifier()) is not None

    @respx.mock
    def test_rejects_a_token_issued_to_another_application(self):
        """Without this any Google token from any app would open the endpoint."""
        self._tokeninfo(aud="some-other-app", azp="some-other-app")

        assert _verify(self._verifier()) is None

    @respx.mock
    def test_rejects_a_token_that_asked_for_too_little(self):
        """The proxy demands these scopes of its own tokens, so this demands them too."""
        self._tokeninfo(scope="")

        assert _verify(self._verifier()) is None

    @respx.mock
    def test_rejects_a_token_google_does_not_recognise(self):
        """An expired or revoked token is what Google answers this way."""
        respx.get(GOOGLE_TOKENINFO).mock(
            return_value=httpx.Response(400, json={"error": "invalid_token"})
        )

        assert _verify(self._verifier()) is None

    @respx.mock
    def test_an_unreachable_endpoint_refuses_rather_than_admits(self):
        """A provider being briefly unwell is not evidence that a token is good."""
        respx.get(GOOGLE_TOKENINFO).mock(
            side_effect=httpx.ConnectError("no route")
        )

        assert _verify(self._verifier()) is None

    @respx.mock
    def test_the_expiry_google_reports_is_carried_over(self):
        """The token stops working when Google says it does, not when we notice."""
        self._tokeninfo(exp="2000000000")

        assert _verify(self._verifier()).expires_at == 2000000000


class TestDirectGoogleTokenProxy:
    """Tests for the proxy falling back to a token it did not mint itself."""

    def _proxy(self, client_registration=True):
        """Return a proxy constructed the way build_google_mcp_auth_factory() would.

        Args:
            client_registration: Whether callers may self-register.

        Returns:
            A DirectGoogleTokenProxy (or OwnClientOnlyProxy) with routes built.
        """
        cls = (
            m.DirectGoogleTokenProxy
            if client_registration
            else m.OwnClientOnlyProxy
        )
        proxy = cls(
            client_id=CLIENT_ID,
            client_secret="s3cret",  # nosec B106
            base_url=BASE_URL,
            required_scopes=["openid"],
            valid_scopes=m.ADVERTISED_SCOPES,
            enable_cimd=False,
        )
        proxy.get_routes("/mcp")
        return proxy

    def test_the_fallback_is_a_google_issued_token_verifier(self):
        """A caller pointed at Google by hand never reaches the proxy's own tokens."""
        proxy = self._proxy()

        assert isinstance(proxy._direct_verifier, m.GoogleIssuedTokenVerifier)

    @respx.mock
    def test_a_token_google_issued_directly_is_accepted(self):
        """The proxy has no record of this token, so it falls back to Google."""
        respx.get(GOOGLE_TOKENINFO).mock(
            return_value=httpx.Response(
                200,
                json={
                    "aud": CLIENT_ID,
                    "azp": CLIENT_ID,
                    "sub": "a-user",
                    "scope": "openid",
                },
            )
        )
        proxy = self._proxy()

        access = asyncio.run(proxy.load_access_token("a-google-token"))

        assert access is not None
        assert access.client_id == CLIENT_ID

    @respx.mock
    def test_a_token_from_nowhere_is_still_refused(self):
        """Neither the proxy's own store nor Google recognise it."""
        respx.get(GOOGLE_TOKENINFO).mock(
            return_value=httpx.Response(400, json={"error": "invalid_token"})
        )
        proxy = self._proxy()

        assert asyncio.run(proxy.load_access_token("not-a-token")) is None


class TestOwnClientOnlyProxy:
    """Tests for restricting the proxy to the deployment's own client."""

    def _proxy(self):
        """Return an OwnClientOnlyProxy with routes built.

        Returns:
            The proxy.
        """
        proxy = m.OwnClientOnlyProxy(
            client_id=CLIENT_ID,
            client_secret="s3cret",  # nosec B106
            base_url=BASE_URL,
            required_scopes=["openid"],
            valid_scopes=m.ADVERTISED_SCOPES,
            enable_cimd=False,
        )
        proxy.get_routes("/mcp")
        return proxy

    def test_registration_is_withdrawn(self):
        """A caller cannot obtain a client this deployment did not provision."""
        assert self._proxy().client_registration_options.enabled is False

    def test_resolves_its_own_client(self):
        """The one client an operator was given must keep working."""
        proxy = self._proxy()

        client = asyncio.run(proxy.get_client(CLIENT_ID))

        assert client is not None

    def test_refuses_any_other_client(self):
        """Otherwise the registration switch would only be half-enforced."""
        proxy = self._proxy()

        assert asyncio.run(proxy.get_client("some-other-client")) is None


class TestBuildGoogleMcpAuthFactory:
    """Tests for the entry point Superset's MCP_AUTH_FACTORY config calls."""

    def test_none_when_the_provider_is_not_google(self, oauth_env):
        """Hydra (or any standard OIDC provider) must use the default JWKS path.

        Args:
            oauth_env: Fixture setting the workload's MCP_AUTH_* environment.
        """
        oauth_env(
            issuer="https://hydra.example.com",
            introspection_url=HYDRA_INTROSPECTION,
            client_id=CLIENT_ID,
            client_secret="s3cret",  # nosec B106
        )

        assert m.build_google_mcp_auth_factory() is None

    def test_none_without_any_oauth_relation(self, oauth_env):
        """mcp-auth-enabled=false / no relation yet must not crash config load.

        Args:
            oauth_env: Fixture setting the workload's MCP_AUTH_* environment.
        """
        oauth_env()

        assert m.build_google_mcp_auth_factory() is None

    def test_builds_a_registering_proxy_by_default(self, oauth_env):
        """The default lets a developer's own machine self-register.

        Args:
            oauth_env: Fixture setting the workload's MCP_AUTH_* environment.
        """
        oauth_env(
            introspection_url=GOOGLE_TOKENINFO,
            client_id=CLIENT_ID,
            client_secret="s3cret",  # nosec B106
            base_url=BASE_URL,
        )

        factory = m.build_google_mcp_auth_factory()

        assert factory is not None
        assert isinstance(factory(None), m.DirectGoogleTokenProxy)
        assert not isinstance(factory(None), m.OwnClientOnlyProxy)

    def test_builds_a_restricted_proxy_when_registration_is_off(
        self, oauth_env
    ):
        """mcp-auth-client-registration=false limits callers to Gemini Enterprise etc.

        Args:
            oauth_env: Fixture setting the workload's MCP_AUTH_* environment.
        """
        oauth_env(
            introspection_url=GOOGLE_TOKENINFO,
            client_id=CLIENT_ID,
            client_secret="s3cret",  # nosec B106
            base_url=BASE_URL,
            client_registration="false",
        )

        factory = m.build_google_mcp_auth_factory()

        assert isinstance(factory(None), m.OwnClientOnlyProxy)

    @pytest.mark.parametrize(
        "missing", ["client_id", "client_secret", "base_url"]
    )
    def test_raises_when_a_required_variable_is_missing(
        self, oauth_env, missing
    ):
        """A half-configured Google mode must fail loudly, not silently open MCP.

        Args:
            oauth_env: Fixture setting the workload's MCP_AUTH_* environment.
            missing: Which of the required variables to leave unset.
        """
        values = {
            "introspection_url": GOOGLE_TOKENINFO,
            "client_id": CLIENT_ID,
            "client_secret": "s3cret",  # nosec B105
            "base_url": BASE_URL,
        }
        del values[missing]
        oauth_env(**values)

        with pytest.raises(ValueError):
            m.build_google_mcp_auth_factory()
