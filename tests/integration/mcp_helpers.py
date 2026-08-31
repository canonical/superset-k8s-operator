#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""MCP test helpers for integration tests."""

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Optional

import requests as _requests
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

MCP_PORT = 5008
MCP_ENDPOINT = "/mcp"

# Hydra deployment constants
HYDRA_APP = "hydra"
HYDRA_CHANNEL = "latest/edge"
HYDRA_PUBLIC_PORT = 4444
HYDRA_ADMIN_PORT = 4445
LOGIN_UI_APP = "identity-platform-login-ui-operator"
LOGIN_UI_CHANNEL = "latest/stable"

ALPHA_ROLE_ID = 3
GAMMA_ROLE_ID = 4
SQLAB_ROLE_ID = 5

SUPERSET_ADMIN_USER = "admin"
SUPERSET_ADMIN_PASSWORD = "admin"


def api(method: str, url: str, body=None, headers: Optional[dict] = None) -> tuple[int, dict]:
    """Make HTTP request to Superset or MCP API.

    Args:
        method: HTTP method (GET, POST, etc.)
        url: Full URL endpoint
        body: Request body (dict, will be JSON encoded)
        headers: Additional HTTP headers

    Returns:
        Tuple of (status_code, response_dict)
    """
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def get_hydra_token(token_url: str, client_id: str, client_secret: str) -> str:
    """Obtain a JWT access token from Hydra via the client_credentials grant.

    Tokens issued this way carry ``sub = client_id``, so creating a Hydra
    client whose id matches a Superset username lets the MCP user resolver
    find the right user without extra claim mapping.

    Args:
        token_url: Hydra public token endpoint (e.g. http://host:4444/oauth2/token).
        client_id: OAuth client id (must match a Superset username for RBAC tests).
        client_secret: Client secret registered with Hydra.

    Returns:
        Raw JWT access token string.

    Raises:
        RuntimeError: if Hydra returns an error or no access_token.
    """
    resp = _requests.post(
        token_url,
        data={"grant_type": "client_credentials", "scope": "openid"},
        auth=(client_id, client_secret),
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(
            f"Hydra returned no access_token for client {client_id!r}: {resp.json()}"
        )
    return token


def create_hydra_client(
    admin_url: str,
    client_id: str,
    client_secret: str,
    audience: Optional[list] = None,
) -> None:
    """Register an OAuth client in Hydra via the admin API.

    Idempotent: if the client already exists its secret is updated to
    ``client_secret`` so tests that restore from a snapshot remain valid.

    Args:
        admin_url: Hydra admin base URL (e.g. http://host:4445).
        client_id: Client id to register.
        client_secret: Client secret.
        audience: Optional list of audiences to embed in issued tokens.
            Pass the MCP OAuth client_id here so audience validation passes.
    """
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_types": ["client_credentials"],
        "token_endpoint_auth_method": "client_secret_basic",
        "scope": "openid",
    }
    if audience:
        payload["audience"] = audience

    # Check whether the client already exists.
    check = _requests.get(
        f"{admin_url}/admin/clients/{client_id}", timeout=10
    )
    if check.status_code == 200:
        resp = _requests.put(
            f"{admin_url}/admin/clients/{client_id}",
            json=payload,
            timeout=10,
        )
    else:
        resp = _requests.post(
            f"{admin_url}/admin/clients",
            json=payload,
            timeout=10,
        )
    resp.raise_for_status()
    logger.info("Hydra client %r registered/updated", client_id)


async def setup_hydra_test_clients(
    ops_test: OpsTest,
    usernames: list[str],
    mcp_client_id: str,
    client_secret_suffix: str = "_pass123",
) -> dict[str, str]:
    """Create Hydra OAuth clients whose ids match Superset usernames.

    Tokens issued via client_credentials carry ``sub = client_id``, which
    the MCP user resolver maps directly to a Superset username.  The MCP
    OAuth client_id is set as the audience so our validator accepts the tokens.

    Args:
        ops_test: Pytest-operator test context.
        usernames: List of Superset usernames to register as Hydra clients.
        mcp_client_id: The MCP service's own OAuth client_id (audience value).
        client_secret_suffix: Appended to each username to form the secret.

    Returns:
        Dict mapping username → client_secret.
    """
    admin_url = await get_unit_url(ops_test, HYDRA_APP, 0, HYDRA_ADMIN_PORT)
    secrets = {}
    for username in usernames:
        secret = f"{username}{client_secret_suffix}"
        create_hydra_client(admin_url, username, secret, audience=[mcp_client_id])
        secrets[username] = secret
    return secrets


async def get_mcp_oauth_client_id(ops_test: OpsTest, app_name: str) -> str:
    """Read MCP_AUTH_CLIENT_ID from the running charm's MCP pebble service env.

    Args:
        ops_test: Pytest-operator test context.
        app_name: Superset application name.

    Returns:
        The MCP OAuth client_id string, or empty string if not found.
    """
    unit = ops_test.model.applications[app_name].units[0]
    action = await unit.run("env", timeout=30)
    await action.wait()
    stdout = action.results.get("stdout", "")
    for line in stdout.splitlines():
        if line.startswith("MCP_AUTH_CLIENT_ID="):
            return line.split("=", 1)[1].strip()
    return ""


async def get_unit_url(ops_test: OpsTest, application: str, unit: int, port: int) -> str:
    """Return a unit's HTTP URL.

    Args:
        ops_test: Pytest-operator test context.
        application: Application name.
        unit: Unit index.
        port: TCP port.

    Returns:
        URL string like ``http://10.x.x.x:port``.
    """
    from integration.helpers import get_unit_url as _get_unit_url
    return await _get_unit_url(ops_test, application, unit, port)


def mcp_call(
    url: str, tool: str, arguments: dict, token: Optional[str] = None
) -> tuple[int, str]:
    """Call MCP tool via HTTP.

    Args:
        url: MCP server URL (e.g., http://localhost:5008/mcp)
        tool: Tool name (e.g., 'get_instance_info', 'execute_sql')
        arguments: Tool arguments dict
        token: JWT token for Authorization header (optional)

    Returns:
        Tuple of (status_code, response_text)
    """
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            for raw in resp:
                line = raw.decode().strip()
                if not line.startswith("data:"):
                    continue
                obj = json.loads(line[5:])
                if "result" in obj:
                    try:
                        return status, obj["result"]["content"][0]["text"]
                    except (KeyError, IndexError):
                        return status, json.dumps(obj)
            return status, ""
    except urllib.error.HTTPError as e:
        return e.code, ""


def superset_login(
    base_url: str, username: str = SUPERSET_ADMIN_USER,
    password: str = SUPERSET_ADMIN_PASSWORD
) -> str:
    """Login to Superset and return API token.

    Args:
        base_url: Superset base URL (e.g., http://localhost:8088)
        username: Username for login
        password: Password for login

    Returns:
        API token string for Bearer auth
    """
    _, response = api(
        "POST",
        f"{base_url}/api/v1/security/login",
        {"username": username, "password": password, "provider": "db"},
    )
    api_token = response.get("access_token")
    if not api_token:
        raise RuntimeError(f"Superset login failed: {response}")
    return api_token


def ensure_role(base_url: str, api_token: str, role_name: str) -> int:
    """Ensure role exists in Superset, creating if needed.

    Args:
        base_url: Superset base URL
        api_token: API token for auth
        role_name: Role name to ensure

    Returns:
        Role ID
    """
    auth = {"Authorization": f"Bearer {api_token}"}

    _, data = api("GET", f"{base_url}/api/v1/security/roles/?q=(page_size:50)", headers=auth)
    for role in data.get("result", []):
        if role["name"] == role_name:
            logger.info(f"Role '{role_name}' already exists (id={role['id']})")
            return role["id"]

    _, data = api(
        "POST",
        f"{base_url}/api/v1/security/roles/",
        {"name": role_name},
        headers=auth,
    )
    role_id = data.get("id") or data.get("result", {}).get("id")
    if not role_id:
        raise RuntimeError(f"Failed to create role '{role_name}': {data}")
    logger.info(f"Created role '{role_name}' (id={role_id})")
    return role_id


def ensure_user(
    base_url: str, api_token: str, username: str, password: str,
    role_ids: list[int]
) -> int:
    """Ensure user exists in Superset, creating if needed.

    Args:
        base_url: Superset base URL
        api_token: API token for auth
        username: Username
        password: User password
        role_ids: List of role IDs to assign

    Returns:
        User ID
    """
    auth = {"Authorization": f"Bearer {api_token}"}

    _, data = api("GET", f"{base_url}/api/v1/security/users/?q=(page_size:50)", headers=auth)
    for user in data.get("result", []):
        if user["username"] == username:
            logger.info(f"User '{username}' already exists (id={user['id']})")
            api(
                "PUT",
                f"{base_url}/api/v1/security/users/{user['id']}",
                {
                    "username": username,
                    "password": password,
                    "first_name": username.title(),
                    "last_name": "User",
                    "email": f"{username}@example.com",
                    "active": True,
                    "roles": role_ids,
                },
                headers=auth,
            )
            return user["id"]

    _, data = api(
        "POST",
        f"{base_url}/api/v1/security/users/",
        {
            "username": username,
            "password": password,
            "first_name": username.title(),
            "last_name": "User",
            "email": f"{username}@example.com",
            "active": True,
            "roles": role_ids,
        },
        headers=auth,
    )
    user_id = data.get("id") or data.get("result", {}).get("id")
    if not user_id:
        raise RuntimeError(f"Failed to create user '{username}': {data}")
    logger.info(f"Created user '{username}' (id={user_id})")
    return user_id


def get_dataset_id_by_name(base_url: str, api_token: str, table_name: str) -> int:
    """Get dataset ID by table name.

    Args:
        base_url: Superset base URL
        api_token: API token for auth
        table_name: Table name to search for

    Returns:
        Dataset ID
    """
    auth = {"Authorization": f"Bearer {api_token}"}
    _, data = api(
        "GET", f"{base_url}/api/v1/dataset/?q=(page_size:100)", headers=auth
    )
    for dataset in data.get("result", []):
        if dataset["table_name"] == table_name:
            return dataset["id"]
    raise RuntimeError(f"Dataset '{table_name}' not found. Is load-examples=true?")


def ensure_rls_rule(
    base_url: str, api_token: str, name: str, clause: str,
    role_ids: list[int], table_ids: list[int]
) -> int:
    """Ensure Row-Level Security rule exists, creating if needed.

    Args:
        base_url: Superset base URL
        api_token: API token for auth
        name: RLS rule name
        clause: SQL WHERE clause (e.g., "gender = 'boy'")
        role_ids: List of role IDs the rule applies to
        table_ids: List of table IDs the rule applies to

    Returns:
        RLS rule ID
    """

    auth_headers = {"Authorization": f"Bearer {api_token}"}

    _, data = api("GET", f"{base_url}/api/v1/rowlevelsecurity/?q=(page_size:50)", headers=auth_headers)
    for rule in data.get("result", []):
        if rule["name"] == name:
            logger.info(f"RLS rule '{name}' already exists (id={rule['id']})")
            return rule["id"]

    # CSRF requires a session cookie. Log in via the JSON API to get both
    # the Bearer token and a session cookie in one session object, then
    # fetch the CSRF token from that session.
    session = _requests.Session()
    login_resp = session.post(
        f"{base_url}/api/v1/security/login",
        json={"username": SUPERSET_ADMIN_USER, "password": SUPERSET_ADMIN_PASSWORD, "provider": "db"},
        timeout=30,
    )
    login_resp.raise_for_status()
    session_token = login_resp.json()["access_token"]

    csrf_resp = session.get(
        f"{base_url}/api/v1/security/csrf_token/",
        headers={"Authorization": f"Bearer {session_token}"},
        timeout=30,
    )
    csrf_resp.raise_for_status()
    csrf_token = csrf_resp.json().get("result", "")

    headers = {
        "Authorization": f"Bearer {session_token}",
        "X-CSRFToken": csrf_token,
        "Referer": base_url,
        "Content-Type": "application/json",
    }
    body = {
        "name": name,
        "clause": clause,
        "filter_type": "Regular",
        "tables": table_ids,
        "roles": role_ids,
        "group_key": "",
        "description": f"Demo: {clause}",
    }
    resp = session.post(
        f"{base_url}/api/v1/rowlevelsecurity/",
        json=body,
        headers=headers,
        timeout=30,
    )
    response = resp.json()

    rule_id = response.get("id") or response.get("result", {}).get("id")
    if not rule_id:
        raise RuntimeError(f"Failed to create RLS rule '{name}': {response}")
    logger.info(f"Created RLS rule '{name}' (id={rule_id}): {clause}")
    return rule_id


def setup_mcp_fixtures(superset_url: str, api_token: str) -> dict:
    """Set up MCP test fixtures (users, roles, RLS rules).

    Args:
        superset_url: Superset base URL
        api_token: API token for auth

    Returns:
        Dict with fixture info (user IDs, role IDs, etc.)
    """
    logger.info("Setting up MCP test fixtures...")

    birth_names_id = get_dataset_id_by_name(superset_url, api_token, "birth_names")
    logger.info(f"birth_names dataset id={birth_names_id}")

    rls_role_id = ensure_role(superset_url, api_token, "SqlLabRLS")

    gamma_user_id = ensure_user(
        superset_url, api_token, "gamma_user", "gamma_pass123", [GAMMA_ROLE_ID]
    )

    sqlab_user_id = ensure_user(
        superset_url, api_token, "sqlab_user", "sqlab_pass123",
        [ALPHA_ROLE_ID, SQLAB_ROLE_ID, rls_role_id]
    )

    logger.info("Setup complete.")

    return {
        "gamma_user_id": gamma_user_id,
        "sqlab_user_id": sqlab_user_id,
        "rls_role_id": rls_role_id,
        "birth_names_id": birth_names_id,
    }
