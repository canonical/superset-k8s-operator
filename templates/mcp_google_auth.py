# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Google-backed MCP auth provider for Superset's MCP server.

Ported from `canonical/datahub-mcp-k8s-operator`'s `rock/files/serve.py`
(`DirectGoogleTokenProxy`, `OwnClientOnlyProxy`, `GoogleIssuedTokenVerifier`,
`_uses_google_tokeninfo`) — see that file for the full design rationale.
This module covers only the Google branch: Superset's own
`create_default_mcp_auth_factory` already handles the non-Google case
(Hydra / standard OIDC), so that branch is deliberately not ported here.

Google doesn't support Dynamic Client Registration, so it must be fronted by
an OAuth proxy: the MCP server becomes its own mini authorization server,
registers callers, forwards them to Google's real login, and mints its own
tokens back to the caller. `GoogleAuthProxy` also accepts a token Google
issued directly, for a client an operator registered by hand outside of DCR
(e.g. Gemini Enterprise), verified via Google's tokeninfo endpoint since
Google's access tokens are opaque (not JWTs) — this is a genuinely separate
mechanism from DCR, not an alternative way of doing it: fastmcp's own
`OAuthProxy` has no config surface to disable DCR at all (it hardcodes
client registration on in its own `__init__`), and its built-in fallback
for a pre-shared `client_id` still expects the caller to complete the
authorize/consent/token round-trip through this proxy, which is not what
a caller presenting an already-issued Google token needs.

`build_google_mcp_auth_factory()` is the entry point: it reads the
MCP_AUTH_* environment variables the charm's `_get_mcp_auth_config()` builds
and returns a callable `(flask_app) -> AuthProvider` suitable for
Superset's `MCP_AUTH_FACTORY` config, or None when the deployment's
introspection endpoint isn't Google's (i.e. the oauth relation is Hydra or
another standard OIDC provider), so the caller knows to leave
MCP_AUTH_FACTORY unset and let the default JWKS-based factory run instead.
"""

import os
from typing import Any, List, Optional
from urllib.parse import urlparse

import httpx
from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.providers.google import GoogleProvider

# This is Google's own tokeninfo endpoint, not the standard OAuth
# introspection protocol (RFC 7662). It's a plain GET with no credentials
# of our own to send, and it signals a bad token with an HTTP error status
# rather than a normal response saying so — different enough that we can't
# just swap in another provider's URL here. A provider that speaks real
# RFC 7662 should use fastmcp's own IntrospectionTokenVerifier instead.
GOOGLE_TOKENINFO_HOST = "oauth2.googleapis.com"
GOOGLE_TOKENINFO_URL = f"https://{GOOGLE_TOKENINFO_HOST}/tokeninfo"
GOOGLE_TOKENINFO_TIMEOUT = 10

# Advertised to clients so they know what to ask the provider for.
ADVERTISED_SCOPES = ["openid", "profile", "email"]


class GoogleIssuedTokenVerifier(TokenVerifier):
    """Accept a token Google issued to this deployment's client.

    Google's access tokens are opaque, so they are checked by asking Google
    about them. The answer names the client the token was issued to, and
    only a caller holding that client's credentials could have obtained
    one, so that is what limits the endpoint to the callers an operator set
    up.
    """

    def __init__(self, client_id: str, required_scopes: List[str]):
        """Construct.

        Args:
            client_id: This deployment's own OAuth client.
            required_scopes: Scopes a token must carry to be usable.
        """
        super().__init__(required_scopes=required_scopes)
        self._client_id = client_id

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        """Return the token's details, or None when it is not usable.

        Args:
            token: The bearer token presented by the client.

        Returns:
            An AccessToken when Google recognises the token and issued it
            to this deployment's client, otherwise None.
        """
        try:
            async with httpx.AsyncClient(
                timeout=GOOGLE_TOKENINFO_TIMEOUT
            ) as client:
                response = await client.get(
                    GOOGLE_TOKENINFO_URL, params={"access_token": token}
                )
            if response.status_code != 200:
                return None
            claims = response.json()
        except (httpx.RequestError, ValueError):
            # An endpoint that did not answer, or did not answer with JSON,
            # has not told us the token is good, so it is refused rather
            # than let through on the assumption that Google is temporarily
            # unavailable.
            return None

        # `aud` is the client the token was issued to and `azp` the one
        # that asked for it. For a caller going through this deployment's
        # client the two agree, but they are separate claims and either one
        # naming us is enough.
        if self._client_id not in (claims.get("aud"), claims.get("azp")):
            return None

        scopes = (claims.get("scope") or "").split()
        if not set(self.required_scopes).issubset(scopes):
            return None

        expires_at = None
        if claims.get("exp"):
            try:
                expires_at = int(claims["exp"])
            except (TypeError, ValueError):
                expires_at = None

        return AccessToken(
            token=token,
            client_id=self._client_id,
            scopes=scopes,
            expires_at=expires_at,
            claims=claims,
        )


class GoogleAuthProxy(GoogleProvider):  # pylint: disable=too-many-ancestors
    """OAuth proxy that also honours a token Google issued directly.

    A caller that follows the MCP specification discovers this server as
    its authorization server, obtains a client from it and comes away
    holding a token this proxy minted. A caller configured by hand does
    not discover anything: it is given Google's own authorization and
    token endpoints together with this deployment's client, so it
    authenticates at Google and arrives holding a token Google minted. The
    proxy on its own refuses that token, because it looks for one of its
    own and finds no signature it recognises — asking Google about it
    instead covers both, and establishes the same thing the proxy
    establishes about the tokens it mints itself, namely that the caller
    went through the client this deployment owns.

    When `registration_enabled` is False, self-service registration is
    withdrawn on top of that: every route that acts on behalf of a client
    looks it up through `get_client()` first, so refusing every client but
    this deployment's own there closes the authorize handler, the token
    endpoint's client authentication, and both a code exchange and a
    refresh with it.
    """

    def __init__(
        self, *, client_id: str, registration_enabled: bool = True, **kwargs
    ):
        """Construct.

        Args:
            client_id: This deployment's own OAuth client.
            registration_enabled: Whether a caller may register a client of
                its own through Dynamic Client Registration.
            kwargs: Passed through to GoogleProvider.

        Raises:
            ValueError: If registration_enabled is False but the proxy
                holds no registration options to withdraw, which would
                leave it registering callers this refuses to serve.
        """
        super().__init__(client_id=client_id, **kwargs)
        self._own_client_id = client_id
        self._registration_enabled = registration_enabled
        # The same scopes the proxy demands of the tokens it issues, so
        # which endpoint a caller was pointed at does not change what it
        # must ask for.
        self._direct_verifier = GoogleIssuedTokenVerifier(
            client_id=client_id, required_scopes=self.required_scopes
        )
        if not registration_enabled:
            options = self.client_registration_options
            if options is None:
                raise ValueError(
                    "the OAuth proxy exposed no client registration "
                    "options to withdraw"
                )
            options.enabled = False

    async def load_access_token(self, token: str) -> Optional[Any]:
        """Return what the bearer token authorizes, or None when it authorizes nothing.

        Args:
            token: The bearer token presented by the client.

        Returns:
            An AccessToken from whichever check recognises the token,
            otherwise None.
        """
        access = await super().load_access_token(token)
        if access is not None:
            return access
        return await self._direct_verifier.verify_token(token)

    async def get_client(self, client_id: str) -> Optional[Any]:
        """Return the registered client with this identifier, if it is usable.

        Args:
            client_id: The client an incoming request claims to be.

        Returns:
            None when registration is withdrawn and the client is not this
            deployment's own, otherwise the registered client (if any).
        """
        if not self._registration_enabled and client_id != self._own_client_id:
            return None
        return await super().get_client(client_id)


def _required(name: str) -> str:
    """Return an environment variable that Google auth cannot work without.

    Superset's own `_create_auth_provider()` (mcp_service/server.py) catches
    any exception this raises and silently falls back to no auth provider
    at all, logging only a bare error string — so this is a last-resort
    check, not the thing actually preventing a misconfigured deployment
    from running unauthenticated. The charm validates these values itself
    before starting the workload; this should never actually fire.

    Args:
        name: Name of the variable.

    Returns:
        Its value.

    Raises:
        ValueError: If the variable is unset or empty.
    """
    value = os.getenv(name)
    if not value:
        raise ValueError(
            f"Google MCP auth is configured but {name} is not set"
        )
    return value


def _client_registration_enabled() -> bool:
    """Return whether callers may obtain a client of their own.

    Returns:
        False only when the charm explicitly disabled registration via
        MCP_AUTH_CLIENT_REGISTRATION=false.
    """
    return (
        os.getenv("MCP_AUTH_CLIENT_REGISTRATION") or "true"
    ).lower() != "false"


def build_google_mcp_auth_factory():
    """Return a Google-backed MCP auth provider factory, or None.

    Reads the MCP_AUTH_* environment variables the charm's
    _get_mcp_auth_config() sets.

    Returns:
        A callable `(flask_app) -> AuthProvider` when
        MCP_AUTH_INTROSPECTION_URL is Google's tokeninfo endpoint,
        otherwise None (the caller should leave MCP_AUTH_FACTORY unset).
    """
    introspection_url = os.getenv("MCP_AUTH_INTROSPECTION_URL") or ""
    if urlparse(introspection_url).hostname != GOOGLE_TOKENINFO_HOST:
        return None

    base_url = _required("MCP_AUTH_BASE_URL")
    client_id = _required("MCP_AUTH_CLIENT_ID")
    client_secret = _required("MCP_AUTH_CLIENT_SECRET")
    registration_enabled = _client_registration_enabled()

    def _factory(app):  # noqa: ARG001 - signature required by MCP_AUTH_FACTORY
        """Build the Google auth provider MCP_AUTH_FACTORY consumes.

        Args:
            app: The Flask app, unused — required by MCP_AUTH_FACTORY's
                signature.

        Returns:
            A GoogleAuthProxy instance.
        """
        return GoogleAuthProxy(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            registration_enabled=registration_enabled,
            required_scopes=["openid"],
            valid_scopes=ADVERTISED_SCOPES,
            # CIMD is a second, URL-based self-registration path independent
            # of registration_enabled: an unregistered caller can hand this
            # proxy a client_id shaped like a URL and have it fetched as a
            # metadata document. That both reopens registration when it was
            # meant to be closed and lets a caller point the proxy at an
            # arbitrary URL, so it stays off regardless of
            # registration_enabled.
            enable_cimd=False,
        )

    return _factory
