#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Auth-status reporting and environment config for the mcp charm function."""

from ops import BlockedStatus, WaitingStatus

from literals import MCP_FUNCTION


def auth_status(config, oauth, https_ingress_url):
    """Report on the mcp application's auth configuration.

    None for any other charm function. mcp requires exactly one of two
    identity sources — the oauth relation and mcp-dev-username; see
    `_oauth_status()` for what the former still has to satisfy once
    picked.

    Args:
        config: The charm's validated config.
        oauth: The charm's OAuthRelation instance.
        https_ingress_url: The charm's current HTTPS ingress URL, or None.

    Returns:
        The status to report, or None when mcp's auth is usable.
    """
    if config["charm-function"] != MCP_FUNCTION:
        return None

    dev_username = config["mcp-dev-username"]
    related = oauth.is_related()

    if not related and not dev_username:
        return BlockedStatus(
            "mcp requires either the oauth relation or mcp-dev-username"
        )
    if related and dev_username:
        return BlockedStatus(
            "conflicting mcp auth configuration: only one of the oauth "
            "relation and mcp-dev-username may be set"
        )
    if dev_username:
        return None
    return _oauth_status(oauth, https_ingress_url)


def _oauth_status(oauth, https_ingress_url):
    """Report on the oauth-relation identity source.

    Args:
        oauth: The charm's OAuthRelation instance.
        https_ingress_url: The charm's current HTTPS ingress URL, or None.

    Returns:
        The status to report, or None once the oauth relation is ready.
    """
    if https_ingress_url is None:
        return BlockedStatus("OAuth requires an HTTPS ingress URL")
    if oauth.provider_info() is None:
        return WaitingStatus("waiting for the oauth relation to be ready")
    return None


def get_mcp_auth_config(oauth):
    """Return MCP_AUTH_* environment values for mcp's own auth provider.

    Reuses the same oauth relation and the same client_id/client_secret as
    the web UI's OAUTH_* config (_get_oauth_config()) — it's the same
    Hydra client. The separate MCP_AUTH_* namespace exists because these
    values feed a different Superset subsystem: mcp's own JWT-verifying
    auth factory, gated by MCP_AUTH_ENABLED, not the web UI's OAuth login
    flow.

    Args:
        oauth: The charm's OAuthRelation instance.

    Returns:
        The MCP_AUTH_* environment values, empty when OAuth is not
        configured.
    """
    provider = oauth.provider_info()
    if provider is None:
        return {}

    return {
        "MCP_AUTH_ISSUER": provider.issuer_url,
        "MCP_AUTH_JWKS_URL": provider.jwks_endpoint,
        "MCP_AUTH_INTROSPECTION_URL": provider.introspection_endpoint,
        # Not read by superset's create_default_mcp_auth_factory (6.1) —
        # it has no RFC 7662 introspection path, only JWT verification.
        # Kept here for operator visibility (`juju ssh ... env`) only;
        # nothing in the charm or workload currently acts on this value.
        "MCP_AUTH_JWT_ACCESS_TOKEN": (
            "true" if provider.jwt_access_token else "false"
        ),
        "MCP_AUTH_CLIENT_ID": provider.client_id or "",
        "MCP_AUTH_CLIENT_SECRET": provider.client_secret or "",
    }
