#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Auth-status reporting and environment config for the mcp charm function."""

from ops import BlockedStatus, ModelError, SecretNotFoundError, WaitingStatus

from literals import MCP_FUNCTION, MCP_JWT_SECRET_KEY


def jwt_secret(model, secret_id):
    """Return the shared secret mcp verifies HS256 bearer tokens against.

    Args:
        model: The charm's Juju model.
        secret_id: The mcp-jwt-secret-id config value.

    Returns:
        The secret's value, or None when secret_id is empty.

    Raises:
        ValueError: When the secret cannot be read or lacks its key.
    """
    if not secret_id:
        return None

    try:
        content = model.get_secret(id=secret_id).get_content(refresh=True)
    except SecretNotFoundError:
        raise ValueError(
            f"mcp-jwt-secret-id '{secret_id}' cannot be found."
        ) from None
    except ModelError:
        raise ValueError(
            f"mcp-jwt-secret-id '{secret_id}' cannot be accessed."
        ) from None

    value = content.get(MCP_JWT_SECRET_KEY, "").strip()
    if not value:
        raise ValueError(
            f"mcp-jwt-secret-id '{secret_id}' has improper schema. "
            f"Missing: {MCP_JWT_SECRET_KEY}"
        )
    return value


def auth_status(config, model, oauth, https_ingress_url):
    """Report on the mcp application's auth configuration.

    None for any other charm function. mcp requires exactly one of three
    identity sources — the oauth relation, mcp-dev-username and
    mcp-jwt-secret-id; see `_oauth_status()`/`_jwt_secret_status()` for
    what each one still has to satisfy once picked.

    Args:
        config: The charm's validated config.
        model: The charm's Juju model.
        oauth: The charm's OAuthRelation instance.
        https_ingress_url: The charm's current HTTPS ingress URL, or None.

    Returns:
        The status to report, or None when mcp's auth is usable.
    """
    if config["charm-function"] != MCP_FUNCTION:
        return None

    dev_username = config["mcp-dev-username"]
    jwt_secret_id = config["mcp-jwt-secret-id"]
    sources_set = sum(
        bool(source)
        for source in (oauth.is_related(), dev_username, jwt_secret_id)
    )

    if sources_set == 0:
        return BlockedStatus(
            "mcp requires the oauth relation, mcp-dev-username, or "
            "mcp-jwt-secret-id"
        )
    if sources_set > 1:
        return BlockedStatus(
            "conflicting mcp auth configuration: only one of the oauth "
            "relation, mcp-dev-username and mcp-jwt-secret-id may be set"
        )
    if dev_username:
        return None
    if jwt_secret_id:
        return _jwt_secret_status(model, jwt_secret_id)
    return _oauth_status(oauth, https_ingress_url)


def _jwt_secret_status(model, jwt_secret_id):
    """Report on the mcp-jwt-secret-id identity source.

    Args:
        model: The charm's Juju model.
        jwt_secret_id: The mcp-jwt-secret-id config value.

    Returns:
        A BlockedStatus if the secret can't be read, None otherwise.
    """
    try:
        jwt_secret(model, jwt_secret_id)
    except ValueError as e:
        return BlockedStatus(str(e))
    return None


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
        "MCP_AUTH_JWT_ACCESS_TOKEN": (
            "true" if provider.jwt_access_token else "false"
        ),
        "MCP_AUTH_CLIENT_ID": provider.client_id or "",
        "MCP_AUTH_CLIENT_SECRET": provider.client_secret or "",
    }


def get_mcp_static_secret_config(model, jwt_secret_id):
    """Return MCP_JWT_SECRET for mcp's shared-secret auth path.

    The alternative to the oauth relation for deployments with no
    external identity provider — see `auth_status()` for how the auth
    sources are kept mutually exclusive.

    Args:
        model: The charm's Juju model.
        jwt_secret_id: The mcp-jwt-secret-id config value.

    Returns:
        The MCP_JWT_SECRET environment value, empty when mcp-jwt-secret-id
        is unset.
    """
    secret = jwt_secret(model, jwt_secret_id)
    if secret is None:
        return {}
    return {"MCP_JWT_SECRET": secret}
