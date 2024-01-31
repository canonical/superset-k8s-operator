#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Literals used by the Superset K8s charm."""

APPLICATION_PORT = 8088
APP_NAME = "superset"
CONFIG_FILES = [
    "superset_config.py",
    "custom_sso_security_manager.py",
    "sentry_interceptor.py",
]
CONFIG_PATH = "/app/pythonpath"
UI_FUNCTIONS = ["app", "app-gunicorn"]
