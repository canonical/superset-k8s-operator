# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the status the charm reports for its workload."""

from unittest import mock

import pytest
import requests
from ops import ActiveStatus, MaintenanceStatus

from tests.unit.helpers import build_state


def serving(*statuses):
    """Return responses for successive health probes.

    Args:
        statuses: the HTTP status code each probe answers with, or an
            exception for a probe that does not connect at all.

    Returns:
        A `side_effect` for the patched `requests.get`.
    """
    return [
        (
            status
            if isinstance(status, Exception)
            else mock.Mock(status_code=status)
        )
        for status in statuses
    ]


@pytest.fixture(name="probe")
def probe_fixture():
    """Patch the health probe the charm makes against its workload.

    Returns:
        The patched `requests.get`, answering 200 until a test says
        otherwise.
    """
    with mock.patch(
        "charm.requests.get", return_value=mock.Mock(status_code=200)
    ) as get:
        yield get


def test_update_status_is_active_while_superset_answers(ctx, probe):
    """The unit is active for as long as the workload serves."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())

    state_out = ctx.run(ctx.on.update_status(), state_mid)

    assert state_out.unit_status == ActiveStatus()


def test_update_status_reports_a_workload_that_stopped_answering(ctx, probe):
    """A workload that has gone away is reported on the periodic hook."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())
    probe.side_effect = requests.exceptions.ConnectionError()

    state_out = ctx.run(ctx.on.update_status(), state_mid)

    assert state_out.unit_status == MaintenanceStatus("Status check: DOWN")


def test_reconcile_does_not_report_active_over_a_dead_workload(ctx, probe):
    """The reconcile reports the workload, not the plan it just applied.

    Waiting for the next update-status to notice would leave the unit
    reading Active for a whole hook interval while the workload is down.
    """
    probe.side_effect = requests.exceptions.ConnectionError()

    state_out = ctx.run(ctx.on.config_changed(), build_state())

    assert state_out.unit_status == MaintenanceStatus("Status check: DOWN")


def test_reconcile_waits_for_a_workload_that_is_still_starting(ctx, probe):
    """A booting workload is waited on rather than reported as down.

    Pebble reports the service as started as soon as the process runs, and
    Superset answers a request some way after that.
    """
    probe.side_effect = serving(
        requests.exceptions.ConnectionError(), 503, 200
    )

    with mock.patch("charm.WORKLOAD_READY_TIMEOUT", 60), mock.patch(
        "charm.WORKLOAD_READY_POLL", 0
    ):
        state_out = ctx.run(ctx.on.config_changed(), build_state())

    assert probe.call_count == 3
    assert state_out.unit_status == ActiveStatus()


def test_a_workload_that_never_answers_is_reported_down(ctx, probe):
    """The wait is bounded; the pebble check covers the slower workload."""
    probe.side_effect = requests.exceptions.ConnectionError()

    with mock.patch("charm.WORKLOAD_READY_TIMEOUT", 0):
        state_out = ctx.run(ctx.on.config_changed(), build_state())

    assert probe.call_count == 1
    assert state_out.unit_status == MaintenanceStatus("Status check: DOWN")


def test_a_worker_is_active_without_being_asked(ctx, probe):
    """Only the UI functions serve HTTP, so only they are probed."""
    state_in = build_state(config={"charm-function": "worker"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    probe.assert_not_called()
    assert state_out.unit_status == ActiveStatus()
