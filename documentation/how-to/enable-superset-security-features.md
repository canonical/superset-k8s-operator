# Enable security features
This guide describes the implementation of security features such as encryption, authentication and HTML sanitization.

## Terminate TLS at ingress
Superset can terminate Transport Layer Security (TLS) at the ingress by leveraging the [Nginx Ingress Integrator Charm](https://charmhub.io/nginx-ingress-integrator).

Deploy this by running:

```bash
juju deploy nginx-ingress-integrator --trust
```

### Using K8s secrets
You can use a self-signed or production-grade TLS certificate stored in a Kubernetes secret. The secret is then associated with the ingress to encrypt traffic between clients and the Superset User Interface (UI).

For self-signed certificates you can do the following:

1. First generate a private key using `openssl` and a certificate signing request using they key you just created. Replace `<YOUR_HOSTNAME>` with an appropriate hostname such as `superset-k8s.com`:

```bash
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=<YOUR_HOSTNAME>"
```
2. You can now sign this signing request, creating your self-signed certificate:
```bash
openssl x509 -req -days 365 -in server.csr -signkey server.key -out server.crt -extfile <(printf "subjectAltName=DNS:<YOUR_HOSTNAME>")
```
3. Next, add this certificate and key as a Kubernetes secret to be used by the ingress:
```bash
kubectl create secret tls superset-tls --cert=server.crt --key=server.key
```
4. You then need to provide the name of the Kubernetes secret to the Superset charm, along with the hostname you included in the certificate:

```bash
juju config superset-k8s tls-secret-name=superset-tls
juju config superset-k8s external-hostname=<YOUR_HOSTNAME>

```
5. Finally, relate Superset with the Nginx Ingress Integrator to create your ingress resource:
```bash
juju relate superset-k8s nginx-ingress-integrator
```
[note]
If you have a production-grade certificate, skip to step 3.
[/note]

Validate your ingress has been created with the TLS certificates:
```bash
kubectl get ingress
kubectl describe <YOUR_INGRESS_NAME>
```
The ingress has the format `<relation_num>-<hostname>-ingress`. The `describe` command should show something similar to the below, with the Kubernetes secret you configured in `TLS`:

```
Name:             relation-201-superset-k8s-com-ingress
Labels:           app.juju.is/created-by=nginx-ingress-integrator
                  nginx-ingress-integrator.charm.juju.is/managed-by=nginx-ingress-integrator
Namespace:        superset-model
Address:          <list-of-ips>
Ingress Class:    nginx-ingress-controller
Default backend:  <default>
TLS:
  superset-tls terminates superset-k8s.com
```

## Enable Google Oauth
Enabling Google Oauth for Charmed Superset allows users to authenticate using their Google accounts, streamlining login and increasing security.

To enable Google Oauth, you need a Google project. You can create one [here](https://console.cloud.google.com/projectcreate).

#### Obtain Oauth2 credentials
If you do not already have Oauth2 credentials set up then follow the steps below:
1. Navigate to https://console.cloud.google.com/apis/credentials.
2. Click `+ Create Credentials`.
3. Select `Oauth client ID`.
4. Select application type (`Web application`).
5. Name the application.
6. Add an Authorized redirect URI (`https://<host>:8088/oauth-authorized/google`).
7. Create and download your client ID and client secret.

### Apply Oauth configuration to Superset charm
Create a file `oauth.yaml` using the Oauth2 credentials you obtained from Google, following the example below and replacing the values:
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
By default, with Google Oauth, a Superset user account is automatically created following successful authentication. This user is provided the least privileged role of `Public`, this role can then be elevated in the UI or via Application Programming Interface (API) by an `Admin` user. 

To change the role that is applied on self-registration, simply pass the role via the configuration parameter `self-registration-role`. Superset's standard roles and their associated permissions can be found [here](https://github.com/apache/superset/blob/master/RESOURCES/STANDARD_ROLES.md).

```bash
juju config superset-k8s self-registration-role=Alpha
```

## Configuring HTML sanitization
Superset supports the sanitization of user-generated HTML content to prevent cross-site scripting (XSS)attacks. This ensures that any embedded HTML in dashboards or reports is safely displayed without executing malicious scripts. This is enabled in Charmed Superset by default.

[note]
Charmed Superset enables by default the sanitiation of HTML content.
[\note]
Some functionality, such as the CSS capabilities provided by the Handlebars plugin, requires the ability to render HTML elements and attributes such as `style` and `class`.

This can be carefully extended using the `html-sanitization-schema-extensions` parameter.

For example, you can enable `style` and `class` as shown below in `sanitization-extensions.json`:
```json
{
  "attributes": {
    "*": ["style","className"],
  },
  "tagNames": ["style"],
}
```

This can be applied to Charmed Superset as follows:
```bash
juju config superset-k8s html-sanititization-schema-extensions=@sanitization-extensions.json
```
