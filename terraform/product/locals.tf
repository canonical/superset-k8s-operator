# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  module_version = "0.1.0"

  # Deploy each mandatory dependency in-module unless an external offer is supplied for it.
  deploy_postgresql = var.database_offer_url == ""
  deploy_redis      = var.redis_offer_url == ""

  enable_sso   = nonsensitive(var.oauth_external_idp_integrator_config != null)
  enable_smtp  = nonsensitive(var.smtp_integrator_config != null)
  enable_trino = var.trino_catalog_offer_url != ""

  # Signing keys: overrides when provided, else generated random values.
  secret_key        = var.signing_keys.secret_key != "" ? var.signing_keys.secret_key : random_password.secret_key.result
  async_queries_jwt = var.signing_keys.async_queries_jwt != "" ? var.signing_keys.async_queries_jwt : random_password.async_queries_jwt.result

  # Config this module owns. Every application of one deployment must name the same secret, and
  # `charm-function` is what makes the three applications differ, so both are merged last and are
  # rejected by the config variables' validation.
  owned_config = {
    "signing-keys-secret-id" = juju_secret.signing_keys.secret_id
  }

  ui_config = merge(
    var.superset.config,
    var.superset_ui.config,
    local.owned_config,
    { "charm-function" = "app-gunicorn" },
  )

  # `server-alias` is the hostname the worker loads dashboards from when rendering a report
  # screenshot, so it has to resolve to the UI application.
  worker_config = merge(
    { "server-alias" = var.superset_ui.app_name },
    var.superset.config,
    var.superset_worker.config,
    local.owned_config,
    { "charm-function" = "worker" },
  )

  beat_config = merge(
    var.superset.config,
    var.superset_beat.config,
    local.owned_config,
    { "charm-function" = "beat" },
  )

  # Keyed by role rather than by application name so the integrations below can be created with
  # `for_each` over keys that are known at plan time.
  superset_requires = {
    ui     = module.superset_ui.requires
    worker = module.superset_worker.requires
    beat   = module.superset_beat.requires
  }

  # Ingress: serve the UI at the root of a hostname. Traefik refuses `routing_mode = "subdomain"`
  # unless `external_hostname` is set too, so the pair is derived together.
  traefik_config = merge(
    var.external_hostname != "" ? {
      "external_hostname" = var.external_hostname
      "routing_mode"      = "subdomain"
    } : {},
    var.traefik.config,
  )

  oauth_charm_config = var.oauth_external_idp_integrator_config == null ? {} : {
    issuer_url             = var.oauth_external_idp_integrator_config.issuer_url
    authorization_endpoint = var.oauth_external_idp_integrator_config.authorization_endpoint
    token_endpoint         = var.oauth_external_idp_integrator_config.token_endpoint
    introspection_endpoint = var.oauth_external_idp_integrator_config.introspection_endpoint
    userinfo_endpoint      = var.oauth_external_idp_integrator_config.userinfo_endpoint
    jwks_endpoint          = var.oauth_external_idp_integrator_config.jwks_endpoint
    scope                  = var.oauth_external_idp_integrator_config.scope
    client_id              = sensitive(var.oauth_external_idp_integrator_config.client_id)
    client_secret          = sensitive(var.oauth_external_idp_integrator_config.client_secret)
    jwt_access_token       = tostring(var.oauth_external_idp_integrator_config.jwt_access_token)
  }

  smtp_charm_config = var.smtp_integrator_config == null ? {} : {
    host               = var.smtp_integrator_config.host
    port               = tostring(var.smtp_integrator_config.port)
    user               = var.smtp_integrator_config.user
    password_secret    = juju_secret.smtp_password[0].secret_uri
    auth_type          = var.smtp_integrator_config.auth_type
    transport_security = var.smtp_integrator_config.transport_security
    domain             = var.smtp_integrator_config.domain
    skip_ssl_verify    = tostring(var.smtp_integrator_config.skip_ssl_verify)
    smtp_sender        = var.smtp_integrator_config.smtp_sender
    recipients         = var.smtp_integrator_config.recipients
  }
}
