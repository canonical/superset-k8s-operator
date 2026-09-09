#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Mail-free integration tests for alert and report screenshot rendering."""

import asyncio
import json
import logging
import shlex
import time
import uuid
from typing import Any, Mapping

import pytest
import pytest_asyncio
import requests
import yaml
from integration.conftest import deploy  # noqa: F401, pylint: disable=W0611
from integration.helpers import (
    CHARM_FUNCTIONS,
    SMTP_INTEGRATOR_NAME,
    UI_NAME,
    api_authentication,
    deploy_smtp_integrator,
    get_unit_url,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)
WORKER_NAME = f"superset-k8s-{CHARM_FUNCTIONS['worker']}"
BEAT_NAME = f"superset-k8s-{CHARM_FUNCTIONS['beat']}"
REPORT_APPS = [UI_NAME, BEAT_NAME, WORKER_NAME]
SHARED_DATABASE_NAME = "superset-metadata"
SHARED_SCHEMA = "public"
SHARED_TABLE = "ab_user"
POLL_INTERVAL = 5
REPORT_TIMEOUT = 180


async def configure_reports(
    ops_test: OpsTest, screenshot_timeout: int, global_async_queries: bool
) -> None:
    """Configure every report component and wait for it to settle.

    Args:
        ops_test: Juju test model.
        screenshot_timeout: Screenshot renderer timeout in seconds.
        global_async_queries: Whether to enable the GLOBAL_ASYNC_QUERIES flag.
    """
    feature_flags = ["ALERT_REPORTS"]
    if global_async_queries:
        feature_flags.append("GLOBAL_ASYNC_QUERIES")

    config = {
        "feature-flags": ",".join(feature_flags),
        "report-dry-run": "true",
        "screenshot-timeout": str(screenshot_timeout),
    }
    for app_name in REPORT_APPS:
        await ops_test.model.applications[app_name].set_config(config)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=REPORT_APPS,
            status="active",
            raise_on_blocked=False,
            timeout=600,
        )


async def worker_exec(
    ops_test: OpsTest,
    command: str,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Run a command in the worker workload container.

    Args:
        ops_test: Juju test model.
        command: Shell command to execute.
        environment: Environment variables to provide to the command.

    Returns:
        Standard output from the command.
    """
    if environment:
        assignments = " ".join(
            f"{key}={shlex.quote(value)}" for key, value in environment.items()
        )
        command = f"env {assignments} {command}"

    return_code, stdout, stderr = await ops_test.juju(
        "ssh",
        "--container",
        "superset",
        f"{WORKER_NAME}/0",
        command,
    )
    assert return_code == 0, stderr
    return stdout.strip()


# The charm's Pebble layer defines its managed service under APP_NAME
# ("superset"). The rock's base layer also ships a disabled "superset-ui"
# service whose environment carries a different secret key, so the service
# must be selected by name rather than scanning the whole rendered plan.
WORKER_SERVICE = "superset"


async def worker_environment(ops_test: OpsTest) -> dict[str, str]:
    """Read the worker service environment from the running Pebble plan.

    Charm-set variables live in the Pebble service environment, which an exec
    shell does not inherit. They are read from the rendered plan and scoped to
    the charm-managed ``superset`` service: the plan also lists a disabled
    ``superset-ui`` service whose secret key differs, so selecting by service
    name is required to sign report screenshots with the key the UI accepts.

    Args:
        ops_test: Juju test model.

    Returns:
        Mapping of environment variable names to their configured values.
    """
    plan_text = await worker_exec(
        ops_test, "/charm/bin/pebble plan 2>/dev/null || pebble plan"
    )
    plan = yaml.safe_load(plan_text)
    environment = plan["services"][WORKER_SERVICE]["environment"]
    return {
        key: "" if value is None else str(value)
        for key, value in environment.items()
    }


async def assert_worker_config(
    ops_test: OpsTest, screenshot_timeout: int
) -> None:
    """Assert charm environment values and Superset's loaded configuration.

    Args:
        ops_test: Juju test model.
        screenshot_timeout: Expected screenshot timeout in seconds.
    """
    environment = await worker_environment(ops_test)
    assert environment.get("SCREENSHOT_TIMEOUT") == str(screenshot_timeout)
    assert environment.get("ALERT_REPORTS_DRY_RUN") == "true"

    config = await worker_exec(
        ops_test,
        'python3 -c "from superset.app import create_app; '
        "app = create_app(); "
        "print(app.config['SCREENSHOT_PLAYWRIGHT_DEFAULT_TIMEOUT']); "
        "print(app.config['ALERT_REPORTS_NOTIFICATION_DRY_RUN'])\"",
        environment,
    )
    assert config.splitlines()[-2:] == [str(screenshot_timeout * 1000), "True"]


def api_post(
    session: requests.Session, url: str, path: str, data: dict
) -> int:
    """Create a Superset resource and return its ID.

    Args:
        session: Authenticated Superset API session.
        url: Superset base URL.
        path: API resource path.
        data: Resource payload.

    Returns:
        The created resource ID.
    """
    response = session.post(f"{url}{path}", json=data, timeout=30)
    assert (
        response.ok
    ), f"POST {path} failed ({response.status_code}): {response.text}"
    return response.json()["id"]


def api_delete(
    session: requests.Session, url: str, path: str, resource_id: int
) -> None:
    """Delete a Superset resource, allowing prior cleanup attempts.

    Args:
        session: Authenticated Superset API session.
        url: Superset base URL.
        path: API resource path.
        resource_id: Resource ID to remove.
    """
    response = session.delete(f"{url}{path}/{resource_id}", timeout=30)
    assert response.status_code in (200, 404), response.text


@pytest_asyncio.fixture(name="report_chart", scope="module")
async def report_chart_fixture(  # pylint: disable=redefined-outer-name
    ops_test: OpsTest, deploy  # noqa: F811
):
    """Create a chart whose data source every unit can reach.

    Superset's bundled examples live in a SQLite file written during UI
    bootstrap, so they exist only on the UI unit's filesystem. Under
    GLOBAL_ASYNC_QUERIES the chart query is executed by the worker, which has no
    such file, so no example chart can ever render for a report. The Superset
    metadata database is reachable from every unit, so it backs the chart here.

    Args:
        ops_test: Juju test model.
        deploy: Deployment fixture.

    Yields:
        The created chart ID.
    """
    url = await get_unit_url(ops_test, UI_NAME, 0, 8088)
    session = await api_authentication(ops_test, url)
    environment = await worker_environment(ops_test)

    database_id = api_post(
        session,
        url,
        "/api/v1/database/",
        {
            "database_name": SHARED_DATABASE_NAME,
            "sqlalchemy_uri": environment["SQL_ALCHEMY_URI"],
            "expose_in_sqllab": True,
        },
    )
    dataset_id = api_post(
        session,
        url,
        "/api/v1/dataset/",
        {
            "database": database_id,
            "schema": SHARED_SCHEMA,
            "table_name": SHARED_TABLE,
        },
    )
    chart_id = api_post(
        session,
        url,
        "/api/v1/chart/",
        {
            "slice_name": f"report-source-{uuid.uuid4()}",
            "viz_type": "big_number_total",
            "datasource_id": dataset_id,
            "datasource_type": "table",
            "params": json.dumps(
                {
                    "datasource": f"{dataset_id}__table",
                    "viz_type": "big_number_total",
                    "metric": "count",
                    "adhoc_filters": [],
                    "time_range": "No filter",
                }
            ),
        },
    )

    yield chart_id

    api_delete(session, url, "/api/v1/chart", chart_id)
    api_delete(session, url, "/api/v1/dataset", dataset_id)
    api_delete(session, url, "/api/v1/database", database_id)


def create_chart_report(
    session: requests.Session, url: str, chart_id: int, name: str
) -> int:
    """Create an inactive PNG report schedule for a chart.

    The schedule is created inactive so Celery beat never runs it; the test
    drives execution synchronously via ``execute_report`` instead, avoiding a
    race where beat leaves the report wedged in the ``Working`` state.

    Args:
        session: Authenticated Superset API session.
        url: Superset base URL.
        chart_id: Chart to render.
        name: Unique report schedule name.

    Returns:
        The created report schedule ID.
    """
    return api_post(
        session,
        url,
        "/api/v1/report/",
        {
            "active": False,
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


async def execute_report(ops_test: OpsTest, report_id: int) -> str:
    """Run the report command synchronously instead of relying on Celery beat.

    The command runs in a one-shot exec process, so its logs go to that process
    rather than the worker service's juju log; they are returned to the caller.
    A concrete ``scheduled_dttm`` is supplied because that execution log column
    is non-nullable and eager Celery execution would otherwise leave it null.

    Args:
        ops_test: Juju test model.
        report_id: Report schedule ID to execute.

    Returns:
        Combined stdout and stderr emitted while executing the report.
    """
    environment = await worker_environment(ops_test)
    assignments = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in environment.items()
    )
    command = (
        f"env {assignments} python3 -c "
        '"from datetime import datetime; '
        "from uuid import uuid4; "
        "from superset.app import create_app; "
        "app = create_app(); "
        "app.app_context().push(); "
        "from superset.commands.report.execute import "
        "AsyncExecuteReportScheduleCommand; "
        "AsyncExecuteReportScheduleCommand("
        f'str(uuid4()), {report_id}, datetime.utcnow()).run()"'
    )
    _, stdout, stderr = await ops_test.juju(
        "ssh", "--container", "superset", f"{WORKER_NAME}/0", command
    )
    output = f"{stdout}\n{stderr}"
    logger.info("execute_report(%s) output:\n%s", report_id, output)
    return output


async def wait_for_report(
    session: requests.Session,
    url: str,
    report_id: int,
    expected_state: str,
) -> dict[str, Any]:
    """Poll a report execution log until it reaches its expected final state.

    Args:
        session: Authenticated Superset API session.
        url: Superset base URL.
        report_id: Report schedule ID.
        expected_state: Expected terminal state.

    Returns:
        The execution log that reached the requested state.

    Raises:
        TimeoutError: If the report does not reach the expected state in time.
    """
    deadline = time.monotonic() + REPORT_TIMEOUT
    last_logs: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        response = session.get(
            f"{url}/api/v1/report/{report_id}/log/", timeout=30
        )
        response.raise_for_status()
        last_logs = response.json().get("result", [])
        for log in last_logs:
            if log.get("state") == expected_state:
                return log
        await asyncio.sleep(POLL_INTERVAL)
    raise TimeoutError(
        f"Report {report_id} did not reach {expected_state}: {last_logs}"
    )


@pytest_asyncio.fixture(name="deploy-report-smtp", scope="module")
async def deploy_report_smtp(  # pylint: disable=redefined-outer-name
    ops_test: OpsTest, deploy  # noqa: F811
) -> None:
    """Relate an SMTP provider, which `ALERT_REPORTS` cannot be set without.

    Args:
        ops_test: Juju test model.
        deploy: Shared deployment fixture from the integration conftest.
    """
    del deploy
    await deploy_smtp_integrator(ops_test)
    for app_name in REPORT_APPS:
        await ops_test.model.integrate(
            f"{app_name}:smtp", f"{SMTP_INTEGRATOR_NAME}:smtp"
        )

    # The relation and the feature flag are validated against each other, so
    # the deployment only settles once both are in place. Each test resets
    # `feature-flags` to the combination it needs.
    for app_name in REPORT_APPS:
        await ops_test.model.applications[app_name].set_config(
            {"feature-flags": "ALERT_REPORTS"}
        )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=REPORT_APPS + [SMTP_INTEGRATOR_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=1200,
        )


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-report-smtp")
class TestReports:
    """Exercise report rendering with notification delivery suppressed.

    The `smtp` relation is required to set `ALERT_REPORTS`, but no mail
    server stands behind it and `report-dry-run` stops the worker delivering.
    """

    @pytest.mark.parametrize(
        "global_async_queries", [False, True], ids=["sync", "async"]
    )
    async def test_dry_run_report_succeeds(
        self,
        ops_test: OpsTest,
        report_chart: int,
        global_async_queries: bool,
    ):
        """Render a chart and suppress its notification delivery."""
        await configure_reports(
            ops_test,
            screenshot_timeout=600,
            global_async_queries=global_async_queries,
        )
        await assert_worker_config(ops_test, screenshot_timeout=600)
        url = await get_unit_url(ops_test, UI_NAME, 0, 8088)
        session = await api_authentication(ops_test, url)

        report_id = create_chart_report(
            session, url, report_chart, f"dry-run-{uuid.uuid4()}"
        )
        try:
            output = await execute_report(ops_test, report_id)
            log = await wait_for_report(session, url, report_id, "Success")
            assert log["state"] == "Success"
            assert "ALERT_REPORTS_NOTIFICATION_DRY_RUN is enabled" in output
        finally:
            api_delete(session, url, "/api/v1/report", report_id)

    @pytest.mark.parametrize(
        "global_async_queries", [False, True], ids=["sync", "async"]
    )
    async def test_screenshot_timeout_is_applied(
        self,
        ops_test: OpsTest,
        report_chart: int,
        global_async_queries: bool,
    ):
        """Fail a chart screenshot at the configured one-second limit."""
        await configure_reports(
            ops_test,
            screenshot_timeout=1,
            global_async_queries=global_async_queries,
        )
        await assert_worker_config(ops_test, screenshot_timeout=1)
        url = await get_unit_url(ops_test, UI_NAME, 0, 8088)
        session = await api_authentication(ops_test, url)
        report_id = create_chart_report(
            session,
            url,
            report_chart,
            f"report_timeout_{uuid.uuid4().hex}",
        )
        try:
            await execute_report(ops_test, report_id)
            log = await wait_for_report(session, url, report_id, "Error")
            error = log.get("error_message", "")
            assert "Timeout 1000ms exceeded" in error, error
        finally:
            api_delete(session, url, "/api/v1/report", report_id)
