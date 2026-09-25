"""Bridge FastMCP's per-request JWT identity into get_user_from_request.

6.1.0's get_user_from_request() only checks g.user and MCP_DEV_USERNAME -
it never reads the JWTVerifier's per-request AccessToken, so a
bearer-authenticated call resolves no user at all and every RBAC check
downstream of it never runs (fixed on Superset master; this backports
the fix). Only installed as get_user_from_request when mcp's own JWKS
auth is configured — see superset_config.py.
"""


def _identity_candidates(claims, client_id=None):
    """Identities worth trying as a Superset username, in priority order.

    Hydra's client_credentials tokens set sub to the client_id itself, which
    is the Superset username directly, so sub is tried before email.

    Args:
        claims: The verified JWT's claims.
        client_id: The token's client_id claim, tried last.

    Returns:
        Candidate usernames, most likely first, with duplicates dropped.
    """
    result = []
    for value in (claims.get("sub"), claims.get("email"), client_id):
        if value and value not in result:
            result.append(value)
    return result


def get_user_from_request_with_jwt():
    """Resolve the Superset user for an MCP tool call.

    Priority: the JWTVerifier's per-request AccessToken, then
    MCP_DEV_USERNAME, then g.user, then fail closed. MCP_DEV_USERNAME is
    checked before g.user: g.user can hold a stale value from a previous
    tool call on the same worker (nothing clears it between calls), which
    would otherwise let that user impersonate whoever the current call
    actually belongs to (fixed upstream on master by the same reordering,
    superset#38747). It resolves an existing user; it never creates one.

    Returns:
        The resolved Superset user.

    Raises:
        ValueError: If no configured identity source resolves a user.
    """
    from fastmcp.server.dependencies import get_access_token
    from flask import current_app, g
    from superset.mcp_service.auth import load_user_with_relationships

    access_token = get_access_token()
    if access_token is not None:
        claims = getattr(access_token, "claims", None) or {}
        candidates = _identity_candidates(
            claims, getattr(access_token, "client_id", None)
        )
        for identity in candidates:
            user = load_user_with_relationships(identity)
            if user:
                return user
        if candidates:
            raise ValueError(
                "JWT authenticated user not found in Superset database"
            )

    dev_username = current_app.config.get("MCP_DEV_USERNAME", "")
    if dev_username:
        user = load_user_with_relationships(dev_username)
        if user:
            return user

    if hasattr(g, "user") and g.user:
        return g.user

    raise ValueError(
        "No authenticated user found. "
        "Pass a valid JWT bearer token or configure MCP_DEV_USERNAME."
    )
