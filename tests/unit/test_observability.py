# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the metrics each charm function exports."""

from ops.testing import Relation, TCPPort, UDPPort

from tests.unit.helpers import build_state


def test_observability_pebble_layer(ctx):
    """The metrics exporter service is part of the generated plan."""
    state_out = ctx.run(ctx.on.config_changed(), build_state())

    plan = state_out.get_container("superset").plan.to_dict()
    assert plan["services"]["metrics-exporter"] == {
        "override": "replace",
        "summary": "metrics exporter",
        "command": "/usr/bin/statsd_exporter",
        "startup": "enabled",
        "after": ["superset"],
    }


def test_a_worker_keeps_its_statsd_sink(ctx):
    """A worker runs the task bodies that report through `STATS_LOGGER`.

    Running celery-exporter in place of statsd_exporter leaves nothing
    listening on the statsd port, which drops every counter the report and
    prune tasks emit, so a worker runs both on ports of their own.
    """
    state_in = build_state(config={"charm-function": "worker"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    services = state_out.get_container("superset").plan.to_dict()["services"]
    assert (
        services["metrics-exporter"]["command"] == "/usr/bin/statsd_exporter"
    )
    assert services["celery-exporter"]["command"] == (
        "/usr/bin/celery-exporter --broker-url redis://redis-host:6379/4 "
        "--port 9103"
    )


def test_a_beat_scheduler_runs_no_exporter(ctx):
    """A beat scheduler has nothing to export.

    It dispatches to the broker and runs no task body, so an exporter there
    would only ever export its own runtime.
    """
    state_in = build_state(config={"charm-function": "beat"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    services = state_out.get_container("superset").plan.to_dict()["services"]
    assert set(services) == {"superset"}


def test_a_beat_scheduler_advertises_no_scrape_target(ctx):
    """A function with nothing to export must not present a dead target."""
    relation = Relation("metrics-endpoint")
    state_in = build_state(
        config={"charm-function": "beat"}, extra_relations=(relation,)
    )

    state_out = ctx.run(ctx.on.relation_changed(relation), state_in)

    assert state_out.get_relation(relation.id).local_app_data == {}


def test_each_function_opens_the_ports_it_serves_on(ctx):
    """The ports follow what the function actually exposes."""
    ui = ctx.run(ctx.on.config_changed(), build_state())
    worker = ctx.run(
        ctx.on.config_changed(),
        build_state(config={"charm-function": "worker"}),
    )
    beat = ctx.run(
        ctx.on.config_changed(),
        build_state(config={"charm-function": "beat"}),
    )

    assert ui.opened_ports == {
        TCPPort(8088),
        TCPPort(9102),
        UDPPort(9125),
    }
    assert worker.opened_ports == {TCPPort(9102), TCPPort(9103)}
    assert beat.opened_ports == set()
