#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured config unit tests."""

import logging

import pytest
from ops.testing import Harness

from charm import SupersetK8SCharm

logger = logging.getLogger(__name__)


@pytest.fixture
def _harness():
    """Harness setup for tests."""
    _harness = Harness(SupersetK8SCharm)
    _harness.begin_with_initial_hooks()
    return _harness


def test_config_parsing_parameters_integer_values(_harness) -> None:
    """Check that integer fields are parsed correctly."""
    integer_fields = [
        "sqlalchemy-pool-size",
        "sqlalchemy-pool-timeout",
        "sqlalchemy-max-overflow",
        "webserver-timeout",
    ]
    erroneus_values = [2147483648, -2147483649]
    valid_values = [42, 100, 1]
    webserver_timeout_valid_values = [60, 170, 300]
    for field in integer_fields:
        if field == "webserver-timeout":
            valid_values = webserver_timeout_valid_values
        check_invalid_values(_harness, field, erroneus_values)
        check_valid_values(_harness, field, valid_values)


def test_product_related_values(_harness) -> None:
    """Test specific parameters for each field."""
    erroneus_values = ["test-value", "foo", "bar"]

    # charm-function
    check_invalid_values(_harness, "charm-function", erroneus_values)
    accepted_values = ["app-gunicorn", "worker", "beat"]
    check_valid_values(_harness, "charm-function", accepted_values)


def check_valid_values(_harness, field: str, accepted_values: list) -> None:
    """Check the correctness of the passed values for a field.

    Args:
        _harness: Harness object.
        field: The configuration field to test.
        accepted_values: List of accepted values for this field.
    """
    for value in accepted_values:
        _harness.update_config({field: value})
        assert _harness.charm.config[field] == value


def check_invalid_values(_harness, field: str, erroneus_values: list) -> None:
    """Check the incorrectness of the passed values for a field.

    Args:
        _harness: Harness object.
        field: The configuration field to test.
        erroneus_values: List of invalid values for this field.
    """
    for value in erroneus_values:
        _harness.update_config({field: value})
        with pytest.raises(ValueError):
            _ = _harness.charm.config[field]
