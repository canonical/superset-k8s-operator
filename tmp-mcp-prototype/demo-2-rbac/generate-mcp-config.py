# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

#!/usr/bin/env python3
"""
Generate an opencode.json MCP config with a signed JWT for a given Superset user.

The JWT uses the same claims (sub/iss/aud/iat/exp) as make_token() in
test-mcp-auth.py, signed HS256 with the mcp-jwt-secret charm config.

Usage:
    python3 generate-mcp-config.py gamma_user
    python3 generate-mcp-config.py sqlab_user --expires-in 86400
    python3 generate-mcp-config.py admin \\
        --juju-app superset-k8s --mcp-url http://10.223.71.200:30608/mcp

Prerequisites:
    pip install pyjwt
    juju switch <model with superset-k8s deployed>
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

try:
    import jwt
except ImportError:
    sys.exit("pip install PyJWT")


ISSUER = "superset-k8s"
AUDIENCE = "superset-mcp"


def get_secret(juju_app: str) -> str:
    result = subprocess.run(
        ["juju", "config", juju_app, "mcp-jwt-secret"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"[ERROR] `juju config {juju_app} mcp-jwt-secret` failed: {result.stderr.strip()}")
    secret = result.stdout.strip()
    if not secret:
        sys.exit(f"[ERROR] mcp-jwt-secret is empty for app '{juju_app}'")
    return secret


def make_token(secret: str, sub: str, expires_in: int) -> str:
    now = int(time.time())
    payload = {"sub": sub, "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + expires_in}
    return jwt.encode(payload, secret, algorithm="HS256")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("user", help="Superset username to embed as the JWT 'sub' claim, e.g. gamma_user")
    parser.add_argument("--secret", help="mcp-jwt-secret value; if omitted, read via `juju config`")
    parser.add_argument("--juju-app", default="superset-k8s", help="Juju application name (default: superset-k8s)")
    parser.add_argument("--mcp-url", default="http://10.223.71.200:30608/mcp", help="MCP endpoint URL")
    parser.add_argument("--expires-in", type=int, default=3600, help="Token lifetime in seconds (default: 3600)")
    parser.add_argument("--out-dir", help="Output directory (default: mcp-configs/<user>/ next to this script)")
    args = parser.parse_args()

    secret = args.secret or get_secret(args.juju_app)
    token = make_token(secret, args.user, args.expires_in)

    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).parent / "mcp-configs" / args.user
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "opencode.json"

    config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "superset": {
                "type": "remote",
                "url": args.mcp_url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        },
    }
    out_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"wrote {out_path}")
    print(f"sub={args.user} expires_in={args.expires_in}s")


if __name__ == "__main__":
    main()
