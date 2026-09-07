# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the status the charm reports for its workload."""

import dataclasses
from unittest import mock

import pytest
import requests
from ops import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.testing import CheckInfo

from tests.unit.helpers import build_state, superset_container

WAITING_ON_THE_UI = WaitingStatus(
    "waiting for the UI to initialise the database"
)


@pytest.fixture(name="uninitialised_database")
def uninitialised_database_fixture():
    """Patch the metadata database as carrying no Superset roles yet.

    The `ctx` fixture answers the role query with a migrated database, which
    is what every other test wants; this is the state before a UI application
    has run its migrations.
    """
    with mock.patch("charm.query_metadata_database", return_value=[]) as query:
        yield query


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

    assert state_out.unit_status == ActiveStatus("Status check: UP")


def test_update_status_reports_a_workload_that_stopped_answering(ctx, probe):
    """A workload that has gone away is reported on the periodic hook."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())
    probe.side_effect = requests.exceptions.ConnectionError()

    state_out = ctx.run(ctx.on.update_status(), state_mid)

    assert state_out.unit_status == MaintenanceStatus("Status check: DOWN")


def test_reconcile_does_not_report_active_over_a_dead_workload(ctx, probe):
    """The status reports the workload, not the plan just applied to it.

    Pebble calls a service started as soon as its process runs, so taking
    the plan at its word would report Active over a workload that never
    answers.
    """
    probe.side_effect = requests.exceptions.ConnectionError()

    state_out = ctx.run(ctx.on.config_changed(), build_state())

    assert state_out.unit_status == MaintenanceStatus("Status check: DOWN")


def test_the_workload_is_asked_once_per_event(ctx, probe):
    """The status is collected without waiting on the workload.

    Nothing polls: the reconcile applies the plan and the collector asks the
    workload once, so a hook costs one request whatever the answer is.
    """
    probe.side_effect = requests.exceptions.ConnectionError()

    ctx.run(ctx.on.config_changed(), build_state())

    assert probe.call_count == 1


def test_a_check_failure_reports_the_workload_down(ctx, probe):
    """A tripped pebble check is what reports a workload dying between hooks.

    Juju only dispatches this when the check crosses its threshold, so it is
    the charm's notice that the workload stopped answering since the last
    hook.
    """
    state_mid = ctx.run(ctx.on.config_changed(), build_state())
    probe.side_effect = requests.exceptions.ConnectionError()

    state_out = ctx.run(
        ctx.on.pebble_check_failed(
            state_mid.get_container("superset"), CheckInfo("up")
        ),
        state_mid,
    )

    assert state_out.unit_status == MaintenanceStatus("Status check: DOWN")


def test_a_recovered_check_returns_the_unit_to_active(ctx, probe):
    """A workload that comes back is reported without waiting for a hook."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())

    state_out = ctx.run(
        ctx.on.pebble_check_recovered(
            state_mid.get_container("superset"), CheckInfo("up")
        ),
        state_mid,
    )

    assert state_out.unit_status == ActiveStatus("Status check: UP")


def test_the_health_check_trips_on_a_single_failure(ctx, probe):
    """The check exists to wake the charm, so it must not debounce.

    Juju fires a check event only when the check crosses its threshold, so a
    workload that recovers before crossing it fires nothing leaving the status
    unchanged until the next update-status. Tripping on one failure is safe:
    the status comes from the probe and `on-check-failure` is `ignore`.
    """
    state_out = ctx.run(ctx.on.config_changed(), build_state())

    plan = state_out.get_container("superset").plan.to_dict()
    assert plan["checks"]["up"]["threshold"] == 1
    assert plan["services"]["superset"]["on-check-failure"] == {"up": "ignore"}


def test_a_worker_is_active_without_being_asked(ctx, probe):
    """Only the UI functions serve HTTP, so only they are probed."""
    state_in = build_state(config={"charm-function": "worker"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    probe.assert_not_called()
    assert state_out.unit_status == ActiveStatus("Status check: UP")


def test_an_unreachable_container_waits(ctx, probe):
    """A workload container that has not come up yet resolves on its own."""
    container = dataclasses.replace(superset_container(), can_connect=False)
    state_in = build_state(container=container)

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == WaitingStatus(
        "waiting for superset container"
    )


def test_status_is_reported_on_an_event_with_no_handler(ctx, probe):
    """The status does not depend on a reconcile having run.

    It is collected at the end of every dispatch from what the model says,
    so an event the charm does not observe at all still reports the truth.
    """
    state_in = build_state(with_database=False, with_redis=False)

    state_out = ctx.run(ctx.on.install(), state_in)

    assert state_out.unit_status == BlockedStatus(
        "Required relations missing: PostgreSQL, Redis"
    )


def test_collecting_the_status_creates_no_secret(ctx, probe):
    """Reporting the status never has a side effect on the model.

    The admin password is generated by the reconcile, so an event that only
    collects a status must leave the peer databag alone.
    """
    state_in = build_state(with_database=False)

    state_out = ctx.run(ctx.on.start(), state_in)

    peer = [
        relation
        for relation in state_out.relations
        if relation.endpoint == "peer"
    ][0]
    assert "admin-password-secret-id" not in peer.local_app_data


@pytest.mark.parametrize("function", ["worker", "beat"])
def test_a_celery_function_waits_for_the_database_to_be_migrated(
    ctx, probe, uninitialised_database, function
):
    """A function that does not migrate waits for the one that does.

    Each application reaches the metadata database as its own PostgreSQL user,
    and a table belongs to whichever user created it, so a worker that lets
    Flask-AppBuilder create the `ab_*` tables first leaves the UI unable to
    migrate them for good. Relating in any order has to be safe.
    """
    state_in = build_state(config={"charm-function": function})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == WAITING_ON_THE_UI


def test_a_waiting_function_plans_no_workload(
    ctx, probe, uninitialised_database
):
    """Waiting is not enough: the workload must not be started either.

    A status is a report; what keeps the schema the UI's to create is that
    the worker has no service in its pebble plan to touch the database with.
    """
    state_in = build_state(config={"charm-function": "worker"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.get_container("superset").plan.to_dict() == {}


def test_a_worker_starts_once_the_roles_are_readable(ctx, probe):
    """The roles the migration writes are what releases the worker."""
    state_in = build_state(config={"charm-function": "worker"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == ActiveStatus("Status check: UP")
    assert "superset" in state_out.get_container("superset").plan.services


def test_a_ui_does_not_wait_on_the_database_it_migrates(
    ctx, probe, uninitialised_database
):
    """The UI is the one that runs the migrations, so it cannot wait on them."""
    state_out = ctx.run(ctx.on.config_changed(), build_state())

    assert state_out.unit_status == ActiveStatus("Status check: UP")


def test_the_periodic_hook_releases_a_waiting_worker(
    ctx, probe, uninitialised_database
):
    """Nothing wakes a worker when the UI finishes, so update-status retries."""
    state_mid = ctx.run(
        ctx.on.config_changed(),
        build_state(config={"charm-function": "worker"}),
    )
    assert state_mid.unit_status == WAITING_ON_THE_UI
    uninitialised_database.return_value = ["Public", "Admin"]

    state_out = ctx.run(ctx.on.update_status(), state_mid)

    assert state_out.unit_status == ActiveStatus("Status check: UP")
