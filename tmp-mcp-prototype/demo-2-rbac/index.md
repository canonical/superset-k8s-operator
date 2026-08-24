
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

## Generating an opencode.json for a client (e.g. gamma_user)

`mcp-configs/<user>/opencode.json` needs a signed JWT in its `Authorization` header
before an MCP client (opencode, etc.) can use it. Generate it with:

```bash
python3 tmp-mcp-prototype/demo-2-rbac/generate-mcp-config.py gamma_user
```

This reads `mcp-jwt-secret` via `juju config superset-k8s mcp-jwt-secret` (make sure
you're on the right model first — `juju switch <model>`), signs a token with the same
claims `test-mcp-auth.py` uses (`sub=<user>`, `iss=superset-k8s`, `aud=superset-mcp`),
and (re)writes `mcp-configs/<user>/opencode.json`.

Useful flags:

```bash
# against superset-model instead of the mcp-demo ports
python3 tmp-mcp-prototype/demo-2-rbac/generate-mcp-config.py admin \
  --mcp-url http://10.223.71.200:30508/mcp

# longer-lived token (default is 1h)
python3 tmp-mcp-prototype/demo-2-rbac/generate-mcp-config.py sqlab_user --expires-in 86400
```

Tokens expire (`exp` claim), so re-run the script to refresh a config once the token
in it stops working (`401` responses from the MCP server).

## Granting gamma_user dashboard access

`test-mcp-auth.py`'s fixture setup gives `gamma_user` only the built-in **Gamma**
role — no dashboards are shared with it. That's intentional (test #6 checks Gamma
gets *fewer* menus than admin), but it means an MCP client authenticated as
`gamma_user` will see "no dashboards visible" until something is explicitly shared.

To grant access:

```bash
# just one (recommended — keeps the demo narrow to what you're actually testing)
python3 tmp-mcp-prototype/demo-2-rbac/grant-dashboard-access.py gamma_user \
  --dashboard "Sales Dashboard" --superset-url http://10.223.71.200:30188

# all dashboards (omit --dashboard)
python3 tmp-mcp-prototype/demo-2-rbac/grant-dashboard-access.py gamma_user \
  --superset-url http://10.223.71.200:30188
```

This makes `gamma_user` an owner of the target dashboard(s), which is enough to make
them visible via `list_dashboards`/`get_dashboard_info`. It does **not** grant
dataset/chart access — `list_charts`/`list_datasets`/`execute_sql` will still be
empty for Gamma until you also grant `datasource_access` on the underlying dataset
(next section).

## Granting gamma_user dataset access

Even with dashboard ownership, a Gamma user can't see the underlying dataset, its
charts, or run SQL against it until a role with `datasource_access` on that specific
dataset is attached (see the "created role for dataset access" checkpoint in
`mcp-configs/gamma_user/chat.md` for what this looked like from the client side
before it existed as a script).

```bash
python3 tmp-mcp-prototype/demo-2-rbac/grant-dataset-access.py gamma_user cleaned_sales_data \
  --superset-url http://10.223.71.200:30188
```

This creates a dedicated role scoped to just that one dataset (default name
`<dataset>-dataset-access`, override with `--role-name`) rather than widening
`gamma_user`'s existing Gamma role — so other Gamma users on the instance are
unaffected. Safe to re-run (idempotent: reuses the role and skips re-adding it to
the user if already assigned).

Once both dashboard ownership and dataset access are granted, `list_dashboards`,
`list_charts`, `list_datasets`, and `execute_sql` all start returning data scoped to
what was explicitly shared — note Superset may also surface *other* dashboards that
happen to use the same now-accessible dataset, which is normal RBAC visibility, not
something either script grants directly.
