# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tests for templates/mcp_preview_guard.py.

Stub tools mirror Superset's: one `request` parameter typed
`str | RequestModel`, with `preview_formats` inside the model. Calls go
through a real FastMCP server and its in-memory client.

The lint env has the charm's compiled pydantic 1.x, which pylint can't
read, hence the no-name-in-module disable on the pydantic import.
"""

import asyncio
import copy
import json
import types
from typing import Any, Dict, List, Literal, Optional, Union

import mcp_preview_guard as guard
import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from pydantic import (  # pylint: disable=no-name-in-module
    BaseModel,
    Field,
    ValidationError,
)

CHART_URL = "https://superset.example/explore/42"
ASCII_PREVIEW = "region | revenue\nemea   | 1200"
TABLE_ROWS = [{"region": "emea", "revenue": 1200}]
FORMATS_DESCRIPTION = "Previews to return: url, ascii, table or vega_lite."
# Superset tools taking request.preview_formats (chart/tool/*.py).
CHART_TOOLS = ["generate_chart", "update_chart", "update_chart_preview"]


class ChartConfig(BaseModel):
    """Nested config, to prove the schema walk follows `$ref`s.

    Attributes:
        chart_type: Enum with an unrelated `table` member.
        columns: Columns to render.
    """

    chart_type: Literal["xy", "table"] = "xy"
    columns: List[str] = Field(default_factory=list)


class ChartRequest(BaseModel):
    """Shape of Superset's chart request models.

    Attributes:
        chart_name: Chart name.
        config: Nested config.
        viz_type: Enum with an unrelated `table` member.
        preview_formats: Requested previews.
    """

    chart_name: str
    config: ChartConfig
    viz_type: Literal["table", "line", "bar"] = "table"
    preview_formats: List[Literal["url", "ascii", "vega_lite", "table"]] = (
        Field(default_factory=lambda: ["url"], description=FORMATS_DESCRIPTION)
    )


def _request(formats: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build a valid request; `formats=None` leaves preview_formats out."""
    request: Dict[str, Any] = {
        "chart_name": "revenue",
        "config": {"chart_type": "table", "columns": ["region"]},
    }
    if formats is not None:
        request["preview_formats"] = formats
    return request


def _chart_payload() -> Dict[str, Any]:
    """Response carrying every row-bearing key at once."""
    return {
        "chart_id": 42,
        "previews": {
            "url": CHART_URL,
            "ascii": {"ascii_chart": ASCII_PREVIEW},
            "table": {"rows": [["emea", 1200]]},
            "vega_lite": {"data": {"values": TABLE_ROWS}},
        },
        "ascii_chart": ASCII_PREVIEW,
        "ascii_content": ASCII_PREVIEW,
        "table_data": TABLE_ROWS,
    }


def _chart_tool(name: str, seen: Dict[str, Any]) -> Any:
    """Make a chart tool stub that records the preview_formats it got."""

    def _tool(request: Union[str, ChartRequest]) -> dict:
        """Validate like Superset's parse_request, return all previews."""
        try:
            parsed = (
                ChartRequest.model_validate_json(request)
                if isinstance(request, str)
                else ChartRequest.model_validate(request)
            )
        except ValidationError as exc:
            raise ToolError(str(exc)) from None
        seen[name] = parsed.preview_formats
        return _chart_payload()

    return _tool


def _build_server(attach_guard: bool = True) -> tuple:
    """Build a server with Superset-shaped stubs; return it and `seen`."""
    server = FastMCP(name="superset-stub")
    seen: Dict[str, Any] = {}
    for name in CHART_TOOLS:
        server.tool(_chart_tool(name, seen), name=name)

    def _search_tools(query: str) -> dict:
        """Return descriptors embedding input schemas, like tool search."""
        return {
            "results": [
                {
                    "name": "generate_chart",
                    "inputSchema": copy.deepcopy(CHART_SCHEMA),
                },
                {"name": "list_dashboards", "inputSchema": {"type": "object"}},
            ]
        }

    def _list_dashboards() -> dict:
        """Return a payload the guard should not touch."""
        return {"dashboards": [{"dashboard_id": 7, "title": "Sales"}]}

    async def _call_tool(
        name: str, ctx: Context, arguments: Optional[dict] = None
    ) -> Any:
        """Superset's tool-search proxy: JSON-encodes `request`, re-enters."""
        if arguments and "request" in arguments:
            arguments = {
                **arguments,
                "request": json.dumps(arguments["request"]),
            }
        return await ctx.fastmcp.call_tool(name, arguments)

    server.tool(_search_tools, name="search_tools")
    server.tool(_list_dashboards, name="list_dashboards")
    server.tool(_call_tool, name="call_tool")
    if attach_guard:
        guard._add_middleware(server)
    return server, seen


def _run(server: FastMCP, method: str, *args: Any) -> Any:
    """Call a client method against `server` over the in-memory transport."""

    async def _go() -> Any:
        """Open a session and make the call."""
        async with Client(server) as client:
            if method == "call_tool":
                return await client.call_tool(*args, raise_on_error=False)
            return await getattr(client, method)(*args)

    return asyncio.run(_go())


def _call(server: FastMCP, name: str, arguments: Optional[dict] = None) -> Any:
    """Call a tool and return the CallToolResult."""
    return _run(server, "call_tool", name, arguments or {})


def _text(result: Any) -> str:
    """Join a result's text blocks."""
    return "".join(getattr(block, "text", "") for block in result.content)


def _formats_prop(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Get preview_formats from a `$defs`-form chart schema."""
    return schema["$defs"]["ChartRequest"]["properties"]["preview_formats"]


def _assert_names_no_hidden_format(schema: Any) -> None:
    """No hidden format named, nothing said to be removed."""
    rendered = json.dumps(schema).lower()
    for banned in ("ascii", "vega_lite", "removed", "disabled"):
        assert banned not in rendered, banned


def _guard_count(server: Any) -> int:
    """Count the PreviewGuardMiddleware instances on `server`."""
    return sum(
        isinstance(m, guard.PreviewGuardMiddleware) for m in server.middleware
    )


def _events(caplog: Any, event: str) -> list:
    """Log records for one guard event."""
    name = f"mcp_preview_guard.{event}"
    return [r for r in caplog.records if getattr(r, "event", "") == name]


_SERVER, _ = _build_server(attach_guard=False)
# What FastMCP holds for a chart tool (before ref inlining): this is what
# tools/list and search results carry.
CHART_SCHEMA: Dict[str, Any] = asyncio.run(
    _SERVER.get_tool("generate_chart")
).parameters


class TestRestrictArguments:
    """Argument restriction edge cases not covered end to end."""

    @pytest.mark.parametrize(
        "arguments",
        [
            {"request": _request(["url"])},
            {"request": json.dumps(_request(["url"]))},
            {"request": "not json"},
            {},
        ],
    )
    def test_leaves_alone(self, arguments):
        """Already-safe, unparsable and request-less calls are untouched."""
        restricted, changed = guard.restrict_arguments(
            "generate_chart", arguments
        )

        assert (restricted, changed) == (arguments, False)

    def test_ignores_unguarded_tools(self):
        """Only guarded tools are restricted."""
        arguments = {"request": _request(["table"])}

        assert guard.restrict_arguments("list_dashboards", arguments) == (
            arguments,
            False,
        )

    def test_restricts_inside_request_only(self):
        """A top-level decoy is ignored, never added, and input is copied."""
        arguments = {
            "preview_formats": ["url"],
            "request": _request(["table"]),
        }

        restricted, changed = guard.restrict_arguments(
            "generate_chart", arguments
        )

        assert changed is True
        assert restricted["request"]["preview_formats"] == ["url"]
        assert arguments["request"]["preview_formats"] == ["table"]


class TestSanitizeResult:
    """Response sanitization."""

    def test_removes_data_keys_and_keeps_the_url(self):
        """Every data key goes, at any depth; unknown formats too."""
        payload = {
            **_chart_payload(),
            "charts": [{"id": 1, "table_data": TABLE_ROWS}],
        }
        payload["previews"]["interactive"] = {"html": "<script/>"}

        cleaned, removed = guard.sanitize_result(payload)

        assert cleaned == {
            "chart_id": 42,
            "previews": {"url": CHART_URL},
            "charts": [{"id": 1}],
        }
        assert removed == [
            "ascii_chart",
            "ascii_content",
            "previews.ascii",
            "previews.interactive",
            "previews.table",
            "previews.vega_lite",
            "table_data",
        ]

    def test_json_text(self):
        """JSON text is sanitized; plain text passes through."""
        cleaned, _ = guard.sanitize_json_text(json.dumps(_chart_payload()))

        assert ASCII_PREVIEW not in cleaned
        assert guard.sanitize_json_text("not json") == ("not json", [])


class TestSanitizeToolSchema:
    """Advertised schema sanitization."""

    def test_sanitizes_preview_formats_only(self):
        """Only url offered or mentioned; other enums untouched."""
        original = copy.deepcopy(CHART_SCHEMA)

        schema = guard.sanitize_tool_schema("generate_chart", original)

        prop = _formats_prop(schema)
        assert prop["items"]["enum"] == ["url"]
        assert prop["default"] == ["url"]
        _assert_names_no_hidden_format(schema)
        model = schema["$defs"]["ChartRequest"]["properties"]
        assert model["viz_type"]["enum"] == ["table", "line", "bar"]
        config = schema["$defs"]["ChartConfig"]["properties"]
        assert config["chart_type"]["enum"] == ["xy", "table"]
        assert schema["properties"] == CHART_SCHEMA["properties"]
        assert original == CHART_SCHEMA

    def test_walks_anyof(self):
        """An optional (anyOf) preview_formats is sanitized too."""
        enum = {"enum": ["url", "ascii", "table"], "type": "string"}
        schema = guard.sanitize_tool_schema(
            "generate_chart",
            {
                "properties": {
                    "preview_formats": {
                        "anyOf": [
                            {"type": "array", "items": enum},
                            {"type": "null"},
                        ]
                    }
                }
            },
        )

        prop = schema["properties"]["preview_formats"]
        assert prop["items"]["enum"] == ["url"]
        assert "anyOf" not in prop


class TestServerEndToEnd:
    """Real FastMCP server, real client."""

    @pytest.mark.parametrize("tool", CHART_TOOLS)
    @pytest.mark.parametrize(
        "request_arg",
        [
            _request(["table", "ascii"]),
            _request(["x-ray"]),
            _request(),
            json.dumps(_request(["ascii"])),
        ],
        ids=["data", "off-schema", "omitted", "json-string"],
    )
    def test_tool_sees_url_client_sees_no_rows(self, tool, request_arg):
        """Every call shape reaches the tool as url-only, data keys removed."""
        server, seen = _build_server()

        result = _call(server, tool, {"request": request_arg})

        assert result.is_error is False
        assert seen[tool] == ["url"]
        assert result.structured_content["previews"] == {"url": CHART_URL}
        assert ASCII_PREVIEW not in _text(result)
        assert "table_data" not in _text(result)

    def test_search_proxy_call_is_restricted(self):
        """The tool-search proxy re-enters the middleware chain."""
        server, seen = _build_server()

        result = _call(
            server,
            "call_tool",
            {
                "name": "generate_chart",
                "arguments": {"request": _request(["table"])},
            },
        )

        assert seen["generate_chart"] == ["url"]
        assert ASCII_PREVIEW not in _text(result)

    def test_unparsable_request_fails_closed(self):
        """A request the guard can't read never reaches the tool."""
        server, seen = _build_server()

        result = _call(server, "generate_chart", {"request": "{oops"})

        assert result.is_error is True
        assert not seen

    def test_listed_and_searched_schemas_offer_url_only(self):
        """tools/list and search_tools results both advertise url only."""
        server, _ = _build_server()

        listed = _run(server, "list_tools")
        tool = next(t for t in listed if t.name == "generate_chart")
        searched = _call(server, "search_tools", {"query": "chart"})
        found = {r["name"]: r for r in searched.structured_content["results"]}

        listed_prop = tool.inputSchema["properties"]["request"]["anyOf"][1][
            "properties"
        ]["preview_formats"]
        searched_prop = _formats_prop(found["generate_chart"]["inputSchema"])
        for prop in (listed_prop, searched_prop):
            assert prop["items"]["enum"] == ["url"]
            assert prop["default"] == ["url"]
            _assert_names_no_hidden_format(prop)
        assert found["list_dashboards"]["inputSchema"] == {"type": "object"}

    def test_unguarded_tool_is_unchanged(self):
        """A tool the guard doesn't own behaves as without the guard."""
        guarded, _ = _build_server()
        plain, _ = _build_server(attach_guard=False)

        with_guard = _call(guarded, "list_dashboards")
        without = _call(plain, "list_dashboards")

        assert with_guard.structured_content == without.structured_content
        assert _text(with_guard) == _text(without)


class TestAddMiddleware:
    """Middleware registration."""

    def test_adds_once_outermost(self):
        """Prepended once; a second add is a no-op."""
        server = FastMCP(name="t")
        existing = Middleware()
        server.add_middleware(existing)

        assert guard._add_middleware(server) is True
        assert guard._add_middleware(server) is False
        assert _guard_count(server) == 1
        assert isinstance(server.middleware[0], guard.PreviewGuardMiddleware)
        assert existing in server.middleware

    def test_touches_only_tool_hooks_and_middleware(self):
        """Auth and other hooks are left alone."""
        server = FastMCP(name="t")
        before = {k: id(v) for k, v in vars(server).items()}

        guard._add_middleware(server)

        after = {k: id(v) for k, v in vars(server).items()}
        assert before == after
        overridden = {
            name
            for name in dir(Middleware)
            if name.startswith("on_")
            and getattr(guard.PreviewGuardMiddleware, name)
            is not getattr(Middleware, name)
        }
        assert overridden == {"on_call_tool", "on_list_tools"}


@pytest.fixture(name="server_module")
def server_module_fixture():
    """Fake `superset.mcp_service.server`; `run_server` looks up late."""
    module = types.ModuleType("superset.mcp_service.server")
    shared = FastMCP(name="global")

    def _run_server(use_factory_config: bool = False) -> FastMCP:
        """Build the served instance, as upstream does."""
        # pylint can't see attributes set on a ModuleType at runtime.
        # pylint: disable=no-member
        if use_factory_config:
            return module.create_mcp_app()
        return module.init_fastmcp_server(auth=None, middleware=[])

    module.create_mcp_app = lambda **kwargs: FastMCP(name="factory")
    module.init_fastmcp_server = lambda **kwargs: shared
    module.run_server = _run_server
    return module


class TestAttachTo:
    """Guarding whatever run_server() serves."""

    @pytest.mark.parametrize("use_factory_config", [True, False])
    def test_served_instance_is_guarded_once(
        self, server_module, use_factory_config
    ):
        """Both construction paths; re-attaching is a no-op."""
        assert guard.attach_to(server_module) == [
            "create_mcp_app",
            "init_fastmcp_server",
        ]
        assert guard.attach_to(server_module) == []

        served = server_module.run_server(use_factory_config)

        assert _guard_count(served) == 1

    def test_missing_factory_is_logged(self, server_module, caplog):
        """An upstream rename is reported, the other path still attached."""
        del server_module.create_mcp_app

        with caplog.at_level("WARNING", logger="mcp_preview_guard"):
            attached = guard.attach_to(server_module)

        assert attached == ["init_fastmcp_server"]
        assert [r.factory for r in _events(caplog, "missing_factory")] == [
            "create_mcp_app"
        ]


class TestLogging:
    """Logs carry names, never values."""

    @pytest.mark.parametrize(
        "formats, supplied", [(["dump_everything"], True), (None, False)]
    )
    def test_restriction_log(self, caplog, formats, supplied):
        """Restriction logs the tool and whether the client asked."""
        server, _ = _build_server()

        with caplog.at_level("INFO", logger="mcp_preview_guard"):
            _call(server, "generate_chart", {"request": _request(formats)})

        [record] = _events(caplog, "restricted_arguments")
        assert record.tool == "generate_chart"
        assert record.client_supplied is supplied
        assert "dump_everything" not in record.getMessage()

    def test_sanitize_log_has_keys_not_rows(self, caplog):
        """Removed data keys are logged by name only."""
        server, _ = _build_server()

        with caplog.at_level("WARNING", logger="mcp_preview_guard"):
            _call(server, "generate_chart", {"request": _request()})

        [record] = _events(caplog, "sanitized_result")
        assert "table_data" in record.removed_keys
        assert ASCII_PREVIEW not in record.getMessage()
