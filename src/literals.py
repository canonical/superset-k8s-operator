#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Literals used by the Superset K8s charm."""

APPLICATION_PORT = 8088
APP_NAME = "superset"
CONFIG_FILE = "superset_config.py"
CONFIG_PATH = "/app/pythonpath"
INIT_PATH = "/app/k8s"
INIT_FILES = [
    "k8s-bootstrap.sh",
    "k8s-init.sh",
    "requirements-local.txt",
    "superset_config.py",
]
UI_FUNCTIONS = ["app", "gunicorn"]
