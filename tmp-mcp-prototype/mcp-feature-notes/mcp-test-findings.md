# MCP End-to-End Test Findings

Branch: `mcp-prototype` | Charm rev: 15 | Rock: `superset_6.1.0-24.04-edge_amd64.rock`

## Setup

- Superset in Multipass VM (`juju-sandbox-k8s`, Canonical K8s snap)
- MCP server: pebble service `mcp`, port 5008
- Access via NodePort (stable across restarts): UI `VM_IP:30088`, MCP `VM_IP:30508`
- Auth: JWT HS256 (`mcp-auth-enabled=true`, `mcp-jwt-secret=<32-byte hex>`, `feature-flags=RLS_IN_SQLLAB`)

## JWT Auth + RBAC

Test suite: `tmp-mcp-prototype/demo-2-rbac/test-mcp-auth.py` — **25/25 passed** on two deployments.

```bash
python3 tmp-mcp-prototype/demo-2-rbac/test-mcp-auth.py \
  --secret <MCP_JWT_SECRET> [--superset-url ...] [--mcp-url ...] [--superset-password admin123]
```

Self-contained: creates all fixtures (users, roles, RLS rule) before testing.

| Test | Result |
|------|--------|
| Valid admin JWT | ✅ HTTP 200, `current_user=admin` |
| Tampered / expired / missing token | ✅ HTTP 401 |
| `gamma_user` (Gamma role) | ✅ 11 menus vs admin's 26 |
| `sqlab_user` with RLS | ✅ `execute_sql` returns only `gender='boy'` rows |
| `admin` with no RLS | ✅ both `boy` and `girl` rows |

**Charm config:**
```bash
juju config superset-k8s mcp-auth-enabled=true mcp-jwt-secret=$(openssl rand -hex 32) feature-flags=RLS_IN_SQLLAB
```

**Implementation note:** Superset 6.1.0 doesn't wire `MCP_USER_RESOLVER` — `get_user_from_request()` never calls `get_access_token()`. Fix: monkey-patch it in `superset_config.py` to call `fastmcp.server.dependencies.get_access_token()` and resolve the user from `token.claims["sub"]`.

## Fresh Deployment Notes

- `superset-secret-key` is required (charm blocks without it): `juju config superset-k8s superset-secret-key=$(openssl rand -hex 32)`
- `juju trust postgresql-k8s --scope=cluster` required immediately after deploy
- Models must be added from the VM controller (`multipass exec juju-sandbox-k8s -- juju add-model ...`)
- Each model needs distinct NodePort values (`superset-model`: 30088/30508, `superset-test`: 30188/30608)

## Tool Status

| Tool | Status |
|------|--------|
| `initialize`, `health_check`, `get_instance_info` | ✅ |
| `execute_sql` (with RLS) | ✅ |
| `list_datasets`, `get_dataset_info`, `search_tools` | ✅ |
| `list_charts`, `get_chart_info` | ✅ |
| `generate_dashboard` | ✅ |
| `generate_chart`, `generate_explore_link` | ❌ Internal Server Error — root cause unknown, needs investigation in `superset-mcp` |

## Caveats

- **`list_datasets` response size**: can be ~27KB; use `get_dataset_info` on a specific ID instead
- **`get_dataset_info` token limit**: large datasets (e.g. `wb_health_population` id:21) hit 30K-token limit
- **`generate_dashboard` ignores `dashboard_title`**: auto-generates title from chart names
- **Dashboard URLs use wrong base**: returns `http://0.0.0.0:8080`; set `SUPERSET_WEBSERVER_BASE_URL` to fix

## Sub-agent Observations

- Agent used MCP tools natively without bash/curl when prompted correctly
- First attempt failed — `.vscode/mcp.json` must exist in the working directory at session start
- Second attempt succeeded — agent recovered from `generate_chart` failures by using `list_charts` + `generate_dashboard`
