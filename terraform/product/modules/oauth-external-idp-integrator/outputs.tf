# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "Object representing the deployed application."
  value       = juju_application.oauth_external_idp_integrator
}

output "provides" {
  description = "Map of all the provided endpoints."
  value = {
    oauth = {
      name     = juju_application.oauth_external_idp_integrator.name
      endpoint = "oauth"
    }
  }
}

output "requires" {
  description = "Map of all the required endpoints."
  value       = {}
}
