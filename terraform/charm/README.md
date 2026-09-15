# Superset charm Terraform module

This folder contains a base [Terraform][Terraform] module for the `superset-k8s` charm.

The module uses the [Terraform Juju provider][Terraform Juju provider] to model the charm
deployment onto any Kubernetes environment managed by Juju. It models one `superset-k8s`
application only. Its dependencies (PostgreSQL, Redis, ingress, TLS, SSO, SMTP) are wired up by the
higher-level [product module](../product).

## One deployment is three applications

`superset-k8s` is deployed several times over: `charm-function` splits it into `app-gunicorn` (the
web UI), `worker` (Celery) and `beat` (the scheduler), which together make one Superset. This
module deploys **one** of them, so a full deployment instantiates it three times with a different
`app_name` and `charm-function`. The applications of one deployment must also name the same
`signing-keys-secret-id`; the product module owns both of those values.

Only the UI application initialises the metadata database. The worker and the beat scheduler wait
in `WaitingStatus` until it has, so no ordering is imposed here: relations can be created in any
order and the deployment converges.

## Module structure

- **main.tf** - Defines the `superset-k8s` Juju application.
- **variables.tf** - Deployment options (model UUID, channel, revision, config, resources, units).
- **locals.tf** - The charm's provided and required endpoint names.
- **outputs.tf** - The application object and its integration endpoints.
- **terraform.tf** - Terraform and provider version constraints.

## Inputs

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `app_name` | `string` | `"superset-k8s"` | Name of the application in the Juju model. |
| `base` | `string` | `"ubuntu@22.04"` | The operating system on which to deploy. |
| `channel` | `string` | `"latest/edge"` | Charmhub channel to deploy from. |
| `config` | `map(string)` | `{}` | Charm configuration, passed unchanged. |
| `constraints` | `string` | `null` | Juju deployment constraints. |
| `model_uuid` | `string` | None | Target Juju model UUID. Required, not nullable. |
| `resources` | `map(string)` | `{}` | OCI-image overrides (`superset-image`). Empty uses the images bundled with the revision. |
| `revision` | `number` | `null` | Charm revision. Null deploys the latest revision on the channel. |
| `units` | `number` | `1` | Number of units. Must be at least 1. |

`config` is a `map(string)` rather than a typed object: `superset-k8s` carries around thirty
configuration options, split by which of the three applications reads them, and the product module
assembles a different map per application from shared and per-role inputs.

## Outputs

| Name | Description |
| --- | --- |
| `application` | The full `juju_application` resource. |
| `provides` | Map of provided endpoints (`grafana_dashboard`, `metrics_endpoint`), each `{ name, endpoint }`. |
| `requires` | Map of required endpoints (`certificates`, `ingress`, `logging`, `oauth`, `postgresql_db`, `redis`, `smtp`, `trino_catalog`), in the same shape. |

## Using the `superset-k8s` base module in higher level modules

```hcl
module "superset_ui" {
  source     = "git::https://github.com/canonical/superset-k8s-operator//terraform/charm"
  model_uuid = var.model_uuid
  app_name   = "superset-k8s-ui"

  config = {
    "charm-function"         = "app-gunicorn"
    "signing-keys-secret-id" = juju_secret.signing_keys.secret_id
  }
}
```

Create integrations, for instance against Traefik:

```hcl
resource "juju_integration" "superset_ingress" {
  model_uuid = var.model_uuid

  application {
    name     = module.superset_ui.requires.ingress.name
    endpoint = module.superset_ui.requires.ingress.endpoint
  }

  application {
    name     = "traefik-k8s"
    endpoint = "ingress"
  }
}
```

The complete list of available integrations can be found [in the Integrations tab][superset-integrations].

## Testing

Run from this directory:

```shell
terraform init
terraform fmt -check
terraform validate
terraform test
```

`terraform test` applies for real against the Juju controller the provider is configured for. The
defaults match CI; for a local MicroK8s controller pass the cloud names and StorageClass as
environment globals:

```shell
TF_VAR_k8s_cloud_name=microk8s TF_VAR_k8s_credential_name=microk8s \
  TF_VAR_k8s_workload_storage=microk8s-hostpath terraform test
```

[Terraform]: https://www.terraform.io/
[Terraform Juju provider]: https://registry.terraform.io/providers/juju/juju/latest
[superset-integrations]: https://charmhub.io/superset-k8s/integrations
