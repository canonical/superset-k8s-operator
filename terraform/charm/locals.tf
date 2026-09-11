# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  provided_endpoints = {
    grafana_dashboard = "grafana-dashboard"
    metrics_endpoint  = "metrics-endpoint"
  }

  required_endpoints = {
    certificates  = "certificates"
    ingress       = "ingress"
    logging       = "logging"
    oauth         = "oauth"
    postgresql_db = "postgresql_db"
    redis         = "redis"
    smtp          = "smtp"
    trino_catalog = "trino-catalog"
  }
}
