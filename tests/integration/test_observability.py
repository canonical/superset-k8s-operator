#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: what a Superset deployment reports to the observability stack.

Each charm function exports what it has to export and nothing else: the UI
and the worker run exporters and advertise scrape targets, and a beat
scheduler reports nothing at all rather than exporting an exporter's own
runtime.
"""

import logging
import time
from typing import Any

import jubilant
import pytest
import requests
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)

ALERT_RULES = ("SupersetDown", "SupersetHalfOrMoreDown", "WorkersDown")
DASHBOARD_TITLE = "Superset Metrics"
LOG_LOOKBACK_SECONDS = 60 * 60
EXPORTING_APPS = (steps.UI_NAME, steps.WORKER_NAME)


def _observe(juju: jubilant.Juju) -> None:
    """Deploy COS and relate every observability endpoint to it.

    Args:
        juju: Jubilant object.
    """
    logger.info("Deploying Prometheus, Loki and Grafana")
    steps.deploy_cos(juju)

    logger.info("Integrating the observability endpoints")
    for app in steps.SUPERSET_APPS:
        juju.integrate(
            f"{app}:grafana-dashboard",
            f"{steps.GRAFANA_NAME}:grafana-dashboard",
        )
        juju.integrate(f"{app}:logging", f"{steps.LOKI_NAME}:logging")

    for app in EXPORTING_APPS:
        juju.integrate(
            f"{app}:metrics-endpoint",
            f"{steps.PROMETHEUS_NAME}:metrics-endpoint",
        )

    steps.wait_for_active(
        juju,
        [*steps.SUPERSET_APPS, *steps.COS_APPS],
        timeout=steps.DEPLOY_TIMEOUT,
    )


@pytest.fixture(scope="module")
def an_observed_deployment(
    request: pytest.FixtureRequest, superset_deployment: jubilant.Juju
) -> jubilant.Juju:
    """Relate the deployment's three observability endpoints to COS.

    Args:
        request: Pytest request object.
        superset_deployment: The active deployment.

    Returns:
        The model, with Prometheus, Loki and Grafana related.
    """
    return steps.adopt_or_build(request, superset_deployment, _observe)


def _prometheus(juju: jubilant.Juju, path: str, **params: str) -> Any:
    """Query the Prometheus API and return the `data` block of its answer.

    Args:
        juju: Jubilant object.
        path: API path below `/api/v1`.
        params: Query parameters.

    Returns:
        The `data` block of the response body.
    """
    url = steps.get_unit_url(
        juju, steps.PROMETHEUS_NAME, 0, steps.PROMETHEUS_PORT
    )
    response = requests.get(
        f"{url}/api/v1/{path}", params=params or None, timeout=30
    )
    response.raise_for_status()
    return response.json()["data"]


def _up_by_application(juju: jubilant.Juju, app: str) -> list[str]:
    """Return the `up` sample values Prometheus holds for an application.

    Args:
        juju: Jubilant object.
        app: The Juju application name.

    Returns:
        One value per scrape target, `"1"` for a target being scraped.
    """
    samples = _prometheus(
        juju, "query", query=f'up{{juju_application="{app}"}}'
    )
    return [sample["value"][1] for sample in samples["result"]]


def test_prometheus_scrapes_the_functions_that_export_metrics(
    an_observed_deployment: jubilant.Juju,
):
    """Scenario: the UI and the worker are scraped and the beat scheduler is not.

    Given a Superset deployment related to Prometheus
    Then Prometheus scrapes the UI and the worker
    And it holds no scrape target for the beat scheduler
    """
    juju = an_observed_deployment

    with given("a Superset deployment related to Prometheus"):
        steps.assert_active(juju, [steps.PROMETHEUS_NAME])

    with then("Prometheus scrapes the UI and the worker"):
        for app in EXPORTING_APPS:
            steps.poll_until(
                juju,
                lambda app=app: "1" in _up_by_application(juju, app),
                f"Prometheus never scraped {app}",
            )

    # `metrics_targets` returns nothing for a beat scheduler, so it never
    # gets a MetricsEndpointProvider and advertises no target at all.
    with and_("it holds no scrape target for the beat scheduler"):
        assert not _up_by_application(juju, steps.BEAT_NAME)


def test_the_charms_alert_rules_are_loaded(
    an_observed_deployment: jubilant.Juju,
):
    """Scenario: the rules the charm ships reach Prometheus.

    Given a Superset deployment related to Prometheus
    When the rules Prometheus has loaded are read
    Then every alert rule the charm ships is among them
    """
    juju = an_observed_deployment
    loaded: set[str] = set()

    with given("a Superset deployment related to Prometheus"):
        steps.assert_active(juju, [steps.PROMETHEUS_NAME])

    with when("the rules Prometheus has loaded are read"):

        def _rules_loaded() -> bool:
            """Return True once every rule the charm ships is loaded."""
            nonlocal loaded
            groups = _prometheus(juju, "rules")["groups"]
            loaded = {
                rule["name"] for group in groups for rule in group["rules"]
            }
            return set(ALERT_RULES).issubset(loaded)

        steps.poll_until(
            juju, _rules_loaded, "Prometheus never loaded the charm's rules"
        )

    with then("every alert rule the charm ships is among them"):
        assert set(ALERT_RULES).issubset(loaded), sorted(loaded)


def test_grafana_holds_the_charms_dashboard(
    an_observed_deployment: jubilant.Juju,
):
    """Scenario: the dashboard the charm ships reaches Grafana.

    Given a Superset deployment related to Grafana
    When Grafana's dashboards are searched
    Then the dashboard the charm ships is among them
    """
    juju = an_observed_deployment
    titles: set[str] = set()

    with given("a Superset deployment related to Grafana"):
        steps.assert_active(juju, [steps.GRAFANA_NAME])

    with when("Grafana's dashboards are searched"):
        task = juju.run(
            f"{steps.GRAFANA_NAME}/0", "get-admin-password", wait=5 * 60
        )
        password = task.results["admin-password"]
        url = steps.get_unit_url(
            juju, steps.GRAFANA_NAME, 0, steps.GRAFANA_PORT
        )

        def _dashboard_loaded() -> bool:
            """Return True once the charm's dashboard is searchable."""
            nonlocal titles
            response = requests.get(
                f"{url}/api/search",
                params={"query": DASHBOARD_TITLE},
                auth=("admin", password),
                timeout=30,
            )
            response.raise_for_status()
            titles = {item["title"] for item in response.json()}
            return DASHBOARD_TITLE in titles

        steps.poll_until(
            juju, _dashboard_loaded, "Grafana never loaded the dashboard"
        )

    with then("the dashboard the charm ships is among them"):
        assert DASHBOARD_TITLE in titles, sorted(titles)


def test_loki_receives_the_workload_logs(
    an_observed_deployment: jubilant.Juju,
):
    """Scenario: the workload's own log lines reach Loki.

    Given a Superset deployment related to Loki
    When Loki is queried for the UI's workload stream
    Then it holds log lines from it
    """
    juju = an_observed_deployment
    streams: list = []

    with given("a Superset deployment related to Loki"):
        steps.assert_active(juju, [steps.LOKI_NAME])

    with when("Loki is queried for the UI's workload stream"):
        url = steps.get_unit_url(juju, steps.LOKI_NAME, 0, steps.LOKI_PORT)
        query = (
            f'{{juju_application="{steps.UI_NAME}",'
            f'pebble_service="{steps.WORKLOAD_SERVICE}"}}'
        )

        def _streams_arrived() -> bool:
            """Return True once Loki holds a matching stream."""
            nonlocal streams
            now = time.time_ns()
            response = requests.get(
                f"{url}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(now - LOG_LOOKBACK_SECONDS * 1_000_000_000),
                    "end": str(now),
                    "limit": "5",
                    "direction": "backward",
                },
                timeout=30,
            )
            response.raise_for_status()
            streams = response.json()["data"]["result"]
            return bool(streams)

        steps.poll_until(
            juju,
            _streams_arrived,
            f"Loki never received a stream matching {query}",
        )

    with then("it holds log lines from it"):
        assert streams, f"no stream matched {query}"
        logger.info("Loki holds %d matching stream(s)", len(streams))
