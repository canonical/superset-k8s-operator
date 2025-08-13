# Supported feature flags

Charmed Superset supports the following feature flags:

| Feature flag                | Default value | Description                                                                 |
|-----------------------------|---------------|-----------------------------------------------------------------------------|
| DASHBOARD_CROSS_FILTERS     | True          | Enables the ability to apply cross-filtering on dashboard charts.           |
| DASHBOARD_RBAC              | True          | Enables role-based access control (RBAC) for dashboards.                    |
| EMBEDDABLE_CHARTS           | True          | Allows charts to be embedded in external websites or applications.          |
| SCHEDULED_QUERIES           | True          | Enables the scheduling of queries to run at regular intervals.              |
| ESTIMATE_QUERY_COST         | True          | Provides an estimate of the query cost before execution.                    |
| ENABLE_TEMPLATE_PROCESSING  | True          | Enables processing of templates within SQL queries for dynamic generation.  |
| GLOBAL_ASYNC_QUERIES        | False         | Allows queries to run asynchronously across multiple instances globally.    |
| ALERT_REPORTS               | False         | Enables emailing reports from dashboards on a schedule.                     |

You can enable, `True`, and/or disable, `False`, these flags using the following command:
```
juju config superset-k8s-ui <flag-name>=<True/False>
```
