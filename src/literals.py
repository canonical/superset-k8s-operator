#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Literals used by the Superset K8s charm."""

APPLICATION_PORT = 8088
DB_NAME = "superset"
SUPERSET_VERSION = "6.0.0"
REDIS_KEY_PREFIX = "superset_results"
APP_NAME = "superset"
CONFIG_FILES = [
    "superset_config.py",
    "custom_sso_security_manager.py",
    "sentry_interceptor.py",
]
CONFIG_PATH = "/app/pythonpath"
UI_FUNCTIONS = ["app", "app-gunicorn"]
DEFAULT_ROLES = ["Public", "Gamma", "Alpha", "Admin"]
SQL_AB_ROLE = "SELECT name FROM ab_role;"

# Observability literals
LOG_FILE = "/var/log/superset.log"
PROMETHEUS_METRICS_PORT = 9102
STATSD_PORT = 9125
