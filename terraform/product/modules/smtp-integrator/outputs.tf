# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "Object representing the deployed application."
  value       = juju_application.smtp_integrator
}

output "provides" {
  description = "Map of all the provided endpoints."
  value = {
    smtp = {
      name     = juju_application.smtp_integrator.name
      endpoint = "smtp"
    }
    smtp_legacy = {
      name     = juju_application.smtp_integrator.name
      endpoint = "smtp-legacy"
    }
  }
}

output "requires" {
  description = "Map of all the required endpoints."
  value       = {}
}
