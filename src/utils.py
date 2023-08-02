#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of helper methods for Superset Charm."""

import logging
import os
import secrets
import string

logger = logging.getLogger(__name__)


def charm_path(directory):
    """Get path for Charm.

    Args:
        directory: charm directory

    Returns:
        path: full charm path
    """
    charm_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir)
    )
    path = os.path.join(charm_dir, directory)
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
