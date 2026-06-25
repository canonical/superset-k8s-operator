#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Literals used by the Superset K8s charm."""

APPLICATION_PORT = 8088
DB_NAME = "superset"
DB_RELATION_NAME = "postgresql_db"
REDIS_RELATION_NAME = "redis"
TRINO_CATALOG_RELATION_NAME = "trino-catalog"
CERTIFICATES_RELATION_NAME = "certificates"

# TLS certificate delivery paths inside the workload container.
# The CA received over the `certificates` relation is installed into the
# system trust store (so outbound TLS to e.g. Kyuubi/Hive validates) and is
# also written to a stable PEM path that can be referenced from a Superset
# database connection's `connect_args.ssl_cert`.
CA_CERT_LOCAL_PATH = "/usr/local/share/ca-certificates/juju-charm-ca.crt"
CA_CERT_PATH = "/etc/ssl/certs/charm-ca.pem"
SUPERSET_VERSION = "6.1.0"
REDIS_KEY_PREFIX = "superset_results"
APP_NAME = "superset"
CONFIG_FILES = [
    "superset_config.py",
    "custom_sso_security_manager.py",
    "sentry_interceptor.py",
    "permission_error_messages.py",
]
CONFIG_PATH = "/app/pythonpath"
UI_FUNCTIONS = ["app", "app-gunicorn"]
DEFAULT_ROLES = ["Public", "Gamma", "Alpha", "Admin"]
SQL_AB_ROLE = "SELECT name FROM ab_role;"

# Observability literals
LOG_FILE = "/var/log/superset.log"
PROMETHEUS_METRICS_PORT = 9102
STATSD_PORT = 9125
