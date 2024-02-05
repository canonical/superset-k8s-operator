# superset-k8s-charm
Apache Superset™ is an open-source modern data exploration and visualization platform.
Superset is fast, lightweight, intuitive, and loaded with options that make it easy for users of all skill sets to explore and visualize their data.

## Superset UI, worker and beat deployment
The same Superset charm can act as either a web server, worker or beat-scheduler. This is determined by the configuration parameter `charm-function`, which can be any of `app-gunicorn` (web), `app` (web development), `worker` or `beat`. Each Superset application must be related to the same PostgreSQL and Redis clusters for communication. The default value of `charm-function` is `app-gunicorn`.

```
# deploy the web server
juju deploy superset-k8s superset-k8s-ui

# deploy a worker
juju deploy superset-k8s --config charm-function=worker superset-k8s-worker

# deploy the beat scheduler
juju deploy superset-k8s --config charm-function=beat superset-k8s-beat
```
Note: while there can be multiple workers or web servers, there should only ever be 1 Superset beat deployment.

## Relations
The following relations are required to start the Superset application.

### Redis
Redis acts as both a cache and message broker for Superset services. It's a requirement to have a redis relation in order to start the Superset application.
```
# deploy redis charm
juju deploy redis-k8s --edge

# relate redis charm
juju relate redis-k8s superset-k8s
```
### PostgreSQL
PostgreSQL is used as the database that stores Superset metadata (slices, connections, tables, dashboards etc.). It's a requirement to have a PostgreSQL relation to start the Superset application.
```
# deploy postgresql charm
juju deploy postgresql-k8s --channel 14/stable

# relate postgresql charm
juju relate postgresql-k8s superset-k8s
```

## Authentication
Username/password authentication is enabled by default using the `admin` user and the password set via the Superset configuration value `admin-password`.

To enable Google Oauth, you will need a Google project, you can create one [here](https://console.cloud.google.com/projectcreate).

### Obtain Oauth2 credentials
If you do not already have Oauth2 credentials set up then follow the steps below:
1. Navigate to https://console.cloud.google.com/apis/credentials 
2. Click `+ Create Credentials` 
3. Select `Oauth client ID`
4. Select application type (Web application)
5. Name the application
6. Add an Authorized redirect URI (`https://<host>:8088/oauth-authorized/google`)
7. Create and download your client ID and client secret

## Apply oauth configuration to Superset charm
Create a file `oauth.yaml` using the Oauth2 credentials you obtained from Google, as follows:
```
superset-k8s:
  google-client-id: "Your client id here"
  google-client-secret: "Your client secret here"
  oauth-domain: "yourcompanydomain.com"
  oauth-admin-email: "youruser@yourcompanydomain.com"

```
If you have given your charm deployment an alias this will replace `superset-k8s` in this file.

Note: please ensure that the email provided by `oauth-admin-email` is one you can authenticate with Google, as this will be your `Admin` account. Additionally ensure that this email domain matches the `oauth-domain` provided.

Apply these credentials to the Superset charm:
```
juju config superset-k8s --file=path/to/oauth.yaml
```

### Self-registration and roles
By default, with Google Oauth, a Superset user account is automatically created following successful authentication. This user is provided the least privileged role of `Public`, this role can then be elevated in the UI or via API by an `Admin` user. 

To change the role that is applied on self-registration, simply pass the role via the configuration parameter `self-registration-role`. Superset's standard roles and their associated permissions can be found [here](https://github.com/apache/superset/blob/master/RESOURCES/STANDARD_ROLES.md).


### Ingress
The Superset operator exposes its ports using the Nginx Ingress Integrator operator. You must first make sure to have an Nginx Ingress Controller deployed. To enable TLS connections, you must have a TLS certificate stored as a k8s secret (default name is "superset-tls"). A self-signed certificate for development purposes can be created as follows:

```
# Generate private key
openssl genrsa -out server.key 2048

# Generate a certificate signing request
openssl req -new -key server.key -out server.csr -subj "/CN=superset-k8s"

# Create self-signed certificate
openssl x509 -req -days 365 -in server.csr -signkey server.key -out server.crt -extfile <(printf "subjectAltName=DNS:superset-k8s")

# Create a k8s secret
kubectl create secret tls superset-tls --cert=server.crt --key=server.key
```
This operator can then be deployed and connected to the Superset operator using the Juju command line as follows:

```
# Deploy ingress controller.
microk8s enable ingress:default-ssl-certificate=superset-k8s/superset-tls

juju deploy nginx-ingress-integrator --channel edge --revision 71
juju relate superset-k8s nginx-ingress-integrator
```

Once deployed, the hostname will default to the name of the application (superset-k8s), and can be configured using the external-hostname configuration on the Superset operator.

## Backup and restore
### Setting up storage
Apache Superset is a stateless application, all of the metadata is stored in the PostgreSQL relation. Therefore backup and restore is achieved through backup and restoration of this data. A requirement for this is an [AWS S3 bucket](https://aws.amazon.com/s3/) for use with the [S3 integrator charm](https://charmhub.io/s3-integrator).

```
# Deploy the s3-integrator charm
juju deploy s3-integrator

# Provide S3 credentials
juju run s3-integrator/leader sync-s3-credentials access-key=<your_key> secret-key=<your_secret_key>

# Configure the s3-integrator
juju config s3-integrator \
    endpoint="https://s3.eu-west-2.amazonaws.com" \
    bucket="superset-backup-bucket-1" \
    path="/superset-backup" \
    region="eu-west-2"

# Relate postgres
juju relate s3-integratior postgresql-k8s
```

More details and configuration values can be found in the [documentation for the PostgreSQL K8s charm](https://charmhub.io/postgresql-k8s/docs/h-configure-s3-aws)

### Create and list backups
```
# Create a backup
juju run postgresql-k8s/leader create-backup --wait 5m
# List backups
juju run postgresql-k8s/leader list-backups
```
More details found [here](https://charmhub.io/postgresql-k8s/docs/h-create-and-list-backups).

### Restore a backup
```
# Check available backups
juju run postgresql-k8s/leader list-backups
# Restore backup by ID
juju run postgresql-k8s/leader restore backup-id=YYYY-MM-DDTHH:MM:SSZ --wait 5m
```
More details found [here](https://charmhub.io/postgresql-k8s/docs/h-restore-backup).

## Scaling
The Superset charm consists of 3 services deployed as separate applications (below), which scale independently.
- Server (`app-gunicorn`): Web server.
- Worker (`worker`): for execution of jobs.
- Beat (`beat`): for job scheduling.
Communication between these applications is via Redis which acts as a message broker. Due to the nature of the beat scheduler, it should not be scaled as this could cause some conflicing schedules. The remaining applications can be scaled according to your needs.

To scale an application, run:
```
juju scale-application <application_name> <num_of_desired_units>
```
Note: The applications must already be related to Redis as described above, or they will not be able to communicate.

Example for scaling the superset-k8s-worker below:
```
# deploy postgresql and redis charms
juju deploy postgresql-k8s --trust
juju deploy redis-k8s --channel=edge

# deploy the web server (with an alias)
juju deploy superset-k8s superset-k8s-ui

# deploy a worker
juju deploy superset-k8s --config charm-function=worker superset-k8s-worker

# deploy the beat scheduler
juju deploy superset-k8s --config charm-function=beat superset-k8s-beat

# relate applications
juju relate superset-k8s-ui postgresql-k8s
juju relate superset-k8s-ui redis-k8s
juju relate superset-k8s-worker postgresql-k8s
juju relate superset-k8s-worker redis-k8s
juju relate superset-k8s-beat postgresql-k8s
juju relate superset-k8s-beat redis-k8s

# scale worker up
juju scale-application superset-k8s-worker 3

# scale worker down
juju scale-application superset-k8s-worker 1
```

## Feature flags
There are a number of features that can be enabled/disabled via the Superset charm `juju config superset-k8s-ui <flag-name>=<True>`. These are:
- `DASHBOARD_CROSS_FILTERS` (default=true),
- `DASHBOARD_RBAC`(default=true),
- `EMBEDDABLE_CHARTS` (default=true),
- `SCHEDULED_QUERIES` (default=true),
- `ESTIMATE_QUERY_COST` (default=true),
- `ENABLE_TEMPLATE_PROCESSING` (default=true),
- `GLOBAL_ASYNC_QUERIES` (default=false),

Note: `GLOBAL_ASYNC_QUERIES` by default is not enabled as this requires at least 1 worker deployment.

Additional feature flags are available which are not currently implemented by the Superset Charm, a complete list can be found [here](https://github.com/apache/superset/blob/master/RESOURCES/FEATURE_FLAGS.md), with descriptions [here](https://preset.io/blog/feature-flags-in-apache-superset-and-preset/).

## Global asynchronous queries (disabled by default)
The `GLOBAL_ASYNC_QUERIES` feature flag enables asynchronous querying for Superset charts and dashboards (otherwise only available with SQLlabs). Queries are added to the Celery queue where they are picked up by the next available Superset worker, while the worker executes the queries the server receives updates via regular HTTP polling.

With asynchronous queries Superset can avoid browser timeouts that occur when executing long-running queries and instead default to the database timeouts as the limitation. Additionally this allows the system to handle a large number of concurrent users and queries without signifcant impact on performance, preventing bottlenecks during peak periods.

As the number of clients increase it may be necessary to scale both the Superset Charm worker and Superset Charm server to maintain performance as a large number of concurrent polling requests can strain server resources.

Additional information on this feature can be found [here](https://github.com/apache/superset/issues/9190).

## HTML Sanitization (enabled by default)
By default HTML Sanitization is enabled in the Superset charm for security purposes. This imposes limitations within Superset’s configuration, which limit the set of tags and attributes available for use by the Handlebars Chart and the Markdown component in their default configuration.

To enable Handlebars plugin with all CSS capabilities, there are two options to choose from:

1. (Recommended) You can configure `HTML_SANITIZATION_SCHEMA_EXTENSIONS` with some additional config overrides. As long as `HTML_SANITIZATION` remains enabled, this allows you to to extend the sanitization schema to allow more HTML elements and attributes such as `style` and `class` attributes on all HTML tags. Example configuration below.
2. Set `HTML_SANITIZATION=False`. This allows Markdown (and the Handlebars plugin) to render ALL HTML tags and attributes. This change removes the limits imposed HTML sanitization in their entirety. This is **not** advised for production deployments.

```
HTML_SANITIZATION_SCHEMA_EXTENSIONS = {
  "attributes": {
    "*": ["style","className"],
  },
  "tagNames": ["style"],
}
```

## Contributing
Please see the [Juju SDK documentation](https://juju.is/docs/sdk) for more information about developing and improving charms and [Contributing](CONTRIBUTING.md) for developer guidance.

## License
The Charmed Superset K8s Operator is free software, distributed under the Apache Software License, version 2.0. See [License](LICENSE) for more details. 
