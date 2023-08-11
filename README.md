# superset-k8s-charm
Superset is fast, lightweight, intuitive, and loaded with options that make it easy for users of all skill sets to explore and visualize their data.

## Relations
### Redis
Redis acts as both a cache and message broker for Superset services. It's a requirement to have a redis relation in order to start the Superset application.
```
# deploy redis charm
juju deploy redis-k8s --edge

# relate redis charm
juju relate redis-k8s superset-k8s
```

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


## Contributing
Please see the [Juju SDK documentation](https://juju.is/docs/sdk) for more information about developing and improving charms and [Contributing](CONTRIBUTING.md) for developer guidance.

## License
The Charmed Superset K8s Operator is free software, distributed under the Apache Software License, version 2.0. See [License](LICENSE) for more details. 
