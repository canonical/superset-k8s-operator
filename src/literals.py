#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Literals used by the Superset K8s charm."""

APPLICATION_PORT = 8088
DB_NAME = "superset"
DB_RELATION_NAME = "postgresql_db"
REDIS_RELATION_NAME = "redis"
TRINO_CATALOG_RELATION_NAME = "trino-catalog"
INGRESS_RELATION_NAME = "ingress"
OAUTH_RELATION_NAME = "oauth"
OAUTH_CALLBACK_PATH = "/oauth-authorized/oidc"
OAUTH_SCOPE = "openid email profile"
OAUTH_GRANT_TYPES = ["authorization_code"]
CERTIFICATES_RELATION_NAME = "certificates"

# TLS certificate delivery paths inside the workload container.
# The CA received over the `certificates` relation is installed into the
# system trust store (so outbound TLS to e.g. Kyuubi/Hive validates) and is
# also written to a stable PEM path that can be referenced from a Superset
# database connection's `connect_args.ssl_cert`. Changing CA_CERT_PATH is a
# breaking change for users: it is documented in the "Trust a certificate
# authority" how-to in the Canonical Data Mesh documentation.
CA_CERT_LOCAL_PATH = "/usr/local/share/ca-certificates/juju-charm-ca.crt"
CA_CERT_PATH = "/etc/ssl/certs/charm-ca.pem"
SUPERSET_VERSION = "6.1.0"

# Juju secret holding the signing keys shared by every application of one
# deployment. It is user-supplied because a charm-generated secret would be
# per application, and because the secret key outlives the charm: it encrypts
# the database connection passwords stored in the metadata database.
SIGNING_KEYS_SECRET_KEYS = ("secret-key", "async-queries-jwt")
ADMIN_SECRET_LABEL = "superset-admin-password"  # nosec B105
ADMIN_SECRET_ID_FIELD = "admin-password-secret-id"  # nosec B105
ADMIN_SECRET_KEY = "password"  # nosec B105
REDIS_KEY_PREFIX = "superset_results"
APP_NAME = "superset"
CONFIG_FILES = [
    "superset_config.py",
    "custom_security_manager.py",
    "sentry_interceptor.py",
    "permission_error_messages.py",
]
CONFIG_PATH = "/app/pythonpath"
UI_FUNCTIONS = ["app", "app-gunicorn"]
SQL_AB_ROLE = "SELECT name FROM ab_role;"
HEALTH_URL = f"http://localhost:{APPLICATION_PORT}/health"
HEALTH_PROBE_TIMEOUT = 5

# Observability literals
LOG_FILE = "/var/log/superset.log"
PROMETHEUS_METRICS_PORT = 9102
STATSD_PORT = 9125
