# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the metrics each charm function exports."""

import dataclasses

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


def test_mcp_opens_its_configured_port(ctx):
    """The mcp function opens its own configured port, not the fixed UI one.

    mcp is in METRICS_FUNCTIONS like the UI, so it also opens the Prometheus
    scrape port.
    """
    state_in = build_state(
        config={
            "charm-function": "mcp",
            "mcp-dev-username": "admin",
            "mcp-service-port": 6000,
        }
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.opened_ports == {TCPPort(6000), TCPPort(9102)}


def test_mcp_keeps_its_statsd_sink(ctx):
    """mcp is treated exactly like the UI: statsd_exporter, no celery-exporter."""
    state_in = build_state(
        config={"charm-function": "mcp", "mcp-dev-username": "admin"}
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    services = state_out.get_container("superset").plan.to_dict()["services"]
    assert services["metrics-exporter"]["command"] == "/usr/bin/statsd_exporter"
    assert "celery-exporter" not in services


def test_a_reconfigured_function_stops_advertising_its_old_ports(ctx):
    """The port set is replaced, not added to.

    A unit reconfigured from one function to another would otherwise keep
    advertising the ports of the function it replaced, since nothing closes
    a port once it has been opened.
    """
    ui = ctx.run(ctx.on.config_changed(), build_state())
    assert TCPPort(8088) in ui.opened_ports

    worker = ctx.run(
        ctx.on.config_changed(),
        dataclasses.replace(
            ui, config={**ui.config, "charm-function": "worker"}
        ),
    )

    assert worker.opened_ports == {TCPPort(9102), TCPPort(9103)}
