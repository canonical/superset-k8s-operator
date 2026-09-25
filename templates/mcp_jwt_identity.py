"""Bridge FastMCP's per-request JWT identity into get_user_from_request.

6.1.0's get_user_from_request() only checks g.user and MCP_DEV_USERNAME -
it never reads the JWTVerifier's per-request AccessToken, so a
bearer-authenticated call resolves no user at all and every RBAC check
downstream of it never runs (fixed on Superset master; this backports
the fix). Only installed as get_user_from_request when mcp's own JWKS
auth is configured — see superset_config.py.
"""


def _identity_candidates(claims, client_id=None, prefer_email=False):
    """Return candidate Superset usernames from a JWT, most likely first.

    Hydra's client_credentials tokens set sub to the client_id itself, so
    sub is tried before email by default. prefer_email flips that for
    providers (currently only Google, reaching mcp) whose sub is an opaque
    account id rather than a usable username.

    Args:
        claims: The verified JWT's claims.
        client_id: The token's client_id claim, tried last.
        prefer_email: Whether to try email before sub.

    Returns:
        Candidate usernames, most likely first, with duplicates dropped.
    """
    ordered = (
        (claims.get("email"), claims.get("sub"))
        if prefer_email
        else (claims.get("sub"), claims.get("email"))
    )
    result = []
    for value in (*ordered, client_id):
        if value and value not in result:
            result.append(value)
    return result


def _should_prefer_email():
    """Whether the related provider's sub is untrustworthy as a username.

    Returns:
        True if identity resolution should try email before sub.
    """
    import os
    from urllib.parse import urlparse

    from mcp_google_auth import GOOGLE_TOKENINFO_HOST

    introspection_url = os.getenv("MCP_AUTH_INTROSPECTION_URL") or ""
    return urlparse(introspection_url).hostname == GOOGLE_TOKENINFO_HOST


def get_user_from_request_with_jwt():
    """Resolve the Superset user making this MCP tool call.

    Tries three identity sources in order, stopping at the first that
    resolves an existing user:

    1. The JWT bearer token's claims (see _identity_candidates).
    2. MCP_DEV_USERNAME, the charm's configured fallback identity.
    3. g.user, whatever the last request on this worker left behind.

    g.user is checked last on purpose. Nothing clears it between requests,
    so checking it before MCP_DEV_USERNAME would let a stale g.user from a
    previous call impersonate the current caller. Superset's own
    get_user_from_request had this same ordering bug; upstream fixed it on
    master the same way (superset#38747), and this backports that fix.

    Never creates a user — only resolves one that already exists.

    Returns:
        The resolved Superset user.

    Raises:
        ValueError: No identity source resolved an existing user.
    """
    from fastmcp.server.dependencies import get_access_token
    from flask import current_app, g
    from superset.mcp_service.auth import load_user_with_relationships

    access_token = get_access_token()
    if access_token is not None:
        claims = getattr(access_token, "claims", None) or {}
        candidates = _identity_candidates(
            claims,
            getattr(access_token, "client_id", None),
            prefer_email=_should_prefer_email(),
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
