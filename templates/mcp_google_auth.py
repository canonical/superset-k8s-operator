# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Google-backed MCP auth provider for Superset's MCP server.

This module only covers the Google branch. Superset's own
`create_default_mcp_auth_factory` already handles the non-Google case
(Hydra, or any other standard OIDC provider), so that branch isn't ported
here.

Google doesn't support Dynamic Client Registration (DCR), so the MCP
server fronts it with an OAuth proxy: the server acts as its own mini
authorization server, registers callers, sends them to Google's real
login, and issues its own tokens back to the caller.

`GoogleAuthProxy` also accepts a token Google issued directly. This
covers a client an operator registered by hand with Google, outside of
DCR — for example, Gemini Enterprise. Google's access tokens are opaque,
not JWTs, so such a token is checked by asking Google's tokeninfo
endpoint about it. This is a separate mechanism from DCR, not another way
of doing the same thing: fastmcp's own `OAuthProxy` has no setting to
turn DCR off (its `__init__` always enables client registration), and its
fallback for a pre-shared `client_id` still expects the caller to go
through this proxy's own authorize/consent/token flow — not what a
caller holding an already-issued Google token needs.

Client registration can be turned off entirely
(`MCP_AUTH_CLIENT_REGISTRATION=false`, set by the charm), producing a
restricted proxy that honours only a client registered by hand with
Google. That closes the proxy's own client too, not just a caller's
self-registered one — see `GoogleAuthProxy.get_client()` for why
withdrawing registration has to take the proxy's own client down with
it.

The proxy keeps the clients it registers and the tokens it mints in
whatever `client_storage` it is given, which defaults to a store local to
the pod. A caller registered by hand with Google is unaffected, since it
never registers with this proxy at all, but a caller that discovers and
registers with the proxy is only recognised by the unit that handled its
registration — so running more than one `mcp` unit behind Google serves
those callers inconsistently unless `client_storage` is backed by
something shared instead.

`build_google_mcp_auth_factory()` is the entry point. It reads the
MCP_AUTH_* environment variables set by the charm's
`_get_mcp_auth_config()` and returns a callable `(flask_app) ->
AuthProvider` for Superset's `MCP_AUTH_FACTORY` config. It returns None
when the deployment's introspection endpoint isn't Google's — meaning the
oauth relation is Hydra or another standard OIDC provider — so the caller
knows to leave MCP_AUTH_FACTORY unset and use the default JWKS-based
factory instead.

Ported from `canonical/datahub-mcp-k8s-operator`'s `rock/files/serve.py`
(`DirectGoogleTokenProxy`, `OwnClientOnlyProxy`, `GoogleIssuedTokenVerifier`,
`_uses_google_tokeninfo`), provenance only.
"""

import os
from typing import TYPE_CHECKING, Any, Callable, List, Optional
from urllib.parse import urlparse

import httpx
from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.providers.google import GoogleProvider

if TYPE_CHECKING:
    # Not imported at runtime: flask isn't a dependency of the isolated
    # google-auth-proxy tox env this module is tested in (see tox.ini) —
    # only Superset's own workload container has it.
    from flask import Flask

# Google's tokeninfo endpoint, not standard OAuth introspection (RFC
# 7662): it's a plain GET with no credentials, and it reports a bad token
# via an HTTP error status rather than a normal "active": false response.
# A provider that speaks real RFC 7662 should use fastmcp's own
# IntrospectionTokenVerifier instead.
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

    def __init__(self, client_id: str, required_scopes: List[str]) -> None:
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
            # Google not answering, or not answering with JSON, doesn't
            # mean the token is good — so treat it as not good.
            return None

        # `aud` is the client the token was issued to; `azp` is the one
        # that asked for it. They usually agree, but they're separate
        # claims, and either one naming us is enough.
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
    """OAuth proxy that also accepts a token Google issued directly.

    There are two ways a caller reaches this proxy:

    - Through the MCP spec: discover this server as the authorization
      server, register a client with it, and come away holding a token
      this proxy minted itself.
    - Configured by hand: skip discovery, and instead be given Google's
      own authorize/token endpoints plus this deployment's client
      directly. That caller authenticates at Google and ends up holding
      a token Google minted, not this proxy.

    The proxy only recognizes tokens it minted itself, so the second case
    needs extra handling: `load_access_token()` falls back to asking
    Google about a token it doesn't recognize. That check
    (`GoogleIssuedTokenVerifier`) confirms the same thing the proxy would
    otherwise confirm about its own tokens — that the caller went through
    the client this deployment owns.

    `get_client()` refuses every client, including this deployment's
    own, once `registration_enabled` is False — see that method.
    """

    def __init__(
        self,
        *,
        client_id: str,
        registration_enabled: bool = True,
        **kwargs: Any,
    ) -> None:
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

        When registration is disabled, this refuses every client_id,
        including this deployment's own, because its id isn't secret:
        GoogleProvider hands out a public client for that id, with no
        secret or redirect-URI check. An operator-registered client like
        Gemini Enterprise doesn't need this path: it's registered with
        Google directly, not through this proxy, so it never calls
        get_client() at all. load_access_token() verifies it instead.

        Args:
            client_id: The client an incoming request claims to be.

        Returns:
            None when registration is withdrawn, otherwise the registered
            client (if any).
        """
        if not self._registration_enabled:
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


def build_google_mcp_auth_factory() -> (
    Optional[Callable[["Flask"], GoogleAuthProxy]]
):
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

    def _factory(
        app: "Flask",  # noqa: ARG001 - signature required by MCP_AUTH_FACTORY
    ) -> GoogleAuthProxy:
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
            # CIMD lets an unregistered caller register anyway, by handing
            # this proxy a URL as its client_id and having the proxy fetch
            # metadata from that URL. That reopens registration when it's
            # meant to be closed, and lets a caller point the proxy at an
            # arbitrary URL — so keep it off regardless of
            # registration_enabled.
            enable_cimd=False,
        )

    return _factory
