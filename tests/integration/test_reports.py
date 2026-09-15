#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: rendering alerts and reports.

`report-dry-run` renders a report's screenshot and delivers nothing, so these
scenarios need no mail server and no `smtp` relation. What they exercise is
the rendering pipeline itself: the worker's Playwright screenshot, the
timeout it is bounded by, and the beat scheduler that dispatches it.
"""

import logging
import shlex
import time
import uuid
from typing import Any, Mapping, Optional

import jubilant
import pytest
import requests
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)

REPORT_APPS = steps.SUPERSET_APPS
POLL_INTERVAL = 5
REPORT_TIMEOUT = 300
LOG_FILE = "/var/log/superset.log"


def worker_ssh(
    juju: jubilant.Juju,
    command: str,
    environment: Optional[Mapping[str, str]] = None,
) -> str:
    """Run a command in the worker workload container.

    The command's own failure is not an error here: a report that times out
    exits non-zero and its output is exactly what the scenario asserts on.

    Args:
        juju: Jubilant object.
        command: Shell command to execute.
        environment: Environment variables to provide to the command.

    Returns:
        The command's combined standard output and standard error.
    """
    if environment:
        assignments = " ".join(
            f"{key}={shlex.quote(value)}" for key, value in environment.items()
        )
        command = f"env {assignments} {command}"

    try:
        return juju.ssh(
            f"{steps.WORKER_NAME}/0",
            command,
            container=steps.WORKLOAD_CONTAINER,
        )
    except jubilant.CLIError as exc:
        return f"{exc.stdout or ''}\n{exc.stderr or ''}"


def configure_reports(
    juju: jubilant.Juju,
    *,
    screenshot_timeout: int,
    global_async_queries: bool,
) -> None:
    """Configure every report component and wait for it to settle.

    Args:
        juju: Jubilant object.
        screenshot_timeout: Screenshot renderer timeout in seconds.
        global_async_queries: Whether to enable `GLOBAL_ASYNC_QUERIES`.
    """
    flags = ["ALERT_REPORTS"]
    if global_async_queries:
        flags.append("GLOBAL_ASYNC_QUERIES")

    steps.set_config(
        juju,
        REPORT_APPS,
        {
            "feature-flags": ",".join(flags),
            "report-dry-run": "true",
            "screenshot-timeout": str(screenshot_timeout),
        },
    )


def assert_worker_configuration(
    juju: jubilant.Juju, screenshot_timeout: int
) -> None:
    """Assert the charm's values reached both the plan and Superset's config.

    Args:
        juju: Jubilant object.
        screenshot_timeout: Expected screenshot timeout in seconds.
    """
    environment = steps.workload_environment(juju, f"{steps.WORKER_NAME}/0")
    assert environment.get("SCREENSHOT_TIMEOUT") == str(screenshot_timeout)
    assert environment.get("ALERT_REPORTS_DRY_RUN") == "true"

    loaded = worker_ssh(
        juju,
        'python3 -c "from superset.app import create_app; '
        "app = create_app(); "
        "print(app.config['SCREENSHOT_PLAYWRIGHT_DEFAULT_TIMEOUT']); "
        "print(app.config['ALERT_REPORTS_NOTIFICATION_DRY_RUN'])\"",
        environment,
    )
    assert loaded.splitlines()[-2:] == [
        str(screenshot_timeout * 1000),
        "True",
    ], loaded


def create_chart_report(
    session: requests.Session,
    url: str,
    chart_id: int,
    name: str,
    *,
    active: bool,
) -> int:
    """Create a PNG report schedule for a chart.

    Args:
        session: Authenticated Superset API session.
        url: Superset base URL.
        chart_id: Chart to render.
        name: Unique report schedule name.
        active: Whether Celery beat should dispatch the schedule itself.

    Returns:
        The created report schedule identifier.
    """
    return steps.api_post(
        session,
        url,
        "/api/v1/report/",
        {
            "active": active,
            "chart": chart_id,
            "crontab": "* * * * *",
            "name": name,
            "recipients": [
                {
                    "type": "Email",
                    "recipient_config_json": {
                        "target": "reports@example.invalid"
                    },
                }
            ],
            "report_format": "PNG",
            "type": "Report",
        },
    )


def execute_report(juju: jubilant.Juju, report_id: int) -> str:
    """Run one report synchronously, without waiting for Celery beat.

    A concrete `scheduled_dttm` is supplied because that execution log column
    is not nullable.

    Args:
        juju: Jubilant object.
        report_id: Report schedule identifier to execute.

    Returns:
        The output emitted while executing the report.
    """
    environment = steps.workload_environment(juju, f"{steps.WORKER_NAME}/0")
    command = (
        'python3 -c "from datetime import datetime; '
        "from uuid import uuid4; "
        "from superset.app import create_app; "
        "app = create_app(); "
        "app.app_context().push(); "
        "from superset.commands.report.execute import "
        "AsyncExecuteReportScheduleCommand; "
        "AsyncExecuteReportScheduleCommand("
        f'str(uuid4()), {report_id}, datetime.utcnow()).run()"'
    )
    output = worker_ssh(juju, command, environment)
    logger.info("execute_report(%s) output:\n%s", report_id, output)
    return output


def worker_log(juju: jubilant.Juju, lines: int = 200) -> str:
    """Return the tail of the worker's Superset log.

    `superset_config.py` sets `FILENAME` from the charm's `LOG_FILE` and turns
    on time-rotated logging, so Superset writes through a file handler and its
    messages never reach the stdout of a one-shot exec process.

    Args:
        juju: Jubilant object.
        lines: How many trailing lines to return.

    Returns:
        The tail of the log, empty if it cannot be read.
    """
    return worker_ssh(juju, f"tail -n {lines} {LOG_FILE}")


def wait_for_report(
    session: requests.Session,
    url: str,
    report_id: int,
    expected_state: str,
    *,
    timeout: float = REPORT_TIMEOUT,
) -> dict[str, Any]:
    """Poll a report's execution log until it reaches a state.

    Args:
        session: Authenticated Superset API session.
        url: Superset base URL.
        report_id: Report schedule identifier.
        expected_state: The terminal state to wait for.
        timeout: Maximum seconds to wait.

    Returns:
        The execution log entry that reached the requested state.

    Raises:
        AssertionError: If the report does not reach the state in time.
    """
    deadline = time.monotonic() + timeout
    last_logs: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        response = session.get(
            f"{url}/api/v1/report/{report_id}/log/", timeout=30
        )
        response.raise_for_status()
        last_logs = response.json().get("result", [])
        for entry in last_logs:
            if entry.get("state") == expected_state:
                return entry
        time.sleep(POLL_INTERVAL)
    raise AssertionError(
        f"report {report_id} never reached {expected_state}: {last_logs}"
    )


@pytest.fixture(scope="module")
def a_report_source_chart(superset_deployment: jubilant.Juju):
    """Create a chart on a data source every unit of the deployment reaches.

    Superset's bundled examples live in a SQLite file written during UI
    bootstrap, so they exist only on the UI unit's filesystem. Under
    `GLOBAL_ASYNC_QUERIES` the chart query is run by the worker, which has no
    such file, so no example chart could ever render for a report.

    Args:
        superset_deployment: The active deployment.

    Yields:
        The identifier of the chart created.
    """
    juju = superset_deployment
    session, url = steps.api_session(juju)
    environment = steps.workload_environment(juju, f"{steps.UI_NAME}/0")

    database_id, dataset_id, chart_id = steps.create_metadata_backed_chart(
        session, url, environment, f"report-source-{uuid.uuid4().hex[:8]}"
    )

    yield chart_id

    session, url = steps.api_session(juju)
    steps.api_delete(session, url, "/api/v1/chart", chart_id)
    steps.api_delete(session, url, "/api/v1/dataset", dataset_id)
    steps.api_delete(session, url, "/api/v1/database", database_id)


@pytest.mark.parametrize(
    "global_async_queries", [False, True], ids=["sync", "async"]
)
def test_a_dry_run_report_renders_and_delivers_nothing(
    superset_deployment: jubilant.Juju,
    a_report_source_chart: int,
    global_async_queries: bool,
):
    """Scenario: a report is rendered with delivery suppressed.

    Given a Superset deployment rendering reports in dry-run mode
    When a report on a chart is executed
    Then the report succeeds
    And it says its notification was suppressed rather than sent
    """
    juju = superset_deployment

    with given("a Superset deployment rendering reports in dry-run mode"):
        configure_reports(
            juju,
            screenshot_timeout=600,
            global_async_queries=global_async_queries,
        )
        assert_worker_configuration(juju, screenshot_timeout=600)

    session, url = steps.api_session(juju)
    report_id = create_chart_report(
        session,
        url,
        a_report_source_chart,
        f"dry-run-{uuid.uuid4().hex[:8]}",
        active=False,
    )
    try:
        with when("a report on a chart is executed"):
            output = execute_report(juju, report_id)

        with then("the report succeeds"):
            entry = wait_for_report(session, url, report_id, "Success")
            assert entry["state"] == "Success"

        with and_("it says its notification was suppressed rather than sent"):
            log = worker_log(juju)
            assert (
                "ALERT_REPORTS_NOTIFICATION_DRY_RUN is enabled" in log
            ), f"dry-run notice absent from {LOG_FILE}; exec output was {output!r}"
    finally:
        steps.api_delete(session, url, "/api/v1/report", report_id)


@pytest.mark.parametrize(
    "screenshot_timeout", [600, 1], ids=["default", "short"]
)
def test_the_configured_screenshot_timeout_reaches_superset(
    superset_deployment: jubilant.Juju, screenshot_timeout: int
):
    """Scenario: the operator bounds how long a report screenshot may take.

    Given a Superset deployment rendering reports
    When the screenshot timeout is configured
    Then Superset's loaded configuration carries it, in milliseconds
    """
    juju = superset_deployment

    with given("a Superset deployment rendering reports"):
        pass

    with when("the screenshot timeout is configured"):
        configure_reports(
            juju,
            screenshot_timeout=screenshot_timeout,
            global_async_queries=False,
        )

    with then("Superset's loaded configuration carries it, in milliseconds"):
        assert_worker_configuration(
            juju, screenshot_timeout=screenshot_timeout
        )


def test_the_beat_scheduler_dispatches_a_report_the_worker_runs(
    superset_deployment: jubilant.Juju, a_report_source_chart: int
):
    """Scenario: a report runs end to end.

    Given a Superset deployment rendering reports in dry-run mode
    And a Celery daemon answering on the broker
    When an active report schedule is created
    Then the report is executed
    """
    juju = superset_deployment

    with given("a Superset deployment rendering reports in dry-run mode"):
        configure_reports(
            juju, screenshot_timeout=600, global_async_queries=False
        )

    with and_("a Celery daemon answering on the broker"):
        steps.wait_for_celery_workers(juju, 1)
        beat_plan = steps.workload_services(juju, f"{steps.BEAT_NAME}/0")
        assert (
            beat_plan.get(steps.WORKLOAD_SERVICE) == "active"
        ), f"the beat scheduler is not running: {beat_plan}"

    session, url = steps.api_session(juju)
    report_id = create_chart_report(
        session,
        url,
        a_report_source_chart,
        f"scheduled-{uuid.uuid4().hex[:8]}",
        active=True,
    )
    try:
        with when("an active report schedule is created"):
            logger.info(
                "Waiting for Celery beat to dispatch report %s", report_id
            )

        with then("the report is executed without anyone running the command"):
            entry = wait_for_report(
                session, url, report_id, "Success", timeout=10 * 60
            )
            assert entry["state"] == "Success"
    finally:
        steps.api_delete(session, url, "/api/v1/report", report_id)
