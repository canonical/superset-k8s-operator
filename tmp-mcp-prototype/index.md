# Superset MCP Prototype — Index


## Key charm config

```bash
juju config superset-k8s mcp-enabled=true
juju config superset-k8s mcp-auth-enabled=true
juju config superset-k8s mcp-jwt-secret=<openssl rand -hex 32>
juju config superset-k8s feature-flags=RLS_IN_SQLLAB
juju config superset-k8s load-examples=true
juju config superset-k8s admin-password=admin123
juju config superset-k8s server-worker-amount=4
juju config superset-k8s superset-secret-key=<openssl rand -hex 32>
```
