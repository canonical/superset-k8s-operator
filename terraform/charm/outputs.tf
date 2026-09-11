# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "Object representing the deployed application."
  value       = juju_application.superset_k8s
}

output "provides" {
  description = "Provided endpoint objects for cross-charm integration."
  value = {
    for key, endpoint in local.provided_endpoints : key => {
      name     = juju_application.superset_k8s.name
      endpoint = endpoint
    }
  }
}

output "requires" {
  description = "Required endpoint objects for cross-charm integration."
  value = {
    for key, endpoint in local.required_endpoints : key => {
      name     = juju_application.superset_k8s.name
      endpoint = endpoint
    }
  }
}
