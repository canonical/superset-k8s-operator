# Superset product Terraform module

This folder contains a Terraform **product module** that deploys a full Superset solution into one
Kubernetes model: the three `superset-k8s` applications that make one deployment (via the
[charm module](../charm)), their mandatory dependencies (PostgreSQL and Redis), a Traefik ingress
with TLS from self-signed-certificates, the Juju secret the applications share, and optionally an
IdP integrator for SSO, an SMTP relay for alerts and reports, and a Trino catalog offer.

## Topology

Every charm this module deploys is a Kubernetes charm, so one K8s model on one controller is
enough and every relation is in-model. The module consumes a model rather than creating one: pass
the UUID of a model you have already created, so the cloud, credential and `workload-storage` stay
yours to choose.

## One Superset is three applications

`charm-function` splits `superset-k8s` into `app-gunicorn` (the web UI), `worker` (Celery) and
`beat` (the scheduler). The module deploys the charm module three times and owns that option: it is
what makes the applications differ, so it is rejected in every caller-supplied config map and
merged last.

- The UI serves the web interface and is the only application that migrates the metadata database.
- The worker executes Celery tasks: asynchronous queries, alerts and reports.
- The beat scheduler dispatches them. It is always a single unit; a second scheduler would dispatch
  every scheduled task twice, so `superset_beat` has no `units` input.

**No ordering is imposed on the relations.** Only the UI initialises the metadata database, and the
charm gates on that: the worker and the beat scheduler report
`waiting for the UI to initialise the database` until `ab_role` is populated, and the following
`update-status` releases them. Terraform therefore creates the six database and cache relations in
whatever order it likes.

## Configuration inputs

Configuration reaches the applications from three places, merged in this order:

1. `superset.config` - options that apply to all three, such as `feature-flags` and the Sentry and
   SQLAlchemy pool options.
2. `superset_ui.config`, `superset_worker.config`, `superset_beat.config` - options that apply to
   one, such as `load-examples` (UI), `screenshot-timeout` (worker) and `log-retention-days` (beat).
3. The options this module owns: `signing-keys-secret-id` and `charm-function`. Setting either in
   any of the config maps is a variable-validation error.

`server-alias` on the worker is derived from the UI application's name, since it is the hostname the
worker loads dashboards from when rendering a report screenshot. An explicit value in
`superset.config` or `superset_worker.config` still wins.

## Secrets

| Secret | Content | Do you supply values? |
|--------|---------|------------------------|
| `superset-signing-keys` | `secret-key`, `async-queries-jwt` | **No**, random values are generated and granted to the three applications. |

`secret-key` signs the session cookie and encrypts the database connection passwords stored in the
metadata database, so it has to stay the same for the lifetime of a deployment: changing it makes
every stored connection password unreadable. Set `signing_keys` to adopt an existing deployment's
keys; leave it alone otherwise.

> Generated keys, the IdP client credentials and the SMTP password are stored in Terraform state.
> Use an encrypted / remote backend in real deployments.

## Dependencies: deploy or bring your own

PostgreSQL and Redis are mandatory for Superset and are deployed in-module by default. Point
`database_offer_url` or `redis_offer_url` at an existing offer to skip that deployment and consume
the offer instead. The two toggles are independent.

## Ingress and host-based routing

Traefik routes by path prefix by default (`https://<LB IP>/<model>-<app>`). Superset is a Flask
application served at `/`, so behind a path prefix its assets and its OIDC callback do not load.
Set `external_hostname` and the module configures Traefik for host-based routing instead:

```hcl
external_hostname = "example.com"
```

That sets `external_hostname` plus `routing_mode = "subdomain"`, and Traefik then serves the UI at
`<model name>-<ui app name>.example.com`. Point that name, or a wildcard record for the domain, at
Traefik's load balancer address. Ask Traefik for the published URL rather than building it by
hand:

```shell
juju run traefik-k8s/0 show-external-endpoints
```

Leaving `external_hostname` empty keeps path routing. Nothing blocks and the deployment reaches
active, but the UI is not usable in a browser, and SSO needs an HTTPS URL at a real hostname.

## SSO via the `oauth` relation

Set `oauth_external_idp_integrator_config` (at minimum `client_id` and `client_secret`; the
endpoint options default to Google). The module deploys
[oauth-external-idp-integrator](https://charmhub.io/oauth-external-idp-integrator) and relates it to
the **UI** on the `oauth` interface. Leave the variable `null` to disable SSO.

The UI blocks with `OAuth requires an HTTPS ingress URL` until the ingress publishes an HTTPS URL,
which is why the module integrates Traefik with self-signed-certificates. The callback the charm
registers is `<ingress URL>/oauth-authorized/oidc`.

## Alerts and reports

`ALERT_REPORTS` in `feature-flags` makes the UI offer alerts and reports, the beat scheduler
schedule them and the worker deliver them. The charm blocks all three with
`ALERT_REPORTS requires an smtp relation` unless a relay is related or `report-dry-run` is set, so
set `smtp_integrator_config` alongside the flag. The module relates the relay to all three
applications, since the flag is checked per application.

`external-url` on the worker is the link report recipients open. The worker holds no ingress
relation and cannot learn it, so set it in `superset_worker.config` when reports are enabled.

## Trino

Set `trino_catalog_offer_url` to relate the UI to a Trino `trino-catalog` offer; Superset then
creates a database connection per Trino catalog. Trino itself is not deployed here: it is an
optional integration with its own product module.

## Running the module tests

`terraform test` defaults match CI ([operator-workflows](https://github.com/canonical/operator-workflows)
registers the K8s cloud as `tfk8s`; storage uses the cluster's default StorageClass). To run
locally against a different setup, pass globals via the environment. MicroK8s, for example, needs an
explicit StorageClass:

```shell
TF_VAR_k8s_cloud_name=microk8s TF_VAR_k8s_credential_name=microk8s \
  TF_VAR_k8s_workload_storage=microk8s-hostpath terraform test
```

## Module structure

This product module is composed entirely of charm modules; it declares no `juju_application` of its
own.

- **main.tf** - composes the three Superset applications, PostgreSQL, Redis, Traefik, TLS and the
  optional SSO and SMTP charm modules; creates the signing-keys secret and every integration.
- **variables.tf** - model UUID, per-charm configuration objects, offer-URL toggles, secret
  overrides and `external_hostname`.
- **locals.tf** - dependency and feature toggles, the per-application config assembly and the
  Traefik routing config.
- **outputs.tf** - `models`, `metadata`, `offers`.
- **terraform.tf** - Terraform and provider version constraints.
- **modules/{redis-k8s,oauth-external-idp-integrator,smtp-integrator}** - local charm modules for
  the charms that have no usable upstream Terraform module. `redis-k8s` publishes none;
  `oauth-external-idp-integrator` has no repository of its own; `smtp-integrator` publishes one that
  pins `juju ~> 1.0`, which cannot resolve alongside the `~> 2.0` this repo declares. Swap each
  `source` to the official module once that changes.

PostgreSQL, Traefik and self-signed-certificates come from their upstream modules, pinned by tag or
commit.

## Example

```hcl
module "superset" {
  source = "git::https://github.com/canonical/superset-k8s-operator//terraform/product"

  model_uuid        = juju_model.superset.uuid
  external_hostname = "example.com"

  superset = {
    channel = "latest/edge"
    config  = { "feature-flags" = "DASHBOARD_RBAC,EMBEDDABLE_CHARTS" }
  }

  superset_ui     = { units = 2 }
  superset_worker = { units = 3 }
}
```

See [opentofu.tfvars.example](opentofu.tfvars.example) for a fuller set of inputs.
