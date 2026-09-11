# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

output "metadata" {
  description = "Metadata of the product deployment."
  value = {
    version     = local.module_version
    deployed_at = time_static.deployed_at.rfc3339
    updated_at  = time_static.updated_at.rfc3339
  }
}

output "models" {
  description = "Map of the deployed models and the applications in each."
  value = {
    superset = {
      model_uuid = var.model_uuid
      components = merge(
        {
          superset-ui              = module.superset_ui.application.name
          superset-worker          = module.superset_worker.application.name
          superset-beat            = module.superset_beat.application.name
          traefik-k8s              = module.traefik.application.name
          self-signed-certificates = module.self_signed_certificates.app_name
        },
        local.deploy_postgresql ? { postgresql-k8s = module.postgresql[0].application_name } : {},
        local.deploy_redis ? { redis-k8s = module.redis[0].application.name } : {},
        local.enable_sso ? {
          oauth-external-idp-integrator = module.oauth_external_idp_integrator[0].application.name
        } : {},
        local.enable_smtp ? { smtp-integrator = module.smtp_integrator[0].application.name } : {},
      )
    }
  }
}

output "offers" {
  description = "Offer URLs consumed by Superset (empty for a dependency deployed in-module)."
  value = {
    database      = var.database_offer_url
    redis         = var.redis_offer_url
    trino_catalog = var.trino_catalog_offer_url
  }
}
