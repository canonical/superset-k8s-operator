#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Auth-status reporting for the mcp charm function."""

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

    Returns None outright for any charm function other than mcp.
    Otherwise mcp has three mutually exclusive identity sources — the
    oauth relation, mcp-dev-username and mcp-jwt-secret-id: blocks if
    none of the three are set, blocks if more than one is set, proceeds
    if mcp-dev-username alone is set, proceeds if mcp-jwt-secret-id
    alone is set once the secret itself reads cleanly, and — if the
    oauth relation alone is set — blocks until it has an HTTPS ingress
    URL to register a client with, then waits until that registration
    completes.

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
        try:
            jwt_secret(model, jwt_secret_id)
        except ValueError as e:
            return BlockedStatus(str(e))
        return None
    if https_ingress_url is None:
        return BlockedStatus("OAuth requires an HTTPS ingress URL")
    if oauth.provider_info() is None:
        return WaitingStatus("waiting for the oauth relation to be ready")
    return None
