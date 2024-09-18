# Feature Flags Supported by Charmed Superset

There are a number of features that can be enabled/disabled via the Superset charm:
```
juju config superset-k8s-ui <flag-name>=<True/False>
```

The below feature flags are currently supported:

| Feature Flag               | Default Value | Description                                                                 |
|----------------------------|---------------|-----------------------------------------------------------------------------|
| DASHBOARD_CROSS_FILTERS     | true          | Enables the ability to apply cross-filtering on dashboard charts.           |
| DASHBOARD_RBAC              | true          | Enables role-based access control (RBAC) for dashboards.                    |
| EMBEDDABLE_CHARTS           | true          | Allows charts to be embedded in external websites or applications.          |
| SCHEDULED_QUERIES           | true          | Enables the scheduling of queries to run at regular intervals.              |
| ESTIMATE_QUERY_COST         | true          | Provides an estimate of the query cost before execution.                    |
| ENABLE_TEMPLATE_PROCESSING  | true          | Enables processing of templates within SQL queries for dynamic generation.  |
| GLOBAL_ASYNC_QUERIES        | false         | Allows queries to run asynchronously across multiple instances globally.    |

Additional feature flags are available which are not currently implemented.
A complete list can be found [here](https://github.com/apache/superset/blob/master/RESOURCES/FEATURE_FLAGS.md), with descriptions [here](https://preset.io/blog/feature-flags-in-apache-superset-and-preset/).
