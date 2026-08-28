#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured config unit tests."""

import dataclasses
import logging

import pytest

from tests.unit.helpers import build_state

logger = logging.getLogger(__name__)


def read_config(ctx, state, overrides, field):
    """Return one parsed structured config value for a set of overrides.

    `TypedCharmBase.config` parses and validates the whole model on access,
    so an invalid value raises here rather than at subscript time.

    Args:
        ctx: the Scenario `Context` for the charm.
        state: the base state to override the config of.
        overrides: config options to apply on top of the state's config.
        field: the configuration field to read back.

    Returns:
        The parsed value of the field.
    """
    config = dict(state.config)
    config.update(overrides)
    state = dataclasses.replace(state, config=config)
    with ctx(ctx.on.update_status(), state) as manager:
        return manager.charm.config[field]


def check_valid_values(ctx, state, field, accepted_values):
    """Check the correctness of the passed values for a field.

    Args:
        ctx: the Scenario `Context` for the charm.
        state: the base state to override the config of.
        field: the configuration field to test.
        accepted_values: list of accepted values for this field.
    """
    for value in accepted_values:
        assert read_config(ctx, state, {field: value}, field) == value


def check_invalid_values(ctx, state, field, erroneus_values):
    """Check the incorrectness of the passed values for a field.

    Args:
        ctx: the Scenario `Context` for the charm.
        state: the base state to override the config of.
        field: the configuration field to test.
        erroneus_values: list of invalid values for this field.
    """
    for value in erroneus_values:
        with pytest.raises(ValueError):
            read_config(ctx, state, {field: value}, field)


@pytest.fixture(name="state")
def state_fixture():
    """Return a minimal state carrying the required config."""
    return build_state()


def test_config_parsing_parameters_integer_values(ctx, state) -> None:
    """Check that integer fields are parsed correctly."""
    integer_fields = {
        "sqlalchemy-pool-size": [42, 100, 1],
        "sqlalchemy-pool-timeout": [42, 100, 1],
        "sqlalchemy-max-overflow": [42, 100, 1],
        "webserver-timeout": [60, 170, 300],
        "screenshot-timeout": [1, 600, 2147483647],
        "server-worker-amount": [1, 8, 32],
        "gunicorn-timeout": [30, 120, 600],
        "celery-worker-concurrency": [0, 16, 128],
    }
    erroneus_values = [2147483648, -2147483649]
    for field, valid_values in integer_fields.items():
        if field != "screenshot-timeout":
            check_invalid_values(ctx, state, field, erroneus_values)
        check_valid_values(ctx, state, field, valid_values)


def test_config_parsing_parameters_out_of_range_values(ctx, state) -> None:
    """Check out-of-range values for fields with bounded validators."""
    invalid_ranges = {
        "webserver-timeout": [59, 301],
        "screenshot-timeout": [0, -1],
        "server-worker-amount": [0, 33],
        "gunicorn-timeout": [29, 601],
        "celery-worker-concurrency": [-1, 129],
    }

    for field, invalid_values in invalid_ranges.items():
        check_invalid_values(ctx, state, field, invalid_values)


def test_config_parsing_parameters_boolean_values(ctx, state) -> None:
    """Check that boolean fields are parsed correctly."""
    check_valid_values(ctx, state, "report-dry-run", [True, False])


def test_product_related_values(ctx, state) -> None:
    """Test specific parameters for each field."""
    erroneus_values = ["test-value", "foo", "bar"]

    # charm-function
    check_invalid_values(ctx, state, "charm-function", erroneus_values)
    accepted_values = ["app-gunicorn", "worker", "beat"]
    check_valid_values(ctx, state, "charm-function", accepted_values)


def test_config_feature_flags(ctx, state) -> None:
    """Test feature flags configuration."""
    assert read_config(
        ctx,
        state,
        {"feature-flags": "ALERTS_ATTACH_REPORTS, ALLOW_ADHOC_SUBQUERY"},
        "feature-flags",
    ) == {
        "ALERTS_ATTACH_REPORTS": True,
        "ALLOW_ADHOC_SUBQUERY": True,
    }

    assert read_config(
        ctx,
        state,
        {"feature-flags": "ALERTS_ATTACH_REPORTS, !ALLOW_ADHOC_SUBQUERY"},
        "feature-flags",
    ) == {
        "ALERTS_ATTACH_REPORTS": True,
        "ALLOW_ADHOC_SUBQUERY": False,
    }

    assert read_config(
        ctx,
        state,
        {"feature-flags": "alerts_attach_reports,!allow_adhoc_subquery"},
        "feature-flags",
    ) == {
        "ALERTS_ATTACH_REPORTS": True,
        "ALLOW_ADHOC_SUBQUERY": False,
    }

    with pytest.raises(ValueError) as va:
        read_config(
            ctx,
            state,
            {"feature-flags": "ALERTS_ATTACH_REPORTS, !UNKNOWN"},
            "feature-flags",
        )
    assert "UNKNOWN" in str(va.value)
