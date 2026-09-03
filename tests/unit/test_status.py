# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the status the charm reports for its workload."""

import dataclasses

from ops import ActiveStatus, MaintenanceStatus
from ops.pebble import CheckStatus
from ops.testing import CheckInfo

from tests.unit.helpers import build_state


def with_check(state, status, successes=1):
    """Return the state with a pebble check reported on the container.

    The check can only be declared once the plan the charm applied carries
    it, so this is applied to the state a first reconcile produced.

    Args:
        state: a state whose plan already declares the `up` check.
        status: the status the check reports.
        successes: how many times the check has passed since it started.

    Returns:
        A new `State` whose container reports the check.
    """
    container = dataclasses.replace(
        state.get_container("superset"),
        check_infos={CheckInfo("up", status=status, successes=successes)},
    )
    return dataclasses.replace(state, containers={container})


def test_update_status_up(ctx):
    """The charm updates the unit status to active based on UP status."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())

    state_out = ctx.run(
        ctx.on.update_status(), with_check(state_mid, CheckStatus.UP)
    )

    assert state_out.unit_status == ActiveStatus()


def test_update_status_down(ctx):
    """The charm reports maintenance when the pebble check is DOWN."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())

    state_out = ctx.run(
        ctx.on.update_status(), with_check(state_mid, CheckStatus.DOWN)
    )

    assert state_out.unit_status == MaintenanceStatus("Status check: DOWN")


def test_reconcile_reports_a_failing_health_check(ctx):
    """A check reporting DOWN is surfaced by the reconcile, not hidden.

    Waiting for the next update-status to notice would leave the unit
    reading Active for a whole hook interval while the workload is down.
    """
    state_mid = ctx.run(ctx.on.config_changed(), build_state())

    state_out = ctx.run(
        ctx.on.config_changed(), with_check(state_mid, CheckStatus.DOWN)
    )

    assert state_out.unit_status == MaintenanceStatus("Status check: DOWN")


def test_reconcile_is_active_while_the_check_is_up(ctx):
    """A check that has not tripped leaves the unit Active."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())

    state_out = ctx.run(
        ctx.on.config_changed(), with_check(state_mid, CheckStatus.UP)
    )

    assert state_out.unit_status == ActiveStatus()


def test_a_check_that_has_not_passed_yet_is_not_active(ctx):
    """A check reads UP before it has run, which is not yet health.

    The replan restarts the check, so for the first probe period the only
    thing UP means is that the workload has not failed the check three
    times yet, which is true of a workload still booting.
    """
    state_mid = ctx.run(ctx.on.config_changed(), build_state())
    state_in = with_check(state_mid, CheckStatus.UP, successes=0)

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == MaintenanceStatus("Status check: DOWN")
