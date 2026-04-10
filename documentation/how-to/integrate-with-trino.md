# Trino Integration

This guide describes how to connect Superset to [Trino](https://charmhub.io/trino-k8s), a distributed SQL query engine, enabling users to explore and visualize data from multiple heterogeneous data sources through Superset dashboards.

The integration uses the `trino-catalog` relation interface, which automatically creates and maintains Superset database connections for each Trino catalog. When catalogs are added or updated in Trino, they become available in Superset without manual configuration.

## Prerequisites

1. Superset is deployed as `charm-function=app-gunicorn` (the default)
2. Trino is deployed as `charm-function=coordinator` (or `all`) and configured with:
   - `external-hostname` if Trino is behind an ingress with TLS
   - `catalog-config` with at least one catalog

```bash
juju deploy superset-k8s

juju deploy trino-k8s --config charm-function=all --trust
```

## Establish the relation

Create the relation between Trino and Superset:

```bash
juju relate trino-k8s:trino-catalog superset-k8s:trino-catalog
```

## Verify the integration

Trino automatically creates per-relation credentials and grants them to Superset. No manual secret creation or granting is required.

After establishing the relation, Superset will automatically create database connections for each configured Trino catalog.

You can verify the integration by:

1. Logging into the Superset UI
2. Navigating to **Settings**/**Database Connections**
3. Checking for databases named after your Trino catalogs (e.g., "Pgsql (pgsql)", "Mysql (mysql)")

Each database connection will be automatically configured with:
- The correct Trino connection URL
- Authentication credentials
- Permissions granted to the "Public" role

## Add or update Trino catalogs

When you add or modify catalogs in Trino's `catalog-config`, Superset will automatically detect these changes and create corresponding database connections.

## Credential rotation

Trino manages per-relation credentials automatically. If the credentials are rotated by Trino, Superset will update all affected database connections during the next `secret-changed` event.

## Understanding connection behavior

The Trino integration follows a partial reconciliation model:

- **New catalogs**: Automatically create Superset database connections
- **Existing catalogs**: Update URL and credentials only; other settings are preserved
- **Removed catalogs**: Database connections remain in Superset for manual cleanup
- **Broken relation**: Database connections persist after relation removal

This design ensures that successive admin customizations are not overwritten during updates.

## Cross-model relations

If Trino and Superset are deployed in different Juju models, you can use cross-model relations:

First, create an offer from the Trino model:

```bash
juju offer trino-k8s:trino-catalog
```

Then, from the Superset model, consume the offer:

```bash
juju consume <controller>:admin/trino-model.trino-catalog
```

## Troubleshooting

### Database connections not appearing

If Superset database connections are not created:

1. Verify the relation exists:
   ```bash
   juju status --relations
   ```

2. Check Trino logs:
   ```bash
   juju debug-log --include trino-k8s
   ```

3. Check Superset logs:
   ```bash
   juju debug-log --include superset-k8s
   ```

### Trino pods restarting

When you update Trino's `catalog-config`, the pods restart to apply changes. During this period:

- Existing Superset queries to Trino will fail
- New database connection creation may be delayed
- Superset automatically retries until reconciliation is complete
