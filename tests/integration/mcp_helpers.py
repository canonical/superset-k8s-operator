#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""MCP test helpers for integration tests."""

import http.cookiejar
import json
import jwt
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

MCP_PORT = 5008
MCP_ENDPOINT = "/mcp"
ISSUER = "superset-k8s"
AUDIENCE = "superset-mcp"
TOKEN_TTL_SECONDS = 3600

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


def make_token(username: str, secret: str, exp_offset: int = TOKEN_TTL_SECONDS) -> str:
    """Generate JWT token for MCP authentication.

    Args:
        username: Subject claim (username)
        secret: HS256 signing secret
        exp_offset: Token expiration offset in seconds (default: 3600)

    Returns:
        Encoded JWT token string
    """
    now = int(time.time())
    payload = {
        "sub": username,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + exp_offset,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


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

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    resp = opener.open(f"{base_url}/login/")
    html = resp.read().decode()
    m = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', html) or \
        re.search(r'value=["\']([^"\']+)["\'][^>]*name=["\']csrf_token["\']', html)
    form_csrf = m.group(1) if m else ""

    form_data = urllib.parse.urlencode({
        "username": SUPERSET_ADMIN_USER,
        "password": SUPERSET_ADMIN_PASSWORD,
        "csrf_token": form_csrf,
    }).encode()
    opener.open(f"{base_url}/login/", form_data)

    req = urllib.request.Request(
        f"{base_url}/api/v1/security/csrf_token/", headers=auth_headers
    )
    with opener.open(req) as resp:
        csrf_token = json.loads(resp.read()).get("result", "")

    headers = {
        "Authorization": f"Bearer {api_token}",
        "X-CSRFToken": csrf_token,
        "Referer": base_url,
        "Content-Type": "application/json",
    }

    body = json.dumps({
        "name": name,
        "clause": clause,
        "filter_type": "Regular",
        "tables": table_ids,
        "roles": role_ids,
        "group_key": "",
        "description": f"Demo: {clause}",
    }).encode()

    req = urllib.request.Request(
        f"{base_url}/api/v1/rowlevelsecurity/",
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with opener.open(req, timeout=30) as resp:
            response = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        response = json.loads(e.read())

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
