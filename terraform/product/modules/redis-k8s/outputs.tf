# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "Object representing the deployed application."
  value       = juju_application.redis_k8s
}

output "provides" {
  description = "Map of all the provided endpoints."
  value = {
    redis = {
      name     = juju_application.redis_k8s.name
      endpoint = "redis"
    }
    grafana_dashboard = {
      name     = juju_application.redis_k8s.name
      endpoint = "grafana-dashboard"
    }
    metrics_endpoint = {
      name     = juju_application.redis_k8s.name
      endpoint = "metrics-endpoint"
    }
  }
}

output "requires" {
  description = "Map of all the required endpoints."
  value = {
    logging = {
      name     = juju_application.redis_k8s.name
      endpoint = "logging"
    }
  }
}
