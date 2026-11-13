#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: the mcp charm function serving MCP tool calls.

`mcp` is a fourth charm function that serves Superset's MCP service against
the shared metadata database. This stage wires no oauth relation, so
`mcp-dev-username` is the only way to give it an identity: with neither that
nor the oauth relation set, the application has no usable auth source at all
and refuses to run.

It also exercises the `MCP_DISABLED_TOOLS` rock patch (39835): a tool named
in `mcp-disabled-tools` must be actually removed from the running FastMCP
server, not merely rejected at the API layer. And `mcp-restrict-chart-data-
previews`: chart tools must return a link, never rows.
"""

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

import jubilant
import pytest
import requests
import steps
from bdd import and_, given, then, when

from literals import LOG_FILE

logger = logging.getLogger(__name__)

MCP_DEV_USERNAME = "admin"
MCP_PORT = 5008


def _deploy_mcp(juju: jubilant.Juju, charm: Path, charm_image: str) -> None:
    """Deploy a migrated UI, then mcp with no auth source configured.

    mcp will not even start until the metadata database has been migrated
    (`_metadata_database_status()`), so a UI is deployed and settled first —
    otherwise mcp would report waiting on the UI rather than the auth
    scenario this fixture exists to isolate.

    Args:
        juju: Jubilant object.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.
    """
    steps.deploy_dependencies(juju)

    ui_name = steps.deploy_superset_application(
        juju, charm, charm_image, "app-gunicorn"
    )
    steps.integrate_dependencies(juju, ui_name)
    steps.wait_for_active(juju, [ui_name], timeout=steps.DEPLOY_TIMEOUT)

    name = steps.deploy_superset_application(juju, charm, charm_image, "mcp")
    steps.integrate_dependencies(juju, name)


def _call_tool(url: str, tool: str, arguments: dict) -> dict:
    """Call an MCP tool once over the streamable-HTTP transport.

    Speaks the JSON-RPC `tools/call` method directly instead of going
    through the `fastmcp` client package, since that package requires
    pydantic 2 and this charm's own dependencies pin pydantic 1.

    Some tools (e.g. those proxied through `call_tool`) emit one or more
    `notifications/message` log events on the SSE stream before their actual
    JSON-RPC response, so the first `data:` line is not necessarily the
    result — the last one always is.

    Args:
        url: The mcp endpoint URL.
        tool: Name of the tool to call.
        arguments: Arguments to pass to the tool.

    Returns:
        The parsed JSON-RPC response body.
    """
    response = requests.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        timeout=30,
    )
    response.raise_for_status()
    result = None
    for line in response.text.splitlines():
        if line.startswith("data:"):
            data = json.loads(line.removeprefix("data:"))
            if "result" in data or "error" in data:
                result = data
    return result if result is not None else response.json()


def _tool_text(result: dict) -> str:
    """Extract a tool call's text payload from its JSON-RPC result.

    Args:
        result: The parsed JSON-RPC response body from `_call_tool`.

    Returns:
        The text of the tool's first content block.
    """
    return result["result"]["content"][0]["text"]


def _wait_for_tool_result(
    juju: jubilant.Juju,
    url: str,
    tool: str,
    arguments: dict,
    *,
    timeout: float = 180,
) -> dict:
    """Call an MCP tool, retrying until the service answers.

    The workload can take a little longer to accept connections than the
    pebble check that first reports it active.

    Args:
        juju: Jubilant object, used for its polling loop.
        url: The mcp endpoint URL.
        tool: Name of the tool to call.
        arguments: Arguments to pass to the tool.
        timeout: Maximum seconds to wait.

    Returns:
        The parsed JSON-RPC response body.
    """
    outcome: dict = {}

    def _answered() -> bool:
        """Return True once the tool call succeeds."""
        outcome["result"] = _call_tool(url, tool, arguments)
        return True

    steps.poll_until(
        juju,
        _answered,
        f"mcp at {url} never answered {tool!r}",
        timeout=timeout,
        delay=10,
    )
    return outcome["result"]


def _wait_for_tool_error(
    juju: jubilant.Juju,
    url: str,
    tool: str,
    arguments: dict,
    *,
    timeout: float = 60,
) -> str:
    """Call `tool` through the `call_tool` proxy until it reports an error.

    `mcp-disabled-tools` only takes effect once the workload has restarted
    with the new environment, which can lag a few seconds behind Juju
    reporting the application active again.

    Args:
        juju: Jubilant object, used for its polling loop.
        url: The mcp endpoint URL.
        tool: Name of the disabled tool to call.
        arguments: Arguments to pass to the tool.
        timeout: Maximum seconds to wait.

    Returns:
        The tool's error text, including its `Error ID`.
    """
    outcome: dict = {}

    def _errored() -> bool:
        """Return True once the proxied call reports an error."""
        text = _tool_text(
            _call_tool(
                url, "call_tool", {"name": tool, "arguments": arguments}
            )
        )
        if not text.startswith("Error:"):
            raise AssertionError(f"{tool} still succeeding: {text}")
        outcome["text"] = text
        return True

    steps.poll_until(
        juju,
        _errored,
        f"{tool} never started reporting a disabled-tool error",
        timeout=timeout,
        delay=5,
    )
    return outcome["text"]


def _preview_formats_props(node: Any):
    """Yield every `preview_formats` property schema under `node`.

    Args:
        node: A JSON schema, or part of one.

    Yields:
        Each `preview_formats` property schema found.
    """
    if isinstance(node, list):
        for item in node:
            yield from _preview_formats_props(item)
    elif isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict) and "preview_formats" in props:
            yield props["preview_formats"]
        for value in node.values():
            yield from _preview_formats_props(value)


def _list_tools(url: str) -> list[dict]:
    """List tools available over the MCP endpoint.

    Args:
        url: The MCP endpoint URL.

    Returns:
        List of tool descriptors from tools/list.
    """
    response = requests.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        },
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        timeout=30,
    )
    response.raise_for_status()
    result = None
    for line in response.text.splitlines():
        if line.startswith("data:"):
            data = json.loads(line.removeprefix("data:"))
            if "result" in data:
                result = data
    parsed = result if result is not None else response.json()
    return parsed.get("result", {}).get("tools", [])


def _assert_preview_formats_url_only(url: str) -> None:
    """Assert tools and search results advertise only url for preview_formats.

    Verifies:
    1. tools/list: any tool inputSchema defining preview_formats restricts
       its enum and default to ["url"] without naming the hidden formats.
    2. search_tools: search results do not name the hidden formats either.

    Args:
        url: The MCP endpoint.
    """
    tools = _list_tools(url)
    props = list(_preview_formats_props(tools))
    for prop in props:
        assert prop["items"]["enum"] == ["url"], prop
        assert prop["default"] == ["url"], prop
        rendered = json.dumps(prop).lower()
        for banned in ("ascii", "vega_lite", "removed", "disabled"):
            assert banned not in rendered, prop

    result = _call_tool(url, "search_tools", {"query": "preview formats"})
    search_results = json.loads(_tool_text(result))
    guarded = {"generate_chart", "update_chart", "update_chart_preview"}
    for item in search_results:
        if item.get("name") in guarded:
            desc = item.get("description", "").lower()
            for banned in ("ascii", "vega_lite", "removed", "disabled"):
                assert banned not in desc, (item.get("name"), desc)


def _table_chart_request(juju: jubilant.Juju) -> tuple[int, dict]:
    """Create a dataset and build a generate_chart call asking for rows.

    Args:
        juju: Jubilant object.

    Returns:
        A chart id and the call_tool arguments.
    """
    session, api_url = steps.api_session(juju)
    environment = steps.workload_environment(juju, f"{steps.UI_NAME}/0")
    _, dataset_id, chart_id = steps.create_metadata_backed_chart(
        session, api_url, environment, f"test-preview-{uuid.uuid4().hex[:8]}"
    )
    request = {
        "dataset_id": dataset_id,
        # Superset only builds a url preview for a saved chart.
        "save_chart": True,
        "config": {
            "chart_type": "table",
            "columns": [
                {"name": "username"},
                {"name": "id", "aggregate": "COUNT"},
            ],
        },
        "preview_formats": ["table"],
    }
    return chart_id, {
        "name": "generate_chart",
        "arguments": {"request": request},
    }


@pytest.fixture(scope="module")
def mcp_without_auth(
    request: pytest.FixtureRequest,
    model: jubilant.Juju,
    charm: Path,
    charm_image: str,
) -> jubilant.Juju:
    """Deploy a migrated UI, then mcp with neither auth source set.

    Args:
        request: Pytest request object.
        model: The module's model.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.

    Returns:
        The model, holding an active UI and mcp related to its dependencies
        but blocked on auth.
    """
    logger.info(
        "Deploying a migrated UI, then mcp with neither auth source set"
    )
    return steps.adopt_or_build(
        request, model, _deploy_mcp, charm, charm_image
    )


def test_mcp_blocks_without_any_auth_configured(
    mcp_without_auth: jubilant.Juju,
):
    """Scenario: mcp is deployed with no oauth relation and no dev username.

    Given a migrated database and mcp related to its dependencies, no auth
      configured
    Then mcp blocks naming what it needs
    """
    juju = mcp_without_auth

    with given(
        "a migrated database and mcp related to its dependencies, no auth "
        "configured"
    ):
        pass

    with then("mcp blocks naming what it needs"):
        steps.assert_blocked_with(
            juju,
            [steps.MCP_NAME],
            "mcp requires either the oauth relation or mcp-dev-username",
        )


def test_mcp_serves_tool_calls_once_dev_username_is_set(
    mcp_without_auth: jubilant.Juju,
):
    """Scenario: mcp-dev-username lets mcp authenticate without oauth.

    Given mcp blocked with no auth source configured
    When mcp-dev-username is set
    Then mcp becomes active
    And mcp answers a tool call
    """
    juju = mcp_without_auth

    with given("mcp blocked with no auth source configured"):
        steps.assert_blocked_with(
            juju,
            [steps.MCP_NAME],
            "mcp requires either the oauth relation or mcp-dev-username",
        )

    with when("mcp-dev-username is set"):
        steps.set_config(
            juju, [steps.MCP_NAME], {"mcp-dev-username": MCP_DEV_USERNAME}
        )

    with then("mcp becomes active"):
        steps.wait_for_active(
            juju, [steps.MCP_NAME], timeout=steps.DEPLOY_TIMEOUT
        )

    with and_("mcp answers a tool call"):
        url = f"{steps.get_unit_url(juju, steps.MCP_NAME, port=MCP_PORT)}/mcp"
        result = _wait_for_tool_result(juju, url, "health_check", {})
        assert "error" not in result, result


def test_mcp_disabled_tools_removes_the_configured_tool(
    mcp_without_auth: jubilant.Juju,
):
    """Scenario: mcp-disabled-tools removes a tool from the live server.

    Given mcp active with dev-username set, no tools restricted
    When mcp-disabled-tools names a tool
    Then calling that tool fails with a not-found error
    And the workload logs confirm the tool was actually removed, not merely
      rejected at the API layer
    """
    juju = mcp_without_auth
    url = f"{steps.get_unit_url(juju, steps.MCP_NAME, port=MCP_PORT)}/mcp"
    unit = f"{steps.MCP_NAME}/0"
    # A read-only tool reached through the `call_tool` proxy (the server
    # only exposes get_instance_info/health_check/search_tools/call_tool
    # directly), used to exercise mcp-disabled-tools. Any read-only tool
    # would do.
    restricted_tool = "list_charts"

    with given("mcp active with dev-username set, no tools restricted"):
        text = _tool_text(
            _call_tool(
                url,
                "call_tool",
                {"name": restricted_tool, "arguments": {"request": {}}},
            )
        )
        assert not text.startswith("Error:"), text
        assert "charts" in json.loads(text)

    try:
        with when(f"mcp-disabled-tools names {restricted_tool!r}"):
            steps.set_config(
                juju, [steps.MCP_NAME], {"mcp-disabled-tools": restricted_tool}
            )

        with then(f"{restricted_tool} fails with a not-found error"):
            text = _wait_for_tool_error(
                juju, url, restricted_tool, {"request": {}}
            )
            match = re.search(r"Error ID: (err_\S+)\.", text)
            assert match, text
            error_id = match.group(1)

        with and_("the workload logs confirm the tool was actually removed"):
            _, log = steps.read_workload_file(juju, unit, LOG_FILE)
            tail = "\n".join(log.splitlines()[-200:])
            assert "error_type=NotFoundError" in tail, tail
            assert f"Unknown tool: '{restricted_tool}'" in tail, tail
            assert error_id in tail, tail
    finally:
        juju.config(steps.MCP_NAME, reset="mcp-disabled-tools")
        steps.wait_for_active(
            juju, [steps.MCP_NAME], timeout=steps.SETTLE_TIMEOUT
        )


def test_mcp_restrict_chart_data_previews_restricts_previews_to_url(
    mcp_without_auth: jubilant.Juju,
):
    """Scenario: mcp-restrict-chart-data-previews limits previews to a link.

    Given mcp is active and a chart exists
    And generate_chart returns the chart's rows in a table preview
    When mcp-restrict-chart-data-previews is set to true
    Then the same request returns only a preview link, with no rows
    And the workload log says previews were restricted to url
    And get_chart_data is no longer available
    And tool listings and search offer no preview format except url
    """
    juju = mcp_without_auth
    config_key = "mcp-restrict-chart-data-previews"

    with given("mcp is active and a chart exists"):
        # This scenario must not depend on test ordering: when it runs
        # alone (-k), nothing else has set the dev username or waited for
        # the unit to publish an address.
        steps.set_config(
            juju, [steps.MCP_NAME], {"mcp-dev-username": MCP_DEV_USERNAME}
        )
        steps.wait_for_active(
            juju, [steps.MCP_NAME], timeout=steps.DEPLOY_TIMEOUT
        )
        url = f"{steps.get_unit_url(juju, steps.MCP_NAME, port=MCP_PORT)}/mcp"
        unit = f"{steps.MCP_NAME}/0"
        chart_id, chart_call = _table_chart_request(juju)

    with and_("generate_chart returns the chart's rows in a table preview"):
        text = _tool_text(_call_tool(url, "call_tool", chart_call))
        previews = json.loads(text).get("previews", {})
        assert "table_data" in previews.get("table", {}), previews

    try:
        with when(f"{config_key} is set to true"):
            steps.set_config(juju, [steps.MCP_NAME], {config_key: True})

        with then(
            "the same request returns only a preview link, with no rows"
        ):
            text = _tool_text(_call_tool(url, "call_tool", chart_call))
            previews = json.loads(text).get("previews", {})
            assert list(previews) == ["url"], previews
            assert "preview_url" in previews["url"], previews
            for key in ("table_data", "ascii_chart", "ascii_content"):
                assert key not in text, text

        with and_("the workload log says previews were restricted to url"):
            _, log = steps.read_workload_file(juju, unit, LOG_FILE)
            tail = "\n".join(log.splitlines()[-200:])
            assert "restricted preview_formats to url" in tail, tail

        with and_("get_chart_data is no longer available"):
            text = _wait_for_tool_error(
                juju,
                url,
                "get_chart_data",
                {"request": {"identifier": chart_id}},
            )
            match = re.search(r"Error ID: (err_\S+)\.", text)
            assert match, text
            error_id = match.group(1)
            _, log = steps.read_workload_file(juju, unit, LOG_FILE)
            tail = "\n".join(log.splitlines()[-200:])
            assert "error_type=NotFoundError" in tail, tail
            assert "Unknown tool: 'get_chart_data'" in tail, tail
            assert error_id in tail, tail

        with and_(
            "tool listings and search offer no preview format except url"
        ):
            _assert_preview_formats_url_only(url)
    finally:
        juju.config(steps.MCP_NAME, reset=config_key)
        steps.wait_for_active(
            juju, [steps.MCP_NAME], timeout=steps.SETTLE_TIMEOUT
        )
