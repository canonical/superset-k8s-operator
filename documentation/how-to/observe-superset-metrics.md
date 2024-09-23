
## Integrate with Canonical Observability Stack
For observability, Superset can be integrated with [Canonical Observability Stack](https://charmhub.io/topics/canonical-observability-stack). This integration allows you to monitor metrics, logs, and events from Superset, enabling proactive health checks and performance analysis. Prometheus, Grafana and Loki are included as part of this observability suite.

To deploy `cos-lite` and expose its endpoints as offers, follow these steps:

Firstly, create a new model and deploy the `cos-lite` bundle:
```bash
juju add-model cos
juju deploy cos-lite --trust
```

Next we can expose the cos integration endpoints as [Juju offers](https://juju.is/docs/juju/manage-offers).
```bash
juju offer prometheus:metrics-endpoint
juju offer loki:logging
juju offer grafana:grafana-dashboard
```
We then then relate our Superset application with these endpoints.
```bash
juju relate superset-k8s admin/cos.grafana
juju relate superset-k8s admin/cos.loki
juju relate superset-k8s admin/cos.prometheus
```
Now we should have a working observability setup. You'll need the admin password and grafana application IP to login to the UI. Which you can retrieve as follows:

```bash
juju run grafana/0 -m cos get-admin-password --wait 1m
juju status
```
The Grafana UI can be found on the application IP address, port 3000. Pre-built dashboards are included under `Superset Metrics`.
