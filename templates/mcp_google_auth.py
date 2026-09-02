# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Google-backed MCP auth provider for Superset's MCP server.

Ported from `canonical/datahub-mcp-k8s-operator`'s `rock/files/serve.py`
(`DirectGoogleTokenProxy`, `OwnClientOnlyProxy`, `GoogleIssuedTokenVerifier`,
`_uses_google_tokeninfo`) — see that file for the full design rationale.
This module covers only the Google branch: Superset's own
`create_default_mcp_auth_factory` already handles the non-Google case
(Hydra / standard OIDC), with more Superset-specific features (API keys,
guest tokens, RBAC) than would be gained by reusing serve.py's generic
`RemoteAuthProvider` path, so that branch is deliberately not ported here.

Google doesn't support Dynamic Client Registration, so it must be fronted by
an OAuth proxy: the MCP server becomes its own mini authorization server,
registers callers, forwards them to Google's real login, and mints its own
tokens back to the caller. `DirectGoogleTokenProxy` also accepts a token
Google issued directly, for a client an operator registered by hand outside
of DCR (e.g. Gemini Enterprise), verified via Google's tokeninfo endpoint
since Google's access tokens are opaque (not JWTs).

`build_google_mcp_auth_factory()` is the entry point: it reads the
MCP_AUTH_* environment variables the charm's `_get_mcp_auth_env()` builds
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

# Google checks tokens through its own endpoint instead of the standard one.
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


class DirectGoogleTokenProxy(
    GoogleProvider
):  # pylint: disable=too-many-ancestors
    """OAuth proxy that also honours a token Google issued directly.

    A caller that follows the MCP specification discovers this server as
    its authorization server, obtains a client from it and comes away
    holding a token this proxy minted. A caller configured by hand does
    not discover anything: it is given Google's own authorization and
    token endpoints together with this deployment's client, so it
    authenticates at Google and arrives holding a token Google minted.

    The proxy on its own refuses that token, because it looks for one of
    its own and finds no signature it recognises. Asking Google about it
    instead covers both, and establishes the same thing the proxy
    establishes about the tokens it mints itself, namely that the caller
    went through the client this deployment owns.
    """

    def __init__(self, *, client_id: str, **kwargs):
        """Construct.

        Args:
            client_id: This deployment's own OAuth client.
            kwargs: Passed through to GoogleProvider.
        """
        super().__init__(client_id=client_id, **kwargs)
        # The same scopes the proxy demands of the tokens it issues, so
        # which endpoint a caller was pointed at does not change what it
        # must ask for.
        self._direct_verifier = GoogleIssuedTokenVerifier(
            client_id=client_id, required_scopes=self.required_scopes
        )

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


class OwnClientOnlyProxy(
    DirectGoogleTokenProxy
):  # pylint: disable=too-many-ancestors
    """OAuth proxy that resolves no client but the one this deployment holds.

    Withdrawing the registration endpoint stops a caller from obtaining a
    client it does not have, but it says nothing about the ones already
    handed out. Every route that acts on behalf of a client looks it up
    here first: the authorize handler directly, and the token endpoint
    through the client authentication that guards it, so a code exchange
    and a refresh are both refused along with the rest.

    The inherited check on a token Google issued needs nothing added: it
    already admits only the client this deployment owns.
    """

    def __init__(self, *, client_id: str, **kwargs):
        """Construct.

        Args:
            client_id: This deployment's own OAuth client, the only one served.
            kwargs: Passed through to GoogleProvider.

        Raises:
            ValueError: If the proxy holds no registration options to
                withdraw, which would leave it registering callers this
                refuses to serve.
        """
        super().__init__(client_id=client_id, **kwargs)
        self._own_client_id = client_id
        options = self.client_registration_options
        if options is None:
            raise ValueError(
                "the OAuth proxy exposed no client registration options to withdraw"
            )
        options.enabled = False

    async def get_client(self, client_id: str) -> Optional[Any]:
        """Return the registered client with this identifier, if it is ours.

        Args:
            client_id: The client an incoming request claims to be.

        Returns:
            The client when it is this deployment's own, otherwise None.
        """
        if client_id != self._own_client_id:
            return None
        return await super().get_client(client_id)


def _uses_google_tokeninfo(introspection_url: str) -> bool:
    """Return whether the validation endpoint is Google's.

    Args:
        introspection_url: The endpoint published over the oauth relation
            (or, in Google mode, MCP_AUTH_INTROSPECTION_URL as set by the
            charm's _get_mcp_auth_env()).

    Returns:
        True for Google's tokeninfo endpoint, False for a standard one.
    """
    return urlparse(introspection_url).hostname == GOOGLE_TOKENINFO_HOST


def _required(name: str) -> str:
    """Return an environment variable that Google auth cannot work without.

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
    _get_mcp_auth_env() sets. Mirrors the Google branch of serve.py's
    `_auth_provider()`.

    Returns:
        A callable `(flask_app) -> AuthProvider` when
        MCP_AUTH_INTROSPECTION_URL is Google's tokeninfo endpoint,
        otherwise None (the caller should leave MCP_AUTH_FACTORY unset).
    """
    if not _uses_google_tokeninfo(
        os.getenv("MCP_AUTH_INTROSPECTION_URL") or ""
    ):
        return None

    base_url = _required("MCP_AUTH_BASE_URL")
    client_id = _required("MCP_AUTH_CLIENT_ID")
    client_secret = _required("MCP_AUTH_CLIENT_SECRET")
    proxy_cls = (
        DirectGoogleTokenProxy
        if _client_registration_enabled()
        else OwnClientOnlyProxy
    )

    def _factory(app):  # noqa: ARG001 - signature required by MCP_AUTH_FACTORY
        return proxy_cls(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            required_scopes=["openid"],
            valid_scopes=ADVERTISED_SCOPES,
            enable_cimd=False,
        )

    return _factory
