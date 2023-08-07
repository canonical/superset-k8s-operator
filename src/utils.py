#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of helper methods for Superset Charm."""

import logging
import os
import secrets
import string

from literals import CONFIG_FILE, CONFIG_PATH, INIT_FILES, INIT_PATH

logger = logging.getLogger(__name__)


def charm_path(file_path):
    """Get path for Charm.

    Args:
        file_path: charm file_path

    Returns:
        path: full charm path
    """
    charm_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir)
    )
    path = os.path.join(charm_dir, file_path)
    return path


def generate_password() -> str:
    """Create randomized string for use as app passwords.

    Returns:
        String of 32 randomized letter+digit characters
    """
    return "".join(
        [
            secrets.choice(string.ascii_letters + string.digits)
            for _ in range(32)
        ]
    )


def read_file(file_path):
    """Read content of a file by path.

    Args:
        file_path: the path of the file

    Returns:
        file_contents: the content of the file as a string
    """
    with open(file_path, "r") as file:
        file_contents = file.read()

    return file_contents


def push_files(container, file_path, destination, permissions):
    """Push files to container destination path.

    Args:
        container: the application container
        file_path: the path of the file
        destination: the destination path in the application
        permissions: the permissions of the file
    """
    abs_path = charm_path(file_path)
    file_content = read_file(abs_path)
    container.push(
        destination, file_content, make_dirs=True, permissions=permissions
    )


def load_superset_files(container):
    """Load files necessary for Superset application to start.

    Args:
        container: the application container
    """
    for file in INIT_FILES:
        if file == CONFIG_FILE:
            path = CONFIG_PATH
        else:
            path = INIT_PATH
        push_files(container, f"templates/{file}", f"{path}/{file}", 0o744)
