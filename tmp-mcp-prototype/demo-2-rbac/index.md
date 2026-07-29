
## Setting the JWT secret

Set it once (or rotate it any time):

```bash
juju config superset-k8s mcp-jwt-secret=$(openssl rand -hex 32)
```

The charm restarts the MCP service automatically.

## Running the test suite

```bash
SECRET=$(juju config superset-k8s mcp-jwt-secret)

# Against the main model (superset-model, ports 30088/30508):
python3 tmp-mcp-prototype/demo-2-rbac/test-mcp-auth.py --secret "$SECRET"

# Against a test model (e.g. superset-test, ports 30188/30608):
python3 tmp-mcp-prototype/demo-2-rbac/test-mcp-auth.py \
  --secret "$SECRET" \
  --superset-url http://10.223.71.200:30188 \
  --mcp-url http://10.223.71.200:30608
```

The script creates `gamma_user`, `sqlab_user`, `SqlLabRLS` role, and the birth_names
RLS rule automatically on first run. Safe to re-run (idempotent).
