# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

### SIGNING KEYS: one secret shared by the three Superset applications

resource "random_password" "secret_key" {
  length  = 32
  special = false
}

resource "random_password" "async_queries_jwt" {
  length  = 64
  special = false
}

resource "juju_secret" "signing_keys" {
  model_uuid = var.model_uuid
  name       = "superset-signing-keys"
  info       = "Session and asynchronous query signing keys for Superset."
  value = {
    "secret-key"        = local.secret_key
    "async-queries-jwt" = local.async_queries_jwt
  }
}

resource "juju_access_secret" "signing_keys" {
  model_uuid = var.model_uuid
  applications = [
    module.superset_ui.application.name,
    module.superset_worker.application.name,
    module.superset_beat.application.name,
  ]
  secret_id = juju_secret.signing_keys.secret_id
}

### SUPERSET: one charm, three applications

module "superset_ui" {
  source = "../charm"

  app_name    = var.superset_ui.app_name
  model_uuid  = var.model_uuid
  channel     = var.superset.channel
  revision    = var.superset.revision
  base        = var.superset.base
  config      = local.ui_config
  constraints = var.superset.constraints
  resources   = var.superset.resources
  units       = var.superset_ui.units
}

module "superset_worker" {
  source = "../charm"

  app_name    = var.superset_worker.app_name
  model_uuid  = var.model_uuid
  channel     = var.superset.channel
  revision    = var.superset.revision
  base        = var.superset.base
  config      = local.worker_config
  constraints = var.superset.constraints
  resources   = var.superset.resources
  units       = var.superset_worker.units
}

module "superset_beat" {
  source = "../charm"

  app_name    = var.superset_beat.app_name
  model_uuid  = var.model_uuid
  channel     = var.superset.channel
  revision    = var.superset.revision
  base        = var.superset.base
  config      = local.beat_config
  constraints = var.superset.constraints
  resources   = var.superset.resources
  units       = 1
}

### DEPENDENCIES: deployed in-module unless an external offer is supplied

module "postgresql" {
  count  = local.deploy_postgresql ? 1 : 0
  source = "git::https://github.com/canonical/postgresql-k8s-operator//terraform?ref=v16/1.194.0"

  juju_model         = var.model_uuid
  app_name           = var.postgresql.app_name
  channel            = var.postgresql.channel
  revision           = var.postgresql.revision
  base               = var.postgresql.base
  constraints        = var.postgresql.constraints
  config             = var.postgresql.config
  resources          = var.postgresql.resources
  storage_directives = var.postgresql.storage_directives
  units              = var.postgresql.units
}

module "redis" {
  count  = local.deploy_redis ? 1 : 0
  source = "./modules/redis-k8s"

  model_uuid         = var.model_uuid
  app_name           = var.redis.app_name
  channel            = var.redis.channel
  revision           = var.redis.revision
  base               = var.redis.base
  constraints        = var.redis.constraints
  config             = var.redis.config
  resources          = var.redis.resources
  storage_directives = var.redis.storage_directives
  units              = var.redis.units
}

### INGRESS + TLS

module "traefik" {
  source = "git::https://github.com/canonical/traefik-k8s-operator//terraform?ref=traefik-k8s-rev452"

  model_uuid         = var.model_uuid
  app_name           = var.traefik.app_name
  channel            = var.traefik.channel
  revision           = var.traefik.revision
  base               = var.traefik.base
  constraints        = var.traefik.constraints
  config             = local.traefik_config
  resources          = var.traefik.resources
  storage_directives = var.traefik.storage_directives
  units              = var.traefik.units
}

module "self_signed_certificates" {
  source = "git::https://github.com/canonical/self-signed-certificates-operator//terraform?ref=e7527a7"

  model_uuid  = var.model_uuid
  app_name    = var.self_signed_certificates.app_name
  channel     = var.self_signed_certificates.channel
  revision    = var.self_signed_certificates.revision
  base        = var.self_signed_certificates.base
  constraints = var.self_signed_certificates.constraints
  config      = var.self_signed_certificates.config
  units       = var.self_signed_certificates.units
}

### SSO (optional): external IdP integrator related to the UI over the `oauth` interface

module "oauth_external_idp_integrator" {
  count  = local.enable_sso ? 1 : 0
  source = "./modules/oauth-external-idp-integrator"

  model_uuid  = var.model_uuid
  app_name    = var.oauth_external_idp_integrator_charm.app_name
  channel     = var.oauth_external_idp_integrator_charm.channel
  revision    = var.oauth_external_idp_integrator_charm.revision
  base        = var.oauth_external_idp_integrator_charm.base
  constraints = var.oauth_external_idp_integrator_charm.constraints
  units       = var.oauth_external_idp_integrator_charm.units
  config      = local.oauth_charm_config
}

### ALERTS AND REPORTS (optional): SMTP relay related to all three applications

module "smtp_integrator" {
  count  = local.enable_smtp ? 1 : 0
  source = "./modules/smtp-integrator"

  model_uuid  = var.model_uuid
  app_name    = var.smtp_integrator_charm.app_name
  channel     = var.smtp_integrator_charm.channel
  revision    = var.smtp_integrator_charm.revision
  base        = var.smtp_integrator_charm.base
  constraints = var.smtp_integrator_charm.constraints
  units       = var.smtp_integrator_charm.units
  config      = local.smtp_charm_config
}

### INTEGRATIONS: every Superset application to PostgreSQL and to Redis
#
# No ordering is imposed between the three. Only the UI application migrates the metadata
# database; the worker and the beat scheduler report `waiting for the UI to initialise the
# database` and are released by the following update-status.

resource "juju_integration" "superset_postgresql" {
  for_each = local.superset_requires

  model_uuid = var.model_uuid

  application {
    name     = each.value.postgresql_db.name
    endpoint = each.value.postgresql_db.endpoint
  }

  application {
    name      = local.deploy_postgresql ? module.postgresql[0].application_name : null
    endpoint  = local.deploy_postgresql ? module.postgresql[0].provides.database : null
    offer_url = local.deploy_postgresql ? null : var.database_offer_url
  }
}

resource "juju_integration" "superset_redis" {
  for_each = local.superset_requires

  model_uuid = var.model_uuid

  application {
    name     = each.value.redis.name
    endpoint = each.value.redis.endpoint
  }

  application {
    name      = local.deploy_redis ? module.redis[0].provides.redis.name : null
    endpoint  = local.deploy_redis ? module.redis[0].provides.redis.endpoint : null
    offer_url = local.deploy_redis ? null : var.redis_offer_url
  }
}

### INTEGRATIONS: the UI to ingress, and ingress to TLS
#
# Only the UI holds an ingress relation. The worker and the beat scheduler serve no HTTP.

resource "juju_integration" "superset_ingress" {
  model_uuid = var.model_uuid

  application {
    name     = module.superset_ui.requires.ingress.name
    endpoint = module.superset_ui.requires.ingress.endpoint
  }

  application {
    name     = module.traefik.application.name
    endpoint = module.traefik.provides.ingress
  }
}

resource "juju_integration" "traefik_certificates" {
  model_uuid = var.model_uuid

  application {
    name     = module.traefik.application.name
    endpoint = module.traefik.requires.certificates
  }

  application {
    name     = module.self_signed_certificates.app_name
    endpoint = module.self_signed_certificates.provides.certificates
  }
}

### INTEGRATIONS: optional relations

resource "juju_integration" "superset_oauth" {
  count = local.enable_sso ? 1 : 0

  model_uuid = var.model_uuid

  application {
    name     = module.superset_ui.requires.oauth.name
    endpoint = module.superset_ui.requires.oauth.endpoint
  }

  application {
    name     = module.oauth_external_idp_integrator[0].provides.oauth.name
    endpoint = module.oauth_external_idp_integrator[0].provides.oauth.endpoint
  }
}

# The `smtp` endpoint is declared on the charm as a whole and `ALERT_REPORTS` is checked per
# application, so all three are related: the UI offers alerts and reports, the worker delivers
# them and the beat scheduler dispatches them.
resource "juju_integration" "superset_smtp" {
  for_each = local.enable_smtp ? local.superset_requires : {}

  model_uuid = var.model_uuid

  application {
    name     = each.value.smtp.name
    endpoint = each.value.smtp.endpoint
  }

  application {
    name     = module.smtp_integrator[0].provides.smtp.name
    endpoint = module.smtp_integrator[0].provides.smtp.endpoint
  }
}

resource "juju_integration" "superset_trino_catalog" {
  count = local.enable_trino ? 1 : 0

  model_uuid = var.model_uuid

  application {
    name     = module.superset_ui.requires.trino_catalog.name
    endpoint = module.superset_ui.requires.trino_catalog.endpoint
  }

  application {
    offer_url = var.trino_catalog_offer_url
  }
}

### METADATA

resource "time_static" "deployed_at" {}

resource "time_static" "updated_at" {
  triggers = {
    superset_channel  = var.superset.channel
    superset_revision = tostring(coalesce(var.superset.revision, 0))
    superset_config   = jsonencode(var.superset.config)
    ui_config         = jsonencode(var.superset_ui)
    worker_config     = jsonencode(var.superset_worker)
    beat_config       = jsonencode(var.superset_beat)
    external_hostname = var.external_hostname
    enable_sso        = tostring(local.enable_sso)
    enable_smtp       = tostring(local.enable_smtp)
  }
}
