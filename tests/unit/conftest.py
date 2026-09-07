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

    Redis relation data, the metadata database query and the workload health
    probe are patched for the duration of the test: all three reach outside
    the charm, to the Redis library, to a live PostgreSQL connection and to
    the workload's HTTP endpoint respectively.
    """
    with mock.patch(
        "charm.Redis.get_redis_relation_data",
        return_value=("redis-host", 6379),
    ), mock.patch(
        "charm.query_metadata_database",
        return_value=["Public", "Gamma", "Alpha", "Admin"],
    ), mock.patch(
        "charm.requests.get", return_value=mock.Mock(status_code=200)
    ):
        yield Context(
            SupersetK8SCharm,
            meta=_load("metadata.yaml"),
            config=_load("config.yaml"),
            actions=_load("actions.yaml"),
        )
