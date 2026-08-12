#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Mail-free integration tests for alert and report screenshot rendering."""

import asyncio
import base64
import json
import logging
import re
import shlex
import time
import uuid
from typing import Any, Mapping

import pytest
import requests
from integration.conftest import deploy  # noqa: F401, pylint: disable=W0611
from integration.helpers import (
    CHARM_FUNCTIONS,
    UI_NAME,
    api_authentication,
    get_unit_url,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)
WORKER_NAME = f"superset-k8s-{CHARM_FUNCTIONS['worker']}"
BEAT_NAME = f"superset-k8s-{CHARM_FUNCTIONS['beat']}"
REPORT_APPS = [UI_NAME, BEAT_NAME, WORKER_NAME]
REPORT_CONFIG = {
    "feature-flags": "GLOBAL_ASYNC_QUERIES,ALERT_REPORTS",
    "report-dry-run": "true",
}
POLL_INTERVAL = 5
REPORT_TIMEOUT = 180


async def configure_reports(
    ops_test: OpsTest, screenshot_timeout: int
) -> None:
    """Configure every report component and wait for it to settle.

    Args:
        ops_test: Juju test model.
        screenshot_timeout: Screenshot renderer timeout in seconds.
    """
    config = {**REPORT_CONFIG, "screenshot-timeout": str(screenshot_timeout)}
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


async def worker_environment(ops_test: OpsTest) -> dict[str, str]:
    """Read the worker service environment from its Pebble plan.

    Charm-set variables live in the Pebble service environment, which an exec
    shell does not inherit, so they are read from the rendered plan instead.

    Args:
        ops_test: Juju test model.

    Returns:
        Mapping of environment variable names to their configured values.
    """
    plan = await worker_exec(ops_test, "/charm/bin/pebble plan")
    entries = re.findall(
        r"^\s*([A-Z][A-Z0-9_]*):\s*(.+?)\s*$", plan, re.MULTILINE
    )
    return {key: value.strip("'\"") for key, value in entries}


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


_SCREENSHOT_PROBE_SCRIPT = """
import json
from superset.app import create_app

app = create_app()
app.app_context().push()

from playwright.sync_api import sync_playwright
from superset.extensions import (
    machine_auth_provider_factory,
    security_manager,
)
from superset.utils.urls import headless_url

chart_id = __CHART_ID__
user = security_manager.find_user(username="admin")
print("PROBE user:", getattr(user, "username", None))
auth_func = app.config.get("WEBDRIVER_AUTH_FUNC")
print("PROBE auth_func:", getattr(auth_func, "__name__", auth_func))
print("PROBE baseurl:", app.config.get("WEBDRIVER_BASEURL"))
import hashlib

secret = app.config.get("SECRET_KEY") or ""
secret = secret.encode() if isinstance(secret, str) else secret
print("PROBE worker_secret_sha:", hashlib.sha256(secret).hexdigest()[:12])
form_data = json.dumps({"slice_id": chart_id})
url = headless_url("/explore/?form_data=" + form_data + "&standalone=true")
print("PROBE url:", url)
args = app.config["WEBDRIVER_OPTION_ARGS"]
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=args)
    context = browser.new_context()
    context.set_default_timeout(30000)
    provider = machine_auth_provider_factory.instance
    minted = provider.get_cookies(user)
    print("PROBE minted_cookies:", list(minted.keys()))
    print("PROBE session_len:", len(minted.get("session", "")))
    import requests

    api_url = headless_url("/api/v1/chart/")
    try:
        resp = requests.get(
            api_url, cookies=minted, allow_redirects=False, timeout=30
        )
        print("PROBE api_status:", resp.status_code)
        print("PROBE api_location:", resp.headers.get("Location"))
        print("PROBE api_body:", resp.text[:200].replace(chr(10), " "))
    except Exception as exc:
        print("PROBE api_error:", exc)
    provider.authenticate_browser_context(context, user)
    print(
        "PROBE ctx_cookies:",
        [(c.get("name"), c.get("domain")) for c in context.cookies()],
    )
    page = context.new_page()
    try:
        page.goto(url, wait_until="load")
    except Exception as exc:
        print("PROBE goto_error:", exc)
    print("PROBE final_url:", page.url)
    try:
        print("PROBE title:", page.title())
    except Exception as exc:
        print("PROBE title_error:", exc)
    print("PROBE password_inputs:", page.locator("input[type=password]").count())
    print("PROBE chart_containers:", page.locator(".chart-container").count())
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except Exception as exc:
        body = "body_error: %s" % exc
    print("PROBE body_snippet:", body[:300].replace(chr(10), " "))
    browser.close()
"""


_UI_SECRET_SCRIPT = """
import subprocess
import os
import hashlib

plan = subprocess.run(
    ["/charm/bin/pebble", "plan"], capture_output=True, text=True
).stdout
for line in plan.splitlines():
    stripped = line.strip()
    if ":" not in stripped:
        continue
    key, _, val = stripped.partition(":")
    if key and key.replace("_", "").isalnum() and key.isupper():
        os.environ[key] = val.strip().strip("'").strip('"')

from superset.app import create_app

app = create_app()
app.app_context().push()
secret = app.config.get("SECRET_KEY") or ""
secret = secret.encode() if isinstance(secret, str) else secret
print("PROBE ui_secret_sha:", hashlib.sha256(secret).hexdigest()[:12])
"""


async def probe_screenshot_page(ops_test: OpsTest, chart_id: int) -> str:
    """Diagnose why a report screenshot failed to render.

    Authenticates a Playwright browser as the admin user exactly as the report
    worker does, navigates to the chart's standalone URL, and reports what the
    browser actually sees. This distinguishes a login-page redirect (browser
    auth/cookie problem) from a chart that simply does not render.

    Args:
        ops_test: Juju test model.
        chart_id: Chart whose standalone page should be probed.

    Returns:
        Combined stdout and stderr from the in-container probe.
    """
    environment = await worker_environment(ops_test)
    assignments = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in environment.items()
    )
    script = _SCREENSHOT_PROBE_SCRIPT.replace("__CHART_ID__", str(chart_id))
    encoded = base64.b64encode(script.encode()).decode()
    runner = f"import base64; exec(base64.b64decode('{encoded}').decode())"
    command = f"env {assignments} python3 -c {shlex.quote(runner)}"
    _, stdout, stderr = await ops_test.juju(
        "ssh", "--container", "superset", f"{WORKER_NAME}/0", command
    )
    ui_encoded = base64.b64encode(_UI_SECRET_SCRIPT.encode()).decode()
    ui_runner = (
        f"import base64; exec(base64.b64decode('{ui_encoded}').decode())"
    )
    ui_command = f"python3 -c {shlex.quote(ui_runner)}"
    _, ui_out, _ = await ops_test.juju(
        "ssh", "--container", "superset", f"{UI_NAME}/0", ui_command
    )
    return f"{stdout}\n{stderr}\n{ui_out}"


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


def create_slow_chart(
    session: requests.Session,
    url: str,
    resource_name: str,
    sqlalchemy_uri: str,
) -> tuple[int, int, int]:
    """Create a PostgreSQL virtual dataset and chart that blocks during render.

    Args:
        session: Authenticated Superset API session.
        url: Superset base URL.
        resource_name: Unique name for the temporary resources.
        sqlalchemy_uri: Related PostgreSQL database connection URI.

    Returns:
        Database, dataset, and chart IDs, in that order.
    """
    database_id = api_post(
        session,
        url,
        "/api/v1/database/",
        {
            "database_name": resource_name,
            "expose_in_sqllab": True,
            "sqlalchemy_uri": sqlalchemy_uri,
        },
    )
    dataset_id = api_post(
        session,
        url,
        "/api/v1/dataset/",
        {
            "database": database_id,
            "schema": "public",
            "sql": "SELECT 1 AS value, pg_sleep(90)",
            "table_name": resource_name,
        },
    )
    chart_id = api_post(
        session,
        url,
        "/api/v1/chart/",
        {
            "datasource_id": dataset_id,
            "datasource_type": "table",
            "slice_name": resource_name,
            "viz_type": "big_number_total",
            "params": json.dumps(
                {
                    "adhoc_filters": [],
                    "datasource": f"{dataset_id}__table",
                    "granularity_sqla": None,
                    "metric": {"expressionType": "SIMPLE", "column": None},
                    "time_range": "No filter",
                    "viz_type": "big_number_total",
                }
            ),
        },
    )
    return database_id, dataset_id, chart_id


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestReports:
    """Exercise report rendering without requiring an SMTP deployment."""

    async def test_dry_run_report_succeeds(self, ops_test: OpsTest):
        """Render an example chart and suppress its notification delivery."""
        await configure_reports(ops_test, screenshot_timeout=600)
        await assert_worker_config(ops_test, screenshot_timeout=600)
        url = await get_unit_url(ops_test, UI_NAME, 0, 8088)
        session = await api_authentication(ops_test, url)
        charts = session.get(f"{url}/api/v1/chart/", timeout=30).json()[
            "result"
        ]
        assert charts, "Expected example charts from load-examples=true"

        report_id = create_chart_report(
            session, url, charts[0]["id"], f"dry-run-{uuid.uuid4()}"
        )
        try:
            output = await execute_report(ops_test, report_id)
            try:
                log = await wait_for_report(session, url, report_id, "Success")
            except TimeoutError:
                try:
                    probe = await probe_screenshot_page(
                        ops_test, charts[0]["id"]
                    )
                    logger.info("screenshot probe:\n%s", probe)
                except Exception:  # noqa: BLE001
                    logger.exception("screenshot probe failed")
                raise
            assert log["state"] == "Success"
            assert "ALERT_REPORTS_NOTIFICATION_DRY_RUN is enabled" in output
        finally:
            api_delete(session, url, "/api/v1/report", report_id)

    async def test_screenshot_timeout_is_applied(self, ops_test: OpsTest):
        """Fail a slow dashboard screenshot at the configured one-second limit."""
        await configure_reports(ops_test, screenshot_timeout=1)
        await assert_worker_config(ops_test, screenshot_timeout=1)
        url = await get_unit_url(ops_test, UI_NAME, 0, 8088)
        session = await api_authentication(ops_test, url)
        resource_name = f"report_timeout_{uuid.uuid4().hex}"
        environment = await worker_environment(ops_test)
        sqlalchemy_uri = environment["SQL_ALCHEMY_URI"]
        database_id, dataset_id, chart_id = create_slow_chart(
            session, url, resource_name, sqlalchemy_uri
        )
        report_id = create_chart_report(session, url, chart_id, resource_name)
        try:
            await execute_report(ops_test, report_id)
            log = await wait_for_report(session, url, report_id, "Error")
            error = log.get("error_message", "")
            assert "Timeout 1000ms exceeded" in error
            assert ".chart-container" in error
        finally:
            api_delete(session, url, "/api/v1/report", report_id)
            api_delete(session, url, "/api/v1/chart", chart_id)
            api_delete(session, url, "/api/v1/dataset", dataset_id)
            api_delete(session, url, "/api/v1/database", database_id)
