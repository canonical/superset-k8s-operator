#!/usr/bin/env python3
"""
Superset MCP JWT auth + RBAC test suite.

Self-contained: creates all fixtures via the Superset REST API,
then validates behaviour through the MCP server.

Usage:
    python3 test-mcp-auth.py \\
        --secret <MCP_JWT_SECRET> \\
        [--superset-url http://host:30088] \\
        [--mcp-url http://host:30508] \\
        [--superset-password admin123]

Prerequisites:
    pip install pyjwt

What is tested:
  1.  Valid admin JWT                            → HTTP 200, current_user = admin
  2.  health_check (no user required)            → HTTP 200, status = healthy
  3.  Tampered token                             → HTTP 401
  4.  Expired token                              → HTTP 401
  5.  No token                                   → HTTP 401
  6.  gamma_user (Gamma role)                    → authenticated, fewer menus than admin
  7.  Unknown username in valid JWT              → HTTP 200, auth error in tool body
  8.  sqlab_user (Alpha + RLS role)              → authenticated, SQL Lab accessible
  9.  sqlab_user execute_sql birth_names         → only gender='boy' rows (RLS active)
  10. admin execute_sql birth_names              → all rows visible (no RLS for admin)

Fixtures created by this script:
  - Role "SqlLabRLS": marker role used to scope the RLS rule
  - User "sqlab_user": Alpha role + SqlLabRLS role (gets SQL Lab + RLS filter)
  - RLS rule on birth_names: gender = 'boy'  (applies to SqlLabRLS role)
"""

import argparse
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import jwt
except ImportError:
    sys.exit("pip install PyJWT")


# ── constants ─────────────────────────────────────────────────────────────────

SUPERSET_ADMIN_USER = "admin"
ISSUER = "superset-k8s"
AUDIENCE = "superset-mcp"

GAMMA_ROLE_ID = 4        # built-in Gamma role (public objects only)
ALPHA_ROLE_ID = 3        # built-in Alpha role (all_datasource_access, dataset access)
SQLAB_ROLE_ID = 5        # built-in sql_lab role (can_execute_sql_query etc.)
BIRTH_NAMES_DATASET_ID = 17


# ── Superset REST API helpers ─────────────────────────────────────────────────

def api(method: str, url: str, body=None, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def session_api(opener, method: str, url: str, body=None,
                extra_headers: dict | None = None) -> tuple[int, dict]:
    """Same as api() but uses a cookie-carrying opener (needed for CSRF-protected endpoints)."""
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if extra_headers:
        h.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with opener.open(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def superset_login(base: str, password: str) -> tuple[str, str, urllib.request.OpenerDirector]:
    """
    Returns (api_token, csrf_token, session_opener).

    The session_opener carries the browser session cookie required by
    CSRF-protected endpoints like POST /api/v1/rowlevelsecurity/.
    """
    # 1. JWT API token (for Bearer-auth endpoints)
    _, d = api("POST", f"{base}/api/v1/security/login",
               {"username": SUPERSET_ADMIN_USER, "password": password, "provider": "db"})
    api_token = d.get("access_token")
    if not api_token:
        sys.exit(f"[ERROR] Superset login failed: {d}")

    # 2. Build a cookie jar opener and do a proper browser session login
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # GET /login/ to receive the pre-login session cookie and HTML CSRF token
    resp = opener.open(f"{base}/login/")
    html = resp.read().decode()
    m = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', html) or \
        re.search(r'value=["\']([^"\']+)["\'][^>]*name=["\']csrf_token["\']', html)
    form_csrf = m.group(1) if m else ""

    # POST login form to establish an authenticated session cookie
    form_data = urllib.parse.urlencode({
        "username": SUPERSET_ADMIN_USER,
        "password": password,
        "csrf_token": form_csrf,
    }).encode()
    opener.open(f"{base}/login/", form_data)

    # 3. Fetch the API-level CSRF token using the session cookie + Bearer token
    req = urllib.request.Request(
        f"{base}/api/v1/security/csrf_token/",
        headers={"Authorization": f"Bearer {api_token}"},
    )
    with opener.open(req) as resp:
        csrf_token = json.loads(resp.read()).get("result", "")

    return api_token, csrf_token, opener


# ── fixture helpers ───────────────────────────────────────────────────────────

def ensure_role(base: str, api_token: str, name: str) -> int:
    auth = {"Authorization": f"Bearer {api_token}"}
    _, d = api("GET", f"{base}/api/v1/security/roles/?q=(page_size:50)", headers=auth)
    for r in d.get("result", []):
        if r["name"] == name:
            print(f"    role '{name}' already exists (id={r['id']})")
            return r["id"]
    _, d = api("POST", f"{base}/api/v1/security/roles/", {"name": name}, headers=auth)
    rid = d.get("id") or d.get("result", {}).get("id")
    if not rid:
        sys.exit(f"[ERROR] Failed to create role '{name}': {d}")
    print(f"    created role '{name}' (id={rid})")
    return rid


def ensure_user(base: str, api_token: str, username: str, password: str,
                first_name: str, last_name: str, email: str, role_ids: list[int]) -> int:
    auth = {"Authorization": f"Bearer {api_token}"}
    _, d = api("GET", f"{base}/api/v1/security/users/?q=(page_size:50)", headers=auth)
    for u in d.get("result", []):
        if u["username"] == username:
            print(f"    user '{username}' already exists (id={u['id']})")
            # Ensure roles are up to date
            api("PUT", f"{base}/api/v1/security/users/{u['id']}", {
                "username": username, "password": password,
                "first_name": first_name, "last_name": last_name,
                "email": email, "active": True, "roles": role_ids,
            }, headers=auth)
            return u["id"]
    _, d = api("POST", f"{base}/api/v1/security/users/", {
        "username": username, "password": password,
        "first_name": first_name, "last_name": last_name,
        "email": email, "active": True, "roles": role_ids,
    }, headers=auth)
    uid = d.get("id") or d.get("result", {}).get("id")
    if not uid:
        sys.exit(f"[ERROR] Failed to create user '{username}': {d}")
    print(f"    created user '{username}' (id={uid})")
    return uid


def ensure_rls(base: str, opener, api_token: str, csrf_token: str,
               name: str, clause: str, role_ids: list[int], table_ids: list[int]) -> int:
    auth_csrf = {
        "Authorization": f"Bearer {api_token}",
        "X-CSRFToken": csrf_token,
        "Referer": base,
    }
    _, d = session_api(opener, "GET", f"{base}/api/v1/rowlevelsecurity/?q=(page_size:50)",
                       extra_headers={"Authorization": f"Bearer {api_token}"})
    for r in d.get("result", []):
        if r["name"] == name:
            print(f"    RLS rule '{name}' already exists (id={r['id']})")
            return r["id"]
    _, d = session_api(opener, "POST", f"{base}/api/v1/rowlevelsecurity/", {
        "name": name, "clause": clause, "filter_type": "Regular",
        "tables": table_ids, "roles": role_ids,
        "group_key": "", "description": f"Demo: {clause}",
    }, extra_headers=auth_csrf)
    rid = d.get("id") or d.get("result", {}).get("id")
    if not rid:
        sys.exit(f"[ERROR] Failed to create RLS rule '{name}': {d}")
    print(f"    created RLS rule '{name}' (id={rid}): {clause}")
    return rid


def get_dataset_id(base: str, api_token: str, table_name: str) -> int:
    auth = {"Authorization": f"Bearer {api_token}"}
    _, d = api("GET", f"{base}/api/v1/dataset/?q=(page_size:100)", headers=auth)
    for ds in d.get("result", []):
        if ds["table_name"] == table_name:
            return ds["id"]
    sys.exit(f"[ERROR] Dataset '{table_name}' not found. Is load-examples=true?")


def setup_fixtures(superset_url: str, password: str) -> None:
    print("\n── Fixture setup ─────────────────────────────────────────────────")
    api_token, csrf_token, opener = superset_login(superset_url, password)

    # Resolve birth_names dataset ID dynamically
    birth_names_id = get_dataset_id(superset_url, api_token, "birth_names")
    print(f"    birth_names dataset id={birth_names_id}")

    # gamma_user: Gamma role only (public objects, no SQL Lab)
    ensure_user(superset_url, api_token, "gamma_user", "gamma_pass123",
                "Gamma", "User", "gamma_user@example.com", [GAMMA_ROLE_ID])

    # Marker role used only to scope the RLS rule — no extra permissions needed
    rls_role_id = ensure_role(superset_url, api_token, "SqlLabRLS")

    # sqlab_user: Alpha (datasource access) + sql_lab (execute SQL) + SqlLabRLS (RLS filter target)
    ensure_user(superset_url, api_token, "sqlab_user", "sqlab_pass123",
                "SQL", "Limited", "sqlab_user@example.com",
                [ALPHA_ROLE_ID, SQLAB_ROLE_ID, rls_role_id])

    # RLS: members of SqlLabRLS only see gender='boy' rows in birth_names
    ensure_rls(superset_url, opener, api_token, csrf_token,
               "demo-rls-birth-names-boy-only", "gender = 'boy'",
               [rls_role_id], [birth_names_id])

    print("── Setup complete ─────────────────────────────────────────────────\n")


# ── MCP call helper ───────────────────────────────────────────────────────────

def mcp_call(url: str, tool: str, arguments: dict, token: str | None) -> tuple[int, str]:
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }).encode()
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
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
                        return status, str(obj)
            return status, ""
    except urllib.error.HTTPError as e:
        return e.code, ""


def make_token(secret: str, sub: str, exp_offset: int = 3600) -> str:
    payload = {
        "sub": sub, "iss": ISSUER, "aud": AUDIENCE,
        "iat": int(time.time()), "exp": int(time.time()) + exp_offset,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ── tests ─────────────────────────────────────────────────────────────────────

def run_tests(mcp_url: str, secret: str) -> bool:
    endpoint = f"{mcp_url}/mcp"
    results: list[bool] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        results.append(passed)

    print(f"Target: {endpoint}\n")

    admin_tok = make_token(secret, "admin")
    sqlab_tok = make_token(secret, "sqlab_user")

    # 1. Valid admin JWT
    print("1. Valid admin JWT → get_instance_info")
    status, text = mcp_call(endpoint, "get_instance_info", {}, admin_tok)
    try:
        d = json.loads(text)
        username = d.get("current_user", {}).get("username", "")
    except Exception:
        username = ""
    check("HTTP 200", status == 200, f"got {status}")
    check("current_user = admin", username == "admin", f"got '{username}'")
    check("No error", "Error:" not in text)

    # 2. health_check
    print("\n2. health_check (no user context required)")
    status, text = mcp_call(endpoint, "health_check", {}, admin_tok)
    try:
        healthy = json.loads(text).get("status") == "healthy"
    except Exception:
        healthy = False
    check("HTTP 200", status == 200, f"got {status}")
    check("status = healthy", healthy, text[:80])

    # 3. Tampered token
    print("\n3. Tampered token → 401")
    status, _ = mcp_call(endpoint, "get_instance_info", {}, admin_tok + "x")
    check("HTTP 401", status == 401, f"got {status}")

    # 4. Expired token
    print("\n4. Expired token → 401")
    status, _ = mcp_call(endpoint, "get_instance_info", {}, make_token(secret, "admin", -60))
    check("HTTP 401", status == 401, f"got {status}")

    # 5. No token
    print("\n5. No token → 401")
    status, _ = mcp_call(endpoint, "get_instance_info", {}, None)
    check("HTTP 401", status == 401, f"got {status}")

    # 6. gamma_user: authenticated but restricted
    print("\n6. gamma_user (Gamma role) → reduced access")
    gamma_tok = make_token(secret, "gamma_user")
    status, text = mcp_call(endpoint, "get_instance_info", {}, gamma_tok)
    try:
        d = json.loads(text)
        gamma_username = d.get("current_user", {}).get("username", "")
        gamma_roles = d.get("current_user", {}).get("roles", [])
        gamma_menus = len(d.get("feature_availability", {}).get("accessible_menus", []))
        _, admin_text = mcp_call(endpoint, "get_instance_info", {}, admin_tok)
        admin_menus = len(json.loads(admin_text).get("feature_availability", {}).get("accessible_menus", []))
    except Exception:
        gamma_username, gamma_roles, gamma_menus, admin_menus = "", [], 0, 26
    check("HTTP 200", status == 200, f"got {status}")
    check("gamma_user authenticated", gamma_username == "gamma_user", f"got '{gamma_username}'")
    check("Gamma role assigned", "Gamma" in gamma_roles, f"got {gamma_roles}")
    check(f"Fewer menus than admin ({gamma_menus} < {admin_menus})",
          gamma_menus < admin_menus, f"gamma={gamma_menus} admin={admin_menus}")

    # 7. Unknown username in valid JWT
    print("\n7. Unknown username in valid JWT → auth error in body")
    status, text = mcp_call(endpoint, "get_instance_info", {}, make_token(secret, "ghost_user"))
    check("HTTP 200 (JWT signature valid)", status == 200, f"got {status}")
    check("Auth error in body", "No authenticated user found" in text, text[:80])

    # 8. sqlab_user: authenticated
    print("\n8. sqlab_user (Alpha + SqlLabRLS) → authenticated with SQL Lab access")
    status, text = mcp_call(endpoint, "get_instance_info", {}, sqlab_tok)
    try:
        d = json.loads(text)
        sqlab_username = d.get("current_user", {}).get("username", "")
        sqlab_roles = d.get("current_user", {}).get("roles", [])
    except Exception:
        sqlab_username, sqlab_roles = "", []
    check("HTTP 200", status == 200, f"got {status}")
    check("sqlab_user authenticated", sqlab_username == "sqlab_user", f"got '{sqlab_username}'")
    check("Has Alpha role (datasource access)", "Alpha" in sqlab_roles, f"got {sqlab_roles}")
    check("Has sql_lab role (SQL Lab execute)", "sql_lab" in sqlab_roles, f"got {sqlab_roles}")
    check("Has SqlLabRLS role (RLS target)", "SqlLabRLS" in sqlab_roles, f"got {sqlab_roles}")

    SQL = "SELECT gender, COUNT(*) as cnt FROM birth_names GROUP BY gender ORDER BY gender"

    # 9. sqlab_user: RLS filters birth_names to gender='boy' only
    print("\n9. sqlab_user execute_sql birth_names → RLS: only gender='boy' rows visible")
    status, text = mcp_call(endpoint, "execute_sql",
                            {"request": {"database_id": 1, "sql": SQL}}, sqlab_tok)
    try:
        rows = json.loads(text).get("rows", [])
        genders = {r["gender"] for r in rows}
    except Exception:
        genders = set()
    check("HTTP 200", status == 200, f"got {status}")
    check("Query succeeded (SQL Lab access works)", "Error:" not in text, text[:100])
    check("Only 'boy' rows returned (RLS active)", genders == {"boy"}, f"got {genders}")
    check("'girl' rows hidden by RLS", "girl" not in genders)

    # 10. admin: no RLS, sees all rows
    print("\n10. admin execute_sql birth_names → no RLS, all rows visible")
    status, text = mcp_call(endpoint, "execute_sql",
                            {"request": {"database_id": 1, "sql": SQL}}, admin_tok)
    try:
        rows = json.loads(text).get("rows", [])
        genders = {r["gender"] for r in rows}
    except Exception:
        genders = set()
    check("HTTP 200", status == 200, f"got {status}")
    check("Both 'boy' and 'girl' visible", genders == {"boy", "girl"}, f"got {genders}")

    passed = sum(results)
    total = len(results)
    print(f"\n{'─'*50}")
    print(f"Results: {passed}/{total} passed")
    if passed < total:
        print("FAILED")
    return passed == total


def main():
    parser = argparse.ArgumentParser(description="Superset MCP JWT auth + RBAC tests")
    parser.add_argument("--secret", required=True, help="JWT HS256 secret (MCP_JWT_SECRET)")
    parser.add_argument("--mcp-url", default="http://10.223.71.200:30508")
    parser.add_argument("--superset-url", default="http://10.223.71.200:30088")
    parser.add_argument("--superset-password", default="admin123")
    parser.add_argument("--skip-setup", action="store_true",
                        help="Skip fixture creation (already exists)")
    args = parser.parse_args()

    if not args.skip_setup:
        setup_fixtures(args.superset_url, args.superset_password)

    ok = run_tests(args.mcp_url, args.secret)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
