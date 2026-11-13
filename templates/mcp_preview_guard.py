# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Restrict Superset's MCP chart previews to `url`.

`generate_chart` and `update_chart` accept `preview_formats`. The
`ascii`, `table` and `vega_lite` formats run the chart query and return
rows in the response; `url` only returns a link that goes through
Superset's own permission checks.

Defences, outermost first:

1. `restrict_arguments()` restricts `request.preview_formats` to `["url"]`.
2. `sanitize_result()` removes data keys from every tool's response,
   not only the guarded tools'.
3. `sanitize_tool_schema()` hides the other formats from the model.
4. `PreviewGuardMiddleware` wires 1-3 into FastMCP's tools/call and
   tools/list hooks. No other hook is touched (auth/OAuth untouched).
5. `attach_to()` attaches the middleware to every server the MCP
   service builds.
"""

import functools
import json
import logging
from typing import Any, Iterable, Sequence

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

logger = logging.getLogger(__name__)

# The chart tools whose request payload carries preview_formats.
# Upstream Superset 6.1.0 defines preview_formats only on these three
# tools. Re-check on every Superset upgrade (CONTRIBUTING.md, "MCP preview
# guard audit").
GUARDED_TOOLS = frozenset(
    {"generate_chart", "update_chart", "update_chart_preview"}
)

# `preview_formats` lives inside the tools' single `request` argument.
REQUEST_ARG = "request"
PREVIEW_FORMATS_ARG = "preview_formats"
PREVIEW_FORMAT_URL = "url"
ALLOWED_PREVIEW_FORMATS = [PREVIEW_FORMAT_URL]

# Formats that execute the chart query.
DATA_PREVIEW_FORMATS = frozenset({"ascii", "table", "vega_lite"})
ALL_PREVIEW_FORMATS = DATA_PREVIEW_FORMATS | {PREVIEW_FORMAT_URL}

# Data keys: response keys that carry chart rows. `previews` is sanitized
# per format so `previews.url` survives.
PREVIEWS_KEY = "previews"
DATA_RESULT_KEYS = frozenset({"ascii_chart", "ascii_content", "table_data"})

# Input-schema keys of a tool descriptor; `search_tools` responses embed
# descriptors, so these are sanitized inside responses too.
SCHEMA_KEYS = frozenset(
    {"inputSchema", "input_schema", "parameters", "schema"}
)

# Shown to the model; must not mention the hidden formats.
PREVIEW_FORMATS_DESCRIPTION = (
    "Preview to return for the chart. The only supported value is "
    "`url`: the response carries a link to the chart in Superset, "
    "which opens with the caller's own permissions."
)

# Factories run_server() uses to build the served instance.
SERVER_FACTORY_NAMES = ("create_mcp_app", "init_fastmcp_server")
ATTACHED_MARKER = "_superset_preview_guard_attached"


def restrict_arguments(
    tool_name: str, arguments: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, bool]:
    """Restrict `request.preview_formats` to `["url"]` for guarded tools.
    Handles `request` as a dict or a JSON string (parse-request mode and
    the tool-search proxy). A top-level `preview_formats` is ignored: the
    tools have no such parameter.

    Returns:
        `(arguments, changed)`.
    """
    if tool_name not in GUARDED_TOOLS:
        return arguments, False

    request = (arguments or {}).get(REQUEST_ARG)
    if isinstance(request, str):
        restricted_request, changed = _restrict_request_text(request)
    else:
        restricted_request, changed = _restrict_request_object(request)
    if not changed:
        return arguments, False

    restricted = dict(arguments or {})
    restricted[REQUEST_ARG] = restricted_request
    return restricted, True


def _restrict_request_object(request: Any) -> tuple[Any, bool]:
    """Set `preview_formats` on a dict request; non-dicts pass through."""
    if not isinstance(request, dict):
        return request, False
    if request.get(PREVIEW_FORMATS_ARG) == ALLOWED_PREVIEW_FORMATS:
        return request, False

    restricted = dict(request)
    restricted[PREVIEW_FORMATS_ARG] = list(ALLOWED_PREVIEW_FORMATS)
    return restricted, True


def _restrict_request_text(request: str) -> tuple[str, bool]:
    """Restrict `preview_formats` on a JSON-string request.

    Invalid JSON passes through; upstream rejects it.
    """
    try:
        decoded = json.loads(request)
    except ValueError:
        logger.warning(
            "mcp-preview-guard: request argument is not JSON, leaving it "
            "for upstream to reject",
            extra={"event": "mcp_preview_guard.unparsable_request"},
        )
        return request, False

    restricted, changed = _restrict_request_object(decoded)
    if not changed:
        return request, False
    return json.dumps(restricted), True


def _client_supplied_preview_formats(
    arguments: dict[str, Any] | None,
) -> bool:
    """Whether the caller set `preview_formats` itself (for logging)."""
    request = (arguments or {}).get(REQUEST_ARG)
    if isinstance(request, str):
        try:
            request = json.loads(request)
        except ValueError:
            return False
    return isinstance(request, dict) and PREVIEW_FORMATS_ARG in request


def sanitize_result(obj: Any) -> tuple[Any, list[str]]:
    """Remove data keys and sanitize embedded guarded-tool schemas.

    Returns:
        `(cleaned, removed_keys)`; names only, never values.
    """
    removed: list[str] = []
    cleaned = _sanitize_node(obj, removed)
    return cleaned, sorted(set(removed))


def sanitize_json_text(text: str) -> tuple[str, list[str]]:
    """Run `sanitize_result()` on JSON text; other text passes through."""
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        return text, []

    if not isinstance(decoded, (dict, list)):
        return text, []

    cleaned, removed = sanitize_result(decoded)
    if cleaned == decoded:
        return text, []
    return json.dumps(cleaned), removed


def sanitize_tool_schema(
    tool_name: str, schema: dict[str, Any]
) -> dict[str, Any]:
    """Return a guarded tool's input schema sanitized to only offer `url`.

    Follows `properties`, `$defs`, `anyOf`/`oneOf`/`allOf` and `items`,
    where the preview-format enum usually sits.
    """
    if tool_name not in GUARDED_TOOLS or not isinstance(schema, dict):
        return schema
    return _sanitize_schema_node(schema)


def _sanitize_node(node: Any, removed: list[str]) -> Any:
    """Recursive worker for `sanitize_result()`; appends to `removed`."""
    if isinstance(node, list):
        return [_sanitize_node(item, removed) for item in node]
    if not isinstance(node, dict):
        return node

    name = node.get("name")
    describes_guarded_tool = isinstance(name, str) and name in GUARDED_TOOLS

    cleaned: dict[Any, Any] = {}
    for key, value in node.items():
        if key in DATA_RESULT_KEYS:
            removed.append(str(key))
            continue

        if key == PREVIEWS_KEY and isinstance(value, dict):
            kept: dict[Any, Any] = {}
            for fmt, preview in value.items():
                if fmt != PREVIEW_FORMAT_URL:
                    removed.append(f"{PREVIEWS_KEY}.{fmt}")
                    continue
                kept[fmt] = _sanitize_node(preview, removed)
            cleaned[key] = kept
            continue

        if (
            describes_guarded_tool
            and key in SCHEMA_KEYS
            and isinstance(value, dict)
        ):
            cleaned[key] = sanitize_tool_schema(str(name), value)
            continue

        cleaned[key] = _sanitize_node(value, removed)
    return cleaned


def _is_preview_format_enum(values: Any) -> bool:
    """Whether `values` is a preview-format enum.

    Strict match, since unrelated enums (e.g. `viz_type`) contain `table`.
    """
    if not isinstance(values, list) or not values:
        return False
    if not all(isinstance(value, str) for value in values):
        return False
    members = set(values)
    return bool(members & DATA_PREVIEW_FORMATS) and members.issubset(
        ALL_PREVIEW_FORMATS
    )


def _url_only(value: Any) -> Any:
    """Map a preview-format default to its url-only equivalent."""
    if isinstance(value, list):
        return list(ALLOWED_PREVIEW_FORMATS)
    if isinstance(value, str) and value in ALL_PREVIEW_FORMATS:
        return PREVIEW_FORMAT_URL
    return value


def _url_only_preview_formats_schema(original: Any) -> dict[str, Any]:
    """Replacement schema for the `preview_formats` property: `["url"]`."""
    url_only: dict[str, Any] = {
        "type": "array",
        "items": {
            "type": "string",
            "enum": list(ALLOWED_PREVIEW_FORMATS),
        },
        "maxItems": 1,
        "default": list(ALLOWED_PREVIEW_FORMATS),
        "description": PREVIEW_FORMATS_DESCRIPTION,
    }
    if isinstance(original, dict) and isinstance(original.get("title"), str):
        url_only["title"] = original["title"]
    return url_only


def _sanitize_schema_node(node: Any) -> Any:
    """Recursive worker for `sanitize_tool_schema()`."""
    if isinstance(node, list):
        return [_sanitize_schema_node(item) for item in node]
    if not isinstance(node, dict):
        return node

    items = node.get("items")
    names_formats = _is_preview_format_enum(
        node.get("enum")
    ) or _is_preview_format_enum(
        items.get("enum") if isinstance(items, dict) else None
    )

    sanitized: dict[Any, Any] = {}
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            sanitized[key] = {
                name: (
                    _url_only_preview_formats_schema(sub)
                    if name == PREVIEW_FORMATS_ARG
                    else _sanitize_schema_node(sub)
                )
                for name, sub in value.items()
            }
        else:
            sanitized[key] = _sanitize_schema_node(value)

    if names_formats:
        sanitized = _sanitize_preview_format_node(sanitized)
    return sanitized


def _sanitize_preview_format_node(node: dict[str, Any]) -> dict[str, Any]:
    """Restrict a recognised preview-format enum node to `url`."""
    sanitized = dict(node)
    if "enum" in sanitized:
        sanitized["enum"] = list(ALLOWED_PREVIEW_FORMATS)
    items = sanitized.get("items")
    if isinstance(items, dict) and "enum" in items:
        sanitized["items"] = dict(items, enum=list(ALLOWED_PREVIEW_FORMATS))
    if "default" in sanitized:
        sanitized["default"] = _url_only(sanitized["default"])
    if "description" in sanitized:
        sanitized["description"] = PREVIEW_FORMATS_DESCRIPTION
    return sanitized


def sanitize_tool_result(tool_name: str, result: Any) -> Any:
    """Sanitize a ToolResult's structured content, meta and JSON text blocks.

    Returns a copy, or the original when nothing changed.
    """
    if not hasattr(result, "model_copy"):
        return result

    removed: list[str] = []
    updates: dict[str, Any] = {}

    for field in ("structured_content", "meta"):
        value = getattr(result, field, None)
        if not isinstance(value, dict):
            continue
        cleaned, keys = sanitize_result(value)
        if cleaned != value:
            updates[field] = cleaned
            removed.extend(keys)

    content = getattr(result, "content", None)
    if isinstance(content, (list, tuple)):
        blocks = []
        changed = False
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                cleaned_text, keys = sanitize_json_text(text)
                if cleaned_text != text:
                    changed = True
                    removed.extend(keys)
                    blocks.append(
                        block.model_copy(update={"text": cleaned_text})
                    )
                    continue
            blocks.append(block)
        if changed:
            updates["content"] = blocks

    if not updates:
        return result

    if removed:
        _log_removed(tool_name, sorted(set(removed)))
    else:
        logger.debug(
            "mcp-preview-guard: sanitized embedded tool schema (tool=%s)",
            tool_name,
            extra={
                "event": "mcp_preview_guard.sanitized_embedded_schema",
                "tool": tool_name,
            },
        )
    return result.model_copy(update=updates)


def _log_removed(tool_name: str, keys: Sequence[str]) -> None:
    """Log removed key names. Never log values: they are the rows."""
    logger.warning(
        "mcp-preview-guard: sanitized result, removed data keys "
        "(tool=%s, keys=%s)",
        tool_name,
        ",".join(keys),
        extra={
            "event": "mcp_preview_guard.sanitized_result",
            "tool": tool_name,
            "removed_keys": list(keys),
        },
    )


class PreviewGuardMiddleware(Middleware):
    """Restrict chart arguments, sanitize results and advertised schemas."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """Restrict arguments before upstream validation, sanitize result."""
        params = context.message
        tool_name = getattr(params, "name", "")
        arguments = getattr(params, "arguments", None)

        restricted, changed = restrict_arguments(tool_name, arguments)
        if changed:
            supplied = _client_supplied_preview_formats(arguments)
            logger.info(
                "mcp-preview-guard: restricted preview_formats to url "
                "(tool=%s, client_supplied=%s)",
                tool_name,
                supplied,
                extra={
                    "event": "mcp_preview_guard.restricted_arguments",
                    "tool": tool_name,
                    "client_supplied": supplied,
                },
            )
            context = context.copy(
                message=params.model_copy(update={"arguments": restricted})
            )

        result = await call_next(context)
        return sanitize_tool_result(tool_name, result)

    async def on_list_tools(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Sequence[Any]:
        """Sanitize advertised schemas; copies tools, never mutates them."""
        tools: Iterable[Any] = await call_next(context)

        listed = []
        for tool in tools:
            name = getattr(tool, "name", "")
            parameters = getattr(tool, "parameters", None)
            if name in GUARDED_TOOLS and isinstance(parameters, dict):
                sanitized = sanitize_tool_schema(name, parameters)
                if sanitized != parameters:
                    logger.debug(
                        "mcp-preview-guard: sanitized advertised schema "
                        "(tool=%s)",
                        name,
                        extra={
                            "event": "mcp_preview_guard.sanitized_tool_schema",
                            "tool": name,
                        },
                    )
                    tool = tool.model_copy(update={"parameters": sanitized})
            listed.append(tool)
        return listed


def _add_middleware(mcp_server: Any) -> bool:
    """Add the guard as the outermost middleware, once per server.

    Returns:
        True if added, False if already present.
    """
    present = getattr(mcp_server, "middleware", None) or []
    if any(
        isinstance(middleware, PreviewGuardMiddleware)
        for middleware in present
    ):
        logger.debug(
            "mcp-preview-guard: already attached",
            extra={"event": "mcp_preview_guard.already_attached"},
        )
        return False

    middleware_instance = PreviewGuardMiddleware()
    if hasattr(mcp_server, "middleware") and isinstance(
        mcp_server.middleware, list
    ):
        mcp_server.middleware.insert(0, middleware_instance)
    else:
        mcp_server.add_middleware(middleware_instance)
    logger.info(
        "mcp-preview-guard: attached, chart previews restricted to url "
        "(tools=%s)",
        ",".join(sorted(GUARDED_TOOLS)),
        extra={
            "event": "mcp_preview_guard.attached",
            "tools": sorted(GUARDED_TOOLS),
        },
    )
    return True


def _attached_factory(factory: Any) -> Any:
    """Wrap a server factory to attach the guard to the server it builds."""

    @functools.wraps(factory)
    def attached_factory(*args: Any, **kwargs: Any) -> Any:
        """Call the upstream factory, then attach the guard."""
        instance = factory(*args, **kwargs)
        if instance is not None:
            _add_middleware(instance)
        return instance

    setattr(attached_factory, ATTACHED_MARKER, True)
    return attached_factory


def attach_to(server_module: Any) -> list[str]:
    """Attach the preview guard to `server_module`'s factories.

    Wraps `create_mcp_app` and `init_fastmcp_server` so whichever path
    `run_server()` takes yields a guarded instance. Idempotent.

    Returns:
        Factory names attached by this call.
    """
    attached = []
    for name in SERVER_FACTORY_NAMES:
        factory = getattr(server_module, name, None)
        if not callable(factory):
            logger.warning(
                "mcp-preview-guard: no %s on the MCP server module, that "
                "construction path is unguarded",
                name,
                extra={
                    "event": "mcp_preview_guard.missing_factory",
                    "factory": name,
                },
            )
            continue
        if getattr(factory, ATTACHED_MARKER, False):
            continue
        setattr(server_module, name, _attached_factory(factory))
        attached.append(name)

    if attached:
        logger.info(
            "mcp-preview-guard: attached to MCP server factories (names=%s)",
            ",".join(attached),
            extra={
                "event": "mcp_preview_guard.attached_to_factories",
                "factories": list(attached),
            },
        )
    return attached


__all__ = [
    "ALLOWED_PREVIEW_FORMATS",
    "DATA_PREVIEW_FORMATS",
    "DATA_RESULT_KEYS",
    "GUARDED_TOOLS",
    "PREVIEW_FORMATS_ARG",
    "REQUEST_ARG",
    "SERVER_FACTORY_NAMES",
    "PreviewGuardMiddleware",
    "attach_to",
    "restrict_arguments",
    "sanitize_json_text",
    "sanitize_result",
    "sanitize_tool_result",
    "sanitize_tool_schema",
]
