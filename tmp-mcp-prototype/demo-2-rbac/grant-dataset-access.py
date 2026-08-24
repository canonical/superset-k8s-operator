# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

#!/usr/bin/env python3
"""
Grant a Superset user (e.g. gamma_user) datasource_access to a dataset by name.

Dashboard ownership (grant-dashboard-access.py) is enough to make a dashboard show
up in list_dashboards/get_dashboard_info, but Gamma users still need an explicit
datasource_access permission before list_datasets/list_charts/execute_sql return
anything for the underlying data. This script:

  1. finds the permission_view_menu id for "datasource_access on <dataset perm>"
     (Superset auto-creates one per dataset — visible via
     GET /api/v1/security/permissions-resources/)
  2. creates a dedicated role scoped to just that dataset (so you're not widening
     access to everything Gamma users can already reach)
  3. adds that permission to the role via POST /api/v1/security/roles/{id}/permissions
  4. assigns the role to the target user

Usage:
    python3 grant-dataset-access.py gamma_user cleaned_sales_data \\
        --superset-url http://10.223.71.200:30188

Prerequisites:
    Superset admin credentials (defaults: admin / admin123)
"""

import argparse
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

SUPERSET_ADMIN_USER = "admin"


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
    _, d = api("POST", f"{base}/api/v1/security/login",
               {"username": SUPERSET_ADMIN_USER, "password": password, "provider": "db"})
    api_token = d.get("access_token")
    if not api_token:
        sys.exit(f"[ERROR] Superset login failed: {d}")

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    resp = opener.open(f"{base}/login/")
    html = resp.read().decode()
    m = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', html) or \
        re.search(r'value=["\']([^"\']+)["\'][^>]*name=["\']csrf_token["\']', html)
    form_csrf = m.group(1) if m else ""

    form_data = urllib.parse.urlencode({
        "username": SUPERSET_ADMIN_USER,
        "password": password,
        "csrf_token": form_csrf,
    }).encode()
    opener.open(f"{base}/login/", form_data)

    req = urllib.request.Request(
        f"{base}/api/v1/security/csrf_token/",
        headers={"Authorization": f"Bearer {api_token}"},
    )
    with opener.open(req) as resp:
        csrf_token = json.loads(resp.read()).get("result", "")

    return api_token, csrf_token, opener


def get_dataset_perm(base: str, api_token: str, table_name: str) -> str:
    """Returns the "[<database>].[<table>](id:<id>)" perm string Superset uses as a
    view_menu name. The dataset list/detail endpoints don't expose "perm" directly
    (only e.g. GET /dashboard/{id}/datasets does), so it's built the same way
    SqlaTable.get_perm() does, from database_name + table_name + id."""
    auth = {"Authorization": f"Bearer {api_token}"}
    _, d = api("GET", f"{base}/api/v1/dataset/?q=(page_size:100)", headers=auth)
    for ds in d.get("result", []):
        if ds["table_name"] == table_name:
            _, detail = api("GET", f"{base}/api/v1/dataset/{ds['id']}", headers=auth)
            r = detail["result"]
            return f"[{r['database']['database_name']}].[{r['table_name']}](id:{r['id']})"
    sys.exit(f"[ERROR] Dataset '{table_name}' not found.")


def find_datasource_access_perm_id(base: str, api_token: str, dataset_perm: str) -> int:
    auth = {"Authorization": f"Bearer {api_token}"}
    page = 0
    while True:
        _, d = api("GET", f"{base}/api/v1/security/permissions-resources/?q=(page:{page},page_size:100)",
                   headers=auth)
        results = d.get("result", [])
        if not results:
            break
        for r in results:
            if r["permission"]["name"] == "datasource_access" and r["view_menu"]["name"] == dataset_perm:
                return r["id"]
        page += 1
    sys.exit(f"[ERROR] No datasource_access permission found for '{dataset_perm}'. "
             "Try viewing/editing the dataset once as admin so Superset registers it.")


def ensure_role(base: str, api_token: str, name: str) -> int:
    auth = {"Authorization": f"Bearer {api_token}"}
    _, d = api("GET", f"{base}/api/v1/security/roles/?q=(page_size:100)", headers=auth)
    for r in d.get("result", []):
        if r["name"] == name:
            return r["id"]
    _, d = api("POST", f"{base}/api/v1/security/roles/", {"name": name}, headers=auth)
    rid = d.get("id") or d.get("result", {}).get("id")
    if not rid:
        sys.exit(f"[ERROR] Failed to create role '{name}': {d}")
    return rid


def get_user(base: str, api_token: str, username: str) -> dict:
    _, d = api("GET", f"{base}/api/v1/security/users/?q=(page_size:100)",
               headers={"Authorization": f"Bearer {api_token}"})
    for u in d.get("result", []):
        if u["username"] == username:
            return u
    sys.exit(f"[ERROR] User '{username}' not found. Run test-mcp-auth.py first to create fixtures.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("user", help="Superset username to grant dataset access to, e.g. gamma_user")
    parser.add_argument("dataset", help="Dataset table_name to grant, e.g. cleaned_sales_data")
    parser.add_argument("--role-name", help="Name for the dedicated access role "
                        "(default: '<dataset>-dataset-access')")
    parser.add_argument("--superset-url", default="http://10.223.71.200:30088")
    parser.add_argument("--superset-password", default="admin123")
    args = parser.parse_args()

    api_token, csrf_token, opener = superset_login(args.superset_url, args.superset_password)

    dataset_perm = get_dataset_perm(args.superset_url, api_token, args.dataset)
    perm_id = find_datasource_access_perm_id(args.superset_url, api_token, dataset_perm)

    role_name = args.role_name or f"{args.dataset}-dataset-access"
    role_id = ensure_role(args.superset_url, api_token, role_name)

    status, d = session_api(opener, "POST", f"{args.superset_url}/api/v1/security/roles/{role_id}/permissions",
                            {"permission_view_menu_ids": [perm_id]},
                            extra_headers={"Authorization": f"Bearer {api_token}",
                                           "X-CSRFToken": csrf_token, "Referer": args.superset_url})
    if status != 200:
        sys.exit(f"[ERROR] Failed to attach permission to role '{role_name}': {status} {d}")

    user = get_user(args.superset_url, api_token, args.user)
    role_ids = [r["id"] for r in user["roles"]]
    if role_id not in role_ids:
        role_ids.append(role_id)
    status, d = api("PUT", f"{args.superset_url}/api/v1/security/users/{user['id']}", {
        "username": user["username"], "first_name": user["first_name"], "last_name": user["last_name"],
        "email": user["email"], "active": True, "roles": role_ids,
    }, headers={"Authorization": f"Bearer {api_token}"})
    if status != 200:
        sys.exit(f"[ERROR] Failed to assign role to '{args.user}': {status} {d}")

    print(f"granted '{args.user}' datasource_access on '{args.dataset}' "
         f"via role '{role_name}' (role id={role_id})")


if __name__ == "__main__":
    main()
