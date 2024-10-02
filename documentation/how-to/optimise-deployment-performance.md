This guide describes steps you can take to optimise your Superset deployment performance.

## Enable asynchronous querying

To enable asynchronous querying, you need to deploy Charmed Superset Workers. These workers handle long-running queries asynchronously, allowing the User Interface (UI) to remain responsive. You can do this as follows:

```bash
juju deploy superset-k8s --config charm-function=worker superset-k8s-worker
```

To be functional, the Charmed Superset Worker requires relations with the same PostgreSQL and Redis charms as the server. These can be added as below:

```bash
juju relate superset-k8s-worker postgresql-k8s
juju relate superset-k8s-worker redis-k8s
```

Using the UI, you can enable Asynchronous Query Execution (AQE) at the database level.

To do so, edit the database. Under `Performance`, check the `Asynchronous query execution` box.

[note]

This is recommended for all production databases to relieve load on the UI.

[/note]

## Enable beat scheduling

Superset’s scheduling system relies on a single instance of the [beat scheduler](https://superset.apache.org/docs/configuration/alerts-reports/). This scheduler handles periodic jobs like caching or data refreshes. Only one instance should be deployed to avoid conflicting schedules. This can be deployed as follows:

```bash
juju deploy superset-k8s --config charm-function=beat superset-k8s-beat
```

To be functional, the Charmed Superset Beat requires relations with the same PostgreSQL and Redis charms as the server and worker(s). These can be added as below:

```bash
juju relate superset-k8s-beat postgresql-k8s
juju relate superset-k8s-beat redis-k8s
```

## Scaling applications

Charmed Superset supports independent scaling of the web server and workers. The web server and workers can be scaled horizontally to handle more load, while the beat scheduler should remain singular. 

Use the juju `scale-application` command to adjust the number of instances of each service as needed:

```bash
juju scale-application superset-k8s -n 3
```

[note]

Three units of server and worker applications for a production deployment are recommended.

[/note]
