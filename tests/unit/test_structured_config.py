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
    # Provide required config before initializing charm
    _harness.update_config({"superset-secret-key": "example-pass"})
    _harness.begin_with_initial_hooks()
    return _harness


def test_config_parsing_parameters_integer_values(_harness) -> None:
    """Check that integer fields are parsed correctly."""
    integer_fields = {
        "sqlalchemy-pool-size": [42, 100, 1],
        "sqlalchemy-pool-timeout": [42, 100, 1],
        "sqlalchemy-max-overflow": [42, 100, 1],
        "webserver-timeout": [60, 170, 300],
        "server-worker-amount": [1, 8, 32],
        "gunicorn-timeout": [30, 120, 600],
        "celery-worker-concurrency": [0, 16, 128],
    }
    erroneus_values = [2147483648, -2147483649]
    for field, valid_values in integer_fields.items():
        check_invalid_values(_harness, field, erroneus_values)
        check_valid_values(_harness, field, valid_values)


def test_config_parsing_parameters_out_of_range_values(_harness) -> None:
    """Check out-of-range values for fields with bounded validators."""
    invalid_ranges = {
        "webserver-timeout": [59, 301],
        "server-worker-amount": [0, 33],
        "gunicorn-timeout": [29, 601],
        "celery-worker-concurrency": [-1, 129],
    }

    for field, invalid_values in invalid_ranges.items():
        check_invalid_values(_harness, field, invalid_values)


def test_product_related_values(_harness) -> None:
    """Test specific parameters for each field."""
    erroneus_values = ["test-value", "foo", "bar"]

    # charm-function
    check_invalid_values(_harness, "charm-function", erroneus_values)
    accepted_values = ["app-gunicorn", "worker", "beat"]
    check_valid_values(_harness, "charm-function", accepted_values)


def test_config_feature_flags(_harness) -> None:
    """Test feature flags configuration."""
    _harness.update_config(
        {"feature-flags": "ALERTS_ATTACH_REPORTS, ALLOW_ADHOC_SUBQUERY"}
    )
    assert _harness.charm.config["feature-flags"] == {
        "ALERTS_ATTACH_REPORTS": True,
        "ALLOW_ADHOC_SUBQUERY": True,
    }

    _harness.update_config(
        {"feature-flags": "ALERTS_ATTACH_REPORTS, !ALLOW_ADHOC_SUBQUERY"}
    )
    assert _harness.charm.config["feature-flags"] == {
        "ALERTS_ATTACH_REPORTS": True,
        "ALLOW_ADHOC_SUBQUERY": False,
    }

    _harness.update_config(
        {"feature-flags": "alerts_attach_reports,!allow_adhoc_subquery"}
    )
    assert _harness.charm.config["feature-flags"] == {
        "ALERTS_ATTACH_REPORTS": True,
        "ALLOW_ADHOC_SUBQUERY": False,
    }

    _harness.update_config(
        {"feature-flags": "ALERTS_ATTACH_REPORTS, !UNKNOWN"}
    )
    with pytest.raises(ValueError) as va:
        _ = _harness.charm.config["feature-flags"]
    assert "UNKNOWN" in str(va.value)


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
