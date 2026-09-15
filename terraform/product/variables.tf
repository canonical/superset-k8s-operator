# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

variable "database_offer_url" {
  description = <<-EOT
    Offer URL for an external PostgreSQL `database` endpoint. Leave empty to deploy PostgreSQL
    in-module, in the same model as Superset.
  EOT
  type        = string
  default     = ""
}

variable "external_hostname" {
  description = <<-EOT
    Base DNS domain to serve the Superset UI from. When set, Traefik is configured for host-based
    routing (`external_hostname` plus `routing_mode = "subdomain"`), which the UI requires: it is a
    Flask application served at `/` and a path prefix breaks its assets and its OIDC callback.
    Leave empty to keep Traefik's default path routing, which deploys cleanly but leaves the UI
    unusable in a browser.
  EOT
  type        = string
  default     = ""
}

variable "model_uuid" {
  description = "UUID of the Kubernetes Juju model to deploy the Superset solution into."
  type        = string
  nullable    = false
}

variable "oauth_external_idp_integrator_charm" {
  description = "Charm deployment configuration for the oauth-external-idp-integrator (SSO)."
  type = object({
    app_name    = optional(string, "oauth-external-idp-integrator")
    channel     = optional(string, "latest/edge")
    revision    = optional(number)
    base        = optional(string, "ubuntu@22.04")
    constraints = optional(string, "arch=amd64")
    units       = optional(number, 1)
  })
  default = {}
}

variable "oauth_external_idp_integrator_config" {
  description = "External IdP details for SSO, served to the Superset UI over the `oauth` relation. Leave null to disable SSO."
  type = object({
    issuer_url             = optional(string, "https://accounts.google.com")
    authorization_endpoint = optional(string, "https://accounts.google.com/o/oauth2/auth")
    token_endpoint         = optional(string, "https://oauth2.googleapis.com/token")
    introspection_endpoint = optional(string, "https://oauth2.googleapis.com/tokeninfo")
    userinfo_endpoint      = optional(string, "https://www.googleapis.com/oauth2/v1/userinfo")
    jwks_endpoint          = optional(string, "https://www.googleapis.com/oauth2/v3/certs")
    scope                  = optional(string, "openid email profile")
    client_id              = string
    client_secret          = string
    jwt_access_token       = optional(bool, false)
  })
  default   = null
  sensitive = true
}

variable "postgresql" {
  description = "Configuration for the in-module PostgreSQL charm (used when database_offer_url is empty)."
  type = object({
    app_name           = optional(string, "postgresql-k8s")
    channel            = optional(string, "14/stable")
    revision           = optional(number)
    base               = optional(string, "ubuntu@22.04")
    constraints        = optional(string, "arch=amd64")
    config             = optional(map(string), {})
    resources          = optional(map(string), {})
    storage_directives = optional(map(string), { pgdata = "10G" })
    units              = optional(number, 1)
  })
  default = {}
}

variable "redis" {
  description = "Configuration for the in-module Redis charm (used when redis_offer_url is empty)."
  type = object({
    app_name           = optional(string, "redis-k8s")
    channel            = optional(string, "latest/edge")
    revision           = optional(number)
    base               = optional(string, "ubuntu@22.04")
    constraints        = optional(string, "arch=amd64")
    config             = optional(map(string), {})
    resources          = optional(map(string), {})
    storage_directives = optional(map(string), { database = "10G" })
    units              = optional(number, 1)
  })
  default = {}
}

variable "redis_offer_url" {
  description = "Offer URL for an external Redis `redis` endpoint. Leave empty to deploy Redis in-module."
  type        = string
  default     = ""
}

variable "self_signed_certificates" {
  description = "Configuration for the self-signed-certificates charm (TLS for the Traefik ingress)."
  type = object({
    app_name    = optional(string, "self-signed-certificates")
    channel     = optional(string, "1/stable")
    revision    = optional(number)
    base        = optional(string, "ubuntu@24.04")
    constraints = optional(string, "arch=amd64")
    config      = optional(map(string), {})
    units       = optional(number, 1)
  })
  default = {}
}

variable "signing_keys" {
  description = <<-EOT
    Optional overrides for the signing keys shared by the three Superset applications. When a field
    is empty a random value is generated. `secret_key` signs the session cookie and encrypts the
    database connection passwords stored in the metadata database, so it must stay the same for the
    lifetime of a deployment: set it to keep an existing deployment's connections readable.
  EOT
  type = object({
    secret_key        = optional(string, "")
    async_queries_jwt = optional(string, "")
  })
  default   = {}
  sensitive = true
}

variable "smtp_integrator_charm" {
  description = "Charm deployment configuration for the smtp-integrator (alerts and reports delivery)."
  type = object({
    app_name    = optional(string, "smtp-integrator")
    channel     = optional(string, "latest/stable")
    revision    = optional(number)
    base        = optional(string, "ubuntu@22.04")
    constraints = optional(string, "arch=amd64")
    units       = optional(number, 1)
  })
  default = {}
}

variable "smtp_integrator_config" {
  description = <<-EOT
    Relay details for the SMTP provider the `ALERT_REPORTS` feature flag needs, served to all three
    Superset applications over the `smtp` relation. Leave null to deploy no relay.
  EOT
  type = object({
    host               = string
    smtp_sender        = string
    port               = optional(number, 25)
    user               = optional(string, "")
    password           = optional(string, "")
    auth_type          = optional(string, "none")
    transport_security = optional(string, "none")
    domain             = optional(string, "")
    skip_ssl_verify    = optional(bool, false)
    recipients         = optional(string, "")
  })
  default   = null
  sensitive = true
}

variable "superset" {
  description = <<-EOT
    Deployment settings shared by the three Superset applications, plus the configuration options
    that apply to all of them. `charm-function` and `signing-keys-secret-id` are owned by this
    module and must not appear in any of the config maps.
  EOT
  type = object({
    channel     = optional(string, "latest/edge")
    revision    = optional(number)
    base        = optional(string, "ubuntu@22.04")
    constraints = optional(string)
    resources   = optional(map(string), {})
    config      = optional(map(string), {})
  })
  default = {}

  validation {
    condition = alltrue([
      !contains(keys(var.superset.config), "charm-function"),
      !contains(keys(var.superset.config), "signing-keys-secret-id"),
    ])
    error_message = "superset.config must not set charm-function or signing-keys-secret-id; the product derives both."
  }
}

variable "superset_beat" {
  description = "Configuration for the Superset beat scheduler application. It is always a single unit: a second scheduler would dispatch every scheduled task twice."
  type = object({
    app_name = optional(string, "superset-k8s-beat")
    config   = optional(map(string), {})
  })
  default = {}

  validation {
    condition = alltrue([
      !contains(keys(var.superset_beat.config), "charm-function"),
      !contains(keys(var.superset_beat.config), "signing-keys-secret-id"),
    ])
    error_message = "superset_beat.config must not set charm-function or signing-keys-secret-id; the product derives both."
  }
}

variable "superset_ui" {
  description = "Configuration for the Superset UI application, the one that serves the web interface and initialises the metadata database."
  type = object({
    app_name = optional(string, "superset-k8s-ui")
    config   = optional(map(string), {})
    units    = optional(number, 1)
  })
  default = {}

  validation {
    condition = alltrue([
      !contains(keys(var.superset_ui.config), "charm-function"),
      !contains(keys(var.superset_ui.config), "signing-keys-secret-id"),
    ])
    error_message = "superset_ui.config must not set charm-function or signing-keys-secret-id; the product derives both."
  }
}

variable "superset_worker" {
  description = "Configuration for the Superset Celery worker application."
  type = object({
    app_name = optional(string, "superset-k8s-worker")
    config   = optional(map(string), {})
    units    = optional(number, 1)
  })
  default = {}

  validation {
    condition = alltrue([
      !contains(keys(var.superset_worker.config), "charm-function"),
      !contains(keys(var.superset_worker.config), "signing-keys-secret-id"),
    ])
    error_message = "superset_worker.config must not set charm-function or signing-keys-secret-id; the product derives both."
  }
}

variable "traefik" {
  description = "Configuration for the Traefik ingress in front of the Superset UI. `routing_mode` and `external_hostname` are derived from the module's external_hostname input."
  type = object({
    app_name           = optional(string, "traefik-k8s")
    channel            = optional(string, "latest/stable")
    revision           = optional(number)
    base               = optional(string)
    constraints        = optional(string, "arch=amd64")
    config             = optional(map(string), {})
    resources          = optional(map(string), {})
    storage_directives = optional(map(string), {})
    units              = optional(number, 1)
  })
  default = {}
}

variable "trino_catalog_offer_url" {
  description = <<-EOT
    Offer URL for a Trino `trino-catalog` endpoint. When set, the Superset UI consumes it and
    Superset creates a database connection per Trino catalog. Leave empty for no Trino integration.
  EOT
  type        = string
  default     = ""
}
