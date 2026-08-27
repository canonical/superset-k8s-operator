# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared fixtures for the Scenario-based unit tests."""

import pathlib
from unittest import mock

import pytest
import yaml
from ops.testing import Context

from charm import SupersetK8SCharm

_CHARM_ROOT = pathlib.Path(__file__).parents[2]


def _load(name: str) -> dict:
    """Load one of the charm's YAML definition files.

    Args:
        name: file name relative to the charm root.

    Returns:
        The parsed mapping.
    """
    return yaml.safe_load((_CHARM_ROOT / name).read_text())


@pytest.fixture
def ctx():
    """Return a Scenario `Context` for the Superset charm.

    Redis relation data and the metadata database query are patched for the
    duration of the test: both reach outside the charm, to the Redis library
    and to a live PostgreSQL connection respectively.
    """
    with mock.patch(
        "charm.Redis.get_redis_relation_data",
        return_value=("redis-host", 6379),
    ), mock.patch(
        "charm.query_metadata_database",
        return_value=["Public", "Gamma", "Alpha", "Admin"],
    ):
        yield Context(
            SupersetK8SCharm,
            meta=_load("metadata.yaml"),
            config=_load("config.yaml"),
            actions=_load("actions.yaml"),
        )
