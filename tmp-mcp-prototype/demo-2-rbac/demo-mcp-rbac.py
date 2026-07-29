# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

#!/usr/bin/env python3
"""demo-mcp-rbac.py — Launch opencode with Superset MCP to demonstrate RBAC enforcement.

Usage:
    python3 demo-mcp-rbac.py --secret <MCP_JWT_SECRET> [--user admin|alpha_user|gamma_user]

What it does:
1. Mints an HS256 JWT for the chosen Superset user.
2. Writes a temporary opencode.json wiring up the Superset MCP server with that token.
3. Launches opencode with deepseek-v4-flash-free and a prompt that exercises MCP tools.

This demonstrates that RBAC enforcement is real: what the AI can see and do through
MCP is bounded by the Superset role of the user whose token is used.

Expected behaviour by role:
  admin       — sees all charts, dashboards, datasets; can run arbitrary SQL
  alpha_user  — sees all charts and datasets; execute_sql succeeds (Alpha has SQL Lab)
  gamma_user  — sees only public objects; private charts/dashboards return empty results

Prerequisites:
    pip install pyjwt
    Users must exist in Superset (run test-mcp-auth.py first to create them).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

import jwt

MCP_URL = "http://10.223.71.200:30508/mcp"
ISSUER = "superset-k8s"
AUDIENCE = "superset-mcp"
TOKEN_TTL_SECONDS = 3600
MODEL = "opencode/deepseek-v4-flash-free"

PROMPTS = {
    "admin": """\
You are connected to a Superset instance via MCP. You have admin access.
Please do the following:
1. Run health_check to confirm connectivity.
2. List the first 3 charts and show their names and IDs.
3. Execute the SQL query "SELECT COUNT(*) as total_rows FROM birth_names" on database ID 1.
4. Summarise what you found.
""",

    "alpha_user": """\
You are connected to a Superset instance via MCP. You have Alpha (read + SQL Lab) access.
Please do the following:
1. Run health_check to confirm connectivity.
2. List the first 3 datasets and show their names.
3. Execute the SQL query "SELECT name, sum(num) as total FROM birth_names GROUP BY name ORDER BY total DESC LIMIT 5" on database ID 1.
4. Summarise what you found.
""",

    "gamma_user": """\
You are connected to a Superset instance via MCP. You have Gamma (public objects only) access.
Please do the following:
1. Run health_check to confirm connectivity.
2. List the first 3 dashboards — note if any are restricted or the list is empty.
3. Attempt to execute the SQL query "SELECT 1" on database ID 1, and report whether it succeeded or was denied.
4. Summarise what RBAC allowed and what it restricted.
""",
}


def make_token(username: str, secret: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": username,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + TOKEN_TTL_SECONDS,
        },
        secret,
        algorithm="HS256",
    )


def build_opencode_config(token: str) -> dict:
    return {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "superset": {
                "type": "remote",
                "url": MCP_URL,
                "headers": {
                    "Authorization": f"Bearer {token}",
                },
                "timeout": 30000,
            }
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Demo MCP RBAC with opencode + deepseek")
    parser.add_argument("--secret", required=True, help="MCP_JWT_SECRET (HS256 shared secret)")
    parser.add_argument(
        "--user",
        default="admin",
        choices=["admin", "alpha_user", "gamma_user"],
        help="Superset user to authenticate as (default: admin)",
    )
    parser.add_argument("--mcp-url", default=MCP_URL, help="MCP endpoint URL")
    args = parser.parse_args()

    try:
        import jwt  # noqa: F401
    except ImportError:
        print("Missing dependency: pip install pyjwt")
        sys.exit(1)

    token = make_token(args.user, args.secret)
    config = build_opencode_config(token)
    config["mcp"]["superset"]["url"] = args.mcp_url
    prompt = PROMPTS[args.user]

    # Write a temporary opencode.json in a temp dir; run opencode from there
    # so it picks up the MCP config without polluting the repo.
    with tempfile.TemporaryDirectory(prefix="mcp-rbac-demo-") as tmpdir:
        config_path = os.path.join(tmpdir, "opencode.json")
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        print(f"=== Superset MCP RBAC Demo ===")
        print(f"User:  {args.user}")
        print(f"Model: {MODEL}")
        print(f"MCP:   {args.mcp_url}")
        print(f"Token: {token[:40]}...")
        print(f"Config written to: {config_path}")
        print()
        print("--- Prompt ---")
        print(prompt.strip())
        print("--------------")
        print()

        cmd = [
            "opencode", "run",
            "--model", MODEL,
            "--dir", tmpdir,
            prompt,
        ]
        print(f"Running: {' '.join(cmd[:4])} ...")
        print()

        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"\n[ERROR] opencode exited with code {e.returncode}")
            sys.exit(e.returncode)
        except FileNotFoundError:
            print("\n[ERROR] opencode not found. Install it or ensure it is on your PATH.")
            sys.exit(1)


if __name__ == "__main__":
    main()
