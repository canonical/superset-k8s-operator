#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Metrics wiring for each of the charm functions."""

from literals import (
    CELERY_METRICS_PORT,
    METRICS_FUNCTIONS,
    PROMETHEUS_METRICS_PORT,
    STATSD_PORT,
    UI_FUNCTION,
    WORKER_FUNCTION,
)


def metrics_targets(function):
    """Return the scrape targets a charm function exposes.

    Args:
        function: the `charm-function` value.

    Returns:
        The targets to advertise, empty for a function that exports nothing.
    """
    if function not in METRICS_FUNCTIONS:
        return []

    targets = [f"*:{PROMETHEUS_METRICS_PORT}"]
    if function == WORKER_FUNCTION:
        targets.append(f"*:{CELERY_METRICS_PORT}")
    return targets


def metrics_services(function, after, redis_hostname, redis_port):
    """Return the exporter services a charm function runs.

    A worker carries a statsd_exporter like the UI does, because the report
    and prune task bodies it runs report through `STATS_LOGGER`, and running
    celery-exporter in its place leaves nothing listening on the statsd port
    and drops every one of those counters. It carries celery-exporter as well,
    on a port of its own, because task events are only visible on the broker.
    A beat scheduler reports nothing, so it is given no exporter rather than
    one that would only ever export the exporter's own runtime.

    Args:
        function: the `charm-function` value.
        after: the workload service the exporters start after.
        redis_hostname: the Redis host celery-exporter reads events from.
        redis_port: the Redis port celery-exporter reads events from.

    Returns:
        The exporter services, empty for a beat scheduler.
    """
    if function not in METRICS_FUNCTIONS:
        return {}

    services = {
        "metrics-exporter": {
            "override": "replace",
            "summary": "metrics exporter",
            "command": "/usr/bin/statsd_exporter",
            "startup": "enabled",
            "after": [after],
        }
    }

    if function != WORKER_FUNCTION:
        return services

    services["celery-exporter"] = {
        "override": "replace",
        "summary": "celery exporter",
        "command": (
            "/usr/bin/celery-exporter --broker-url "
            f"redis://{redis_hostname}:{redis_port}/4 "
            f"--port {CELERY_METRICS_PORT}"
        ),
        "startup": "enabled",
        "after": [after],
    }

    return services


def open_metrics_ports(unit, function):
    """Open the ports a charm function exposes its metrics on.

    Args:
        unit: the unit to open the ports on.
        function: the `charm-function` value.
    """
    if function not in METRICS_FUNCTIONS:
        return

    unit.open_port(port=PROMETHEUS_METRICS_PORT, protocol="tcp")

    if function == WORKER_FUNCTION:
        unit.open_port(port=CELERY_METRICS_PORT, protocol="tcp")

    if function == UI_FUNCTION:
        # The port statsd_exporter accepts the workload's metrics on.
        unit.open_port(port=STATSD_PORT, protocol="udp")
