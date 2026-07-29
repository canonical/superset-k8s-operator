# MCP End-to-End Test Findings

Date: 2026-07-24 (initial) / 2026-07-28 (JWT auth + RBAC)
Branch: `mcp-prototype`
Charm rev: 15 | Rock: `superset_6.1.0-24.04-edge_amd64.rock`

---

## Test Setup

- Superset deployed in Multipass VM (`juju-sandbox-k8s`, Canonical K8s snap)
- MCP server: pebble service `mcp`, port 5008, `superset mcp run --host 0.0.0.0 --port 5008`
- Accessed via NodePort services (stable — survives pod/charm restarts):
  - UI: `VM_IP:30088` → pod port 8088
  - MCP: `VM_IP:30508` → pod port 5008
- Auth: JWT HS256 (`mcp-auth-enabled=true`, `mcp-jwt-secret=<32-byte hex>`)
- RLS in SQL Lab: enabled via `feature-flags=RLS_IN_SQLLAB`

---

## JWT Auth + RBAC (validated 2026-07-28)

Full automated test suite: `tmp-mcp-prototype/demo-2-rbac/test-mcp-auth.py`

**Self-contained** — creates all fixtures (users, roles, RLS rule) via Superset REST API
before testing. Safe to run against a fresh deployment.

```bash
python3 tmp-mcp-prototype/demo-2-rbac/test-mcp-auth.py \
  --secret <MCP_JWT_SECRET> \
  [--superset-url http://VM_IP:30088] \
  [--mcp-url http://VM_IP:30508] \
  [--superset-password admin123]
```

Results: **25/25 passed** on two deployments (existing model + fresh `superset-test` model).

### Test matrix

| # | Test | Expected | Result |
|---|------|----------|--------|
| 1 | Valid admin JWT → `get_instance_info` | HTTP 200, `current_user=admin` | ✅ |
| 2 | `health_check` (no user required) | HTTP 200, `status=healthy` | ✅ |
| 3 | Tampered token | HTTP 401 | ✅ |
| 4 | Expired token | HTTP 401 | ✅ |
| 5 | No token | HTTP 401 | ✅ |
| 6 | `gamma_user` (Gamma role) | HTTP 200, 11 menus (vs admin's 26) | ✅ |
| 7 | Unknown username in valid JWT | HTTP 200, auth error in body | ✅ |
| 8 | `sqlab_user` (Alpha + sql_lab + SqlLabRLS) | HTTP 200, all three roles present | ✅ |
| 9 | `sqlab_user` `execute_sql birth_names` | Only `gender='boy'` rows (RLS active) | ✅ |
| 10 | `admin` `execute_sql birth_names` | Both `boy` and `girl` rows (no RLS) | ✅ |

### Fixture setup (created by test script)

| Fixture | Details |
|---------|---------|
| `gamma_user` | Gamma role (public objects only, no SQL Lab) |
| `SqlLabRLS` role | Marker role — scopes the RLS rule, no extra permissions |
| `sqlab_user` | Alpha + sql_lab + SqlLabRLS roles |
| RLS rule | `birth_names`: `gender = 'boy'` for SqlLabRLS role |

### Charm config required

```bash
juju config superset-k8s mcp-auth-enabled=true
juju config superset-k8s mcp-jwt-secret=<openssl rand -hex 32>
juju config superset-k8s feature-flags=RLS_IN_SQLLAB
```

### Implementation notes

- JWT validation (401 for bad/expired tokens) is handled by FastMCP's `JWTVerifier`
  configured via `MCP_AUTH_ENABLED=True` + `MCP_JWT_ALGORITHMS=HS256` + `MCP_JWT_SECRET`
  in the MCP pebble service environment.
- Superset 6.1.0 has a gap: `MCP_USER_RESOLVER` is documented but not wired in
  `auth.py`. `get_user_from_request()` only checks `g.user` (set by middleware) or
  `MCP_DEV_USERNAME`, never calling `get_access_token()`.
- **Fix**: monkey-patch `get_user_from_request` in `superset_config.py` (lines ~564–636)
  to call `fastmcp.server.dependencies.get_access_token()` and load the user from
  the JWT `claims["sub"]` field. This runs at config-load time, before tool decoration,
  so the patched function is captured in each tool wrapper's merged globals.
- `get_access_token()` returns an `AccessToken` object; the username is in
  `token.claims["sub"]` (not `token.subject` which is always `None` in this version).
- `RLS_IN_SQLLAB` must be enabled for RLS to filter `execute_sql` queries; by default
  Superset only applies RLS to chart/explore queries, not SQL Lab.

---

## Fresh deployment procedure (superset-test model, 2026-07-28)

Issues encountered and required steps not in the original notes:

### `superset-secret-key` is required
The charm blocks on `missing required config: superset-secret-key`.
Must be set at deploy time or immediately after:

```bash
juju config superset-k8s superset-secret-key=$(openssl rand -hex 32)
```

**Add this to the deploy command** (the original notes omit it).

### `postgresql-k8s` requires cluster trust

```bash
juju trust postgresql-k8s --scope=cluster
```

This was already in the original notes. Required immediately after deploy.

### `juju add-model` requires credential owned by `admin`

`hostuser` (the juju user on the host) cannot add models — the k8s credential is
owned by `admin` on the VM controller. Use:

```bash
multipass exec juju-sandbox-k8s -- juju add-model <model-name>
multipass exec juju-sandbox-k8s -- juju grant hostuser admin <model-name>
```

### NodePort services use hardcoded NodePort numbers

Each model needs different NodePort values to avoid conflicts:

| Model | UI NodePort | MCP NodePort |
|-------|-------------|--------------|
| `superset-model` | 30088 | 30508 |
| `superset-test` | 30188 | 30608 |

Apply with `kubectl apply -f` after model is active. See `superset-nodeport.yaml`
for the template (update namespace and nodePort values per model).

---

## What Works ✅

| Tool | Result |
|------|--------|
| `initialize` (MCP handshake) | Full tool/resource/prompt manifest returned correctly |
| `get_instance_info` | Returns current user, roles, instance stats |
| `health_check` | Returns healthy status |
| `execute_sql` | Runs SQL queries; RLS filters apply when `RLS_IN_SQLLAB=true` |
| `search_tools` | Discovers all available tools via meta-tool |
| `list_datasets` | Returns dataset list (see caveat below) |
| `get_dataset_info` | Returns columns and metrics for a given dataset |
| `list_charts` | Returns chart list with viz types and IDs |
| `get_chart_info` | Returns full chart config |
| `generate_dashboard` | Creates dashboard from existing chart IDs ✅ |

---

## What Doesn't Work ❌

### `generate_chart` — Internal Server Error
All `generate_chart` calls return:
```
Internal error in generate_chart: An unexpected error occurred.
```

**Root cause unknown** — likely a server-side issue. Needs investigation.

### `generate_explore_link` — Same Internal Error
Same error pattern as `generate_chart`.

---

## Caveats / Unexpected Behaviour ⚠️

### `list_datasets` response too large
When datasets have many columns, the full response can be ~27KB. Workaround: use
`get_dataset_info` on a specific ID.

### `get_dataset_info` token limit
`wb_health_population` (id:21) failed with a 30K-token limit error.

### Dashboard title is auto-generated
`generate_dashboard` ignores the `dashboard_title` parameter.

### Base URL in dashboard URL is wrong
Returned URLs use `http://0.0.0.0:8080` instead of the external address.
Configure `SUPERSET_WEBSERVER_BASE_URL` to fix.

### `kubectl port-forward` is not stable
Use NodePort services instead (see above).

---

## Sub-agent behaviour observations

- The general-purpose agent correctly used native MCP tools without needing bash/curl
  when prompted correctly
- First attempt failed because the agent's session didn't load the MCP config
  (`.vscode/mcp.json` must be present at session start in the working directory)
- Second attempt succeeded — agent autonomously recovered from `generate_chart`
  failures by falling back to `list_charts` to find pre-existing charts, then
  calling `generate_dashboard`
- Agent correctly identified dataset schema, picked appropriate chart types, and
  mapped columns to chart configs


---

## Test Setup

- Superset deployed in Multipass VM (`juju-sandbox-k8s`, Canonical K8s snap)
- MCP server: pebble service `mcp`, port 5008, `superset mcp run --host 0.0.0.0 --port 5008`
- Auth: `mcp-auth-enabled=false`, `mcp-dev-username=admin`
- Accessed via `kubectl port-forward pod/superset-k8s-0 8088:8088 5008:5008`
- Agent used: GitHub Copilot sub-agent (general-purpose) calling native MCP tools

---

## What Works ✅

| Tool | Result |
|------|--------|
| `initialize` (MCP handshake) | Full tool/resource/prompt manifest returned correctly |
| `get_instance_info` | Returns current user, roles, instance stats |
| `list_datasets` | Returns dataset list (see caveat below) |
| `get_dataset_info` | Returns columns and metrics for a given dataset |
| `list_charts` | Returns chart list with viz types and IDs |
| `get_chart_info` | Returns full chart config |
| `generate_dashboard` | Creates dashboard from existing chart IDs ✅ |
| `health_check` | Returns healthy status |

Dashboard created: id=10, `http://localhost:8088/superset/dashboard/10/`
Charts used: id 94 (big_number), 103 (bar), 99 (line) — all from `birth_names` dataset

---

## What Doesn't Work ❌

### `generate_chart` — Internal Server Error
All `generate_chart` calls return:
```
Internal error in generate_chart: An unexpected error occurred.
```
Tried all variants:
- `chart_type: "big_number"` with `metric: {aggregate: "COUNT", name: "count"}`
- `chart_type: "xy", kind: "bar"` with groupby/metric
- `save_chart: false` (no-save mode)
- Parameters verified correct via `get_chart_type_schema`

**Root cause unknown** — likely a server-side issue (async worker, missing config, or
Superset API call inside the MCP tool failing silently). Needs investigation in the
`superset-mcp` Python package.

### `generate_explore_link` — Same Internal Error
Same error pattern as `generate_chart`. Both tools likely share the same broken
underlying Superset API call.

---

## Caveats / Unexpected Behaviour ⚠️

### `list_datasets` response too large
When datasets have many columns, the full response can be ~27KB. The MCP framework
offloads this to a temp file instead of returning inline. In an MCP-only workflow
(no shell access) this means the data is inaccessible. Workaround: use `get_dataset_info`
on a specific ID instead of scanning the list.

### `get_dataset_info` token limit
`wb_health_population` (id:21) failed with a 30K-token limit error — it has an
extremely large number of columns/metrics. Consider adding pagination or column
filtering to this tool.

### Dashboard title is auto-generated
`generate_dashboard` ignores the `dashboard_title` parameter and auto-generates a
title from the chart names. The requested title `"Data Insights Dashboard"` was
replaced with `"Participants, Trends, Genders by State"`. This is a bug or
undocumented behaviour in the MCP tool.

### Base URL in dashboard URL is wrong
The returned dashboard URL uses `http://0.0.0.0:8080` (the Superset process bind
address), not the external URL. In the charm, `SUPERSET_WEBSERVER_BASE_URL` should
be configured to produce correct URLs. Until then, manually replace with
`http://localhost:8088`.

### `kubectl port-forward` is not stable
Port-forward dies on pod restart (e.g. after charm refresh). Must be restarted
manually. Future work: expose Superset via NodePort or `juju expose` + ingress.
The service (`superset-k8s` ClusterIP) doesn't expose ports 8088/5008 directly —
must port-forward to the pod, not the service.

---

## Issues to Fix

1. **`generate_chart` / `generate_explore_link` broken** — investigate the upstream
   `superset-mcp` package or the Superset API endpoint it calls
2. **`MCP_DEV_USERNAME` not in `superset_config.py`** — was missing from the template,
   added in charm rev 6 (see `templates/superset_config.py`)
3. **`mcp_dev_username` not in `structured_config.py`** — caused `AttributeError` on
   config-changed hook, fixed in charm rev 5
4. **Dashboard title override** — report upstream or handle in the MCP tool
5. **UI ChunkLoadError** — `ChunkLoadError: Loading chunk 7391 failed` on dashboard
   render. The chunk file exists in the container but the port-forward times out when
   serving large static assets. Likely due to `kubectl port-forward` bandwidth
   limitations or the port-forward dying mid-transfer. Fix: use a more stable access
   method (NodePort, ingress, or `multipass exec` browser).

---

## Sub-agent behaviour observations

- The general-purpose agent correctly used native MCP tools without needing bash/curl
  when prompted correctly
- First attempt failed because the agent's session didn't load the MCP config
  (`.vscode/mcp.json` must be present at session start in the working directory)
- Second attempt succeeded — agent autonomously recovered from `generate_chart`
  failures by falling back to `list_charts` to find pre-existing charts, then
  calling `generate_dashboard`
- Agent correctly identified dataset schema, picked appropriate chart types, and
  mapped columns to chart configs
