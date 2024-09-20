
# Prepare for Production

## Performance Optimisation

### Enable Asynchronous Querying
To enable asynchronous querying, you need to deploy Charmed Superset Workers. These workers handle long-running queries asynchronously, allowing the UI to remain responsive. You can deploy additional Superset workers to offload and process background queries. Deploy Charmed Superset workers to the same model as the server with:

```
# Deploy Charmed Superset with worker functionality
juju deploy superset-k8s --config charm-function=worker superset-k8s-worker

# Relate with Charmed PostgreSQL and Charmed Redis
juju relate superset-k8s-worker postgresql-k8s
juju relate superset-k8s-worker redis-k8s
```
Within the UI you can enable Asynchronous Query Execution (`AGE`) at the database level.
Edit the database and under the `Performance` tab check the `Asynchronous query execution`
box.

This is recommended for all production databases to relieve load on the UI.

### Enable Beat Scheduling
Superset’s scheduling system relies on a single instance of the Beat scheduler. This scheduler handles periodic jobs like caching or data refreshes. Only one instance should be deployed to avoid conflicting schedules. This can be deployed with:
```
# Deploy Charmed Superset with beat functionality
juju deploy superset-k8s --config charm-function=beat superset-k8s-beat

# Relate with Charmed PostgreSQL and Charmed Redis
juju relate superset-k8s-beat postgresql-k8s
juju relate superset-k8s-beat redis-k8s
```

### Scaling Applications
Charmed Superset supports independent scaling of the web server and workers. The web server and workers can be scaled horizontally to handle more load, while the Beat scheduler should remain singular. Use the juju `scale-application` command to adjust the number of instances of each service as needed.

```
juju scale-application superset-k8s -n 3
```
We recommend `3` units of server and `3` units of worker applications for a production deployment.


## Enabling Database Backups
Superset relies on PostgreSQL for metadata storage. You can enable database backups by integrating the PostgreSQL charm with the S3 integrator. Configure it to store backups in an S3-compatible bucket, ensuring disaster recovery and data retention.

[A complete how-to guide can be found here.](https://charmhub.io/postgresql-k8s/docs/h-configure-s3-aws)

## Integrate with Canonical Observability Stack
For observability, Superset can be integrated with [Canonical Observability Stack](https://charmhub.io/topics/canonical-observability-stack). This integration allows you to monitor metrics, logs, and events from Superset, enabling proactive health checks and performance analysis. Prometheus, Grafana and Loki are included as part of this observability suite.

To deploy `cos-lite` and expose its endpoints as offers, follow these steps:

```bash
# Deploy the cos-lite bundle
juju add-model cos
juju deploy cos-lite --trust
```

```bash
# Expose the cos integration endpoints
juju offer prometheus:metrics-endpoint
juju offer loki:logging
juju offer grafana:grafana-dashboard

# Relate superset to the cos-lite apps
juju relate superset-k8s admin/cos.grafana
juju relate superset-k8s admin/cos.loki
juju relate superset-k8s admin/cos.prometheus
```

```bash
# Access grafana with username "admin" and password
juju run grafana/0 -m cos get-admin-password --wait 1m

# Note the application IP address
juju status
```
The Grafana UI can be found on the application IP address, port 3000. Pre-built dashboards are included under `Superset Metrics`.


## Security

### Terminate TLS at Ingress
Superset can terminate TLS at the ingress by leveraging the [Nginx Ingress Integrator Charm](https://charmhub.io/nginx-ingress-integrator).

You can deploy this with:

```bash
# Deploy the nginx ingress integrator charm
juju deploy nginx-ingress-integrator --trust
```

#### Using K8s Secrets
You can use a self-signed or production-grade TLS certificate stored in a Kubernetes secret. The secret is then associated with the ingress to encrypt traffic between clients and the Superset UI.

For self-signed certificates you can do the below, if you have a production-grade certificate skip to the `create a k8s secret` step.

```bash
# Generate private key
openssl genrsa -out server.key 2048

# Generate a certificate signing request
openssl req -new -key server.key -out server.csr -subj "/CN=<YOUR_HOST_NAME>"

# Create self-signed certificate
openssl x509 -req -days 365 -in server.csr -signkey server.key -out server.crt -extfile <(printf "subjectAltName=DNS:<YOUR_HOST_NAME>")

# Create a k8s secret
kubectl create secret tls superset-tls --cert=server.crt --key=server.key
```

This secret name is provided to Charmed Superset and related to the Nginx Ingress Integrator:

```bash
# Configure the secret name
juju config superset-k8s tls-secret-name=superset-tls

# Configure the hostname
juju config superset-k8s external-hostname=<YOUR_HOST_NAME>

# Relate to the Integrator Charm
juju relate superset-k8s nginx-ingress-integrator
```

You can now validate your ingress has been created with the tls certificates provided:
```bash
# Note the ingress with name like relation_<num>-<hostname>-ingress
kubectl get ingress

kubectl describe <YOUR_INGRESS_NAME>
```
You should see this using the K8s secret provided.

### Enable Google Oauth
Enabling Google Oauth for Charmed Superset allows users to authenticate using their Google accounts, streamlining login and increasing security.

To enable Google Oauth, you will need a Google project, you can create one [here](https://console.cloud.google.com/projectcreate).

#### Obtain Oauth2 credentials
If you do not already have Oauth2 credentials set up then follow the steps below:
1. Navigate to https://console.cloud.google.com/apis/credentials 
2. Click `+ Create Credentials` 
3. Select `Oauth client ID`
4. Select application type (Web application)
5. Name the application
6. Add an Authorized redirect URI (`https://<host>:8088/oauth-authorized/google`)
7. Create and download your client ID and client secret

### Apply oauth configuration to Superset charm
Create a file `oauth.yaml` using the Oauth2 credentials you obtained from Google, following the example below and replacing the values.
```yaml
superset-k8s:
  google-client-id: "client_id"
  google-client-secret: "client_secret"
  oauth-domain: "companydomain.com"
  oauth-admin-email: "user@companydomain.com"
```
This file can now be applied to Charmed Superset with:

```bash
juju config superset-k8s --file=path/to/oauth.yaml
```

### Configure the self-registraton role
By default, with Google Oauth, a Superset user account is automatically created following successful authentication. This user is provided the least privileged role of `Public`, this role can then be elevated in the UI or via API by an `Admin` user. 

To change the role that is applied on self-registration, simply pass the role via the configuration parameter `self-registration-role`. Superset's standard roles and their associated permissions can be found [here](https://github.com/apache/superset/blob/master/RESOURCES/STANDARD_ROLES.md).

```bash
juju config superset-k8s self-registration-role=Alpha
```

### Configuring HTML Sanitization (enabled by default)
Superset supports the sanitization of user-generated HTML content to prevent cross-site scripting (XSS)attacks. This ensures that any embedded HTML in dashboards or reports is safely displayed without executing malicious scripts. This is enabled in Charmed Superset by default.

Some functionality, such as the CSS capabilities provided by the Handlebars plugin, requires the ability to render HTML elements and attributes such as `style` and `class`.

This can be carefully extended using the `html-sanitization-schema-extensions` paramerter.

An example enabling `style` and `class` is shown below in `sanitization-extensions.json`:
```json
{
  "attributes": {
    "*": ["style","className"],
  },
  "tagNames": ["style"],
}
```

This can be applied to Charmed Superset with the below:
```bash
juju config superset-k8s html-sanititization-schema-extensions=@sanitization-extensions.json
```
