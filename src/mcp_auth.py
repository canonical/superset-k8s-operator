#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Auth-status reporting for the mcp charm function."""

from ops import BlockedStatus, WaitingStatus

from literals import MCP_FUNCTION


def auth_status(config, oauth, https_ingress_url):
    """Report on the mcp application's auth configuration.

    None for any other charm function. mcp requires exactly one of the
    oauth relation or mcp-dev-username; with the oauth relation, also
    requires an HTTPS ingress URL to register a client, then waits for
    registration.

    Args:
        config: The charm's validated config.
        oauth: The charm's OAuthRelation instance.
        https_ingress_url: The charm's current HTTPS ingress URL, or None.

    Returns:
        The status to report, or None when mcp's auth is usable.
    """
    if config["charm-function"] != MCP_FUNCTION:
        return None

    related = oauth.is_related()
    dev_username = config["mcp-dev-username"]

    if not related and not dev_username:
        return BlockedStatus(
            "mcp requires either the oauth relation or mcp-dev-username"
        )
    if related and dev_username:
        return BlockedStatus(
            "conflicting mcp auth configuration: both the oauth relation "
            "and mcp-dev-username are set — remove one"
        )
    if dev_username:
        return None
    return _oauth_status(oauth, https_ingress_url)


def _oauth_status(oauth, https_ingress_url):
    """Report on the oauth-relation identity source for mcp.

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
