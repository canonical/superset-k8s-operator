"""Rewrite Trino/Ranger permission-denied errors into user-friendly messages."""

import json
import re

from flask import request

# Async-query results are delivered through this endpoint as HTTP 200 with any
# error embedded in the JSON body, so it must be rewritten despite the 2xx status.
_ASYNC_EVENT_PATH = "/api/v1/async_event"

# Trino builds every authorization denial from a `denyXxx` helper on
# `io.trino.spi.security.AccessDeniedException`. The relevant ones read:
# `Access Denied: Cannot <operation> <object>`.
_ACCESS_DENIED = r"Access Denied:\s*"

# Example:
#   Access Denied: Cannot select from columns [html_url, login] in table or view users
_COLUMN_DENIED_PATTERN = re.compile(
    _ACCESS_DENIED
    + r"Cannot select from columns \[(?P<columns>[^\]]+)\]\s+in\s+table\s+or\s+view\s+(?P<table>[\w.\"]+)",
    re.IGNORECASE,
)

# Example:
#   Access Denied: Cannot select from table analytics.default.users
_TABLE_DENIED_PATTERN = re.compile(
    _ACCESS_DENIED + r"Cannot select from table\s+(?P<table>[\w.\"]+)",
    re.IGNORECASE,
)

# Example:
#   Access Denied: Cannot access catalog sales
_CATALOG_DENIED_PATTERN = re.compile(
    _ACCESS_DENIED + r"Cannot access catalog\s+(?P<catalog>[\w-]+)",
    re.IGNORECASE,
)

# A denied read with no dedicated pattern. Writes and DDL are excluded.
_READ_DENIED_PATTERN = re.compile(
    _ACCESS_DENIED + r"Cannot (?:select|access|show)\b",
    re.IGNORECASE,
)

# The keys Superset carries an error message under. Only values found under
# these are candidates, so the `sql` a failed query is echoed back with is
# never rewritten.
_MESSAGE_KEYS = frozenset(
    {"message", "msg", "error", "error_message", "errors"}
)


def _build_request_message(request_url):
    """Build the "request access" line, optionally pointing at a request URL."""
    if request_url:
        return f"Request access:\n {request_url}"
    return "Request access from your administrator"


def _rewrite_permission_denied_string(msg, request_message):
    """Return a rewritten message for a data-access denial, else the original.

    Args:
        msg: the candidate message.
        request_message: the "request access" line to append.

    Returns:
        The rewritten message, or `msg` unchanged when it is not a Trino
        denial of a read.
    """
    if not isinstance(msg, str) or not msg.strip():
        return msg

    m = _COLUMN_DENIED_PATTERN.search(msg)
    if m:
        table = (m.group("table") or "").strip()
        columns = (m.group("columns") or "").strip()

        # Normalize columns formatting a bit
        columns_clean = (
            ", ".join([c.strip() for c in columns.split(",") if c.strip()])
            or columns
        )

        return (
            f"Access to one or more restricted columns in '{table}' was blocked.\n\n"
            f"Restricted columns: {columns_clean}\n\n"
            f"{request_message}"
        )

    # Table-level denial
    m = _TABLE_DENIED_PATTERN.search(msg)
    if m:
        table = (m.group("table") or "").strip()
        return (
            f"Access to table '{table}' is restricted.\n\n"
            f"{request_message}"
        )

    # Catalog-level denial
    m = _CATALOG_DENIED_PATTERN.search(msg)
    if m:
        catalog = (m.group("catalog") or "").strip()
        return (
            f"Access to catalog '{catalog}' is restricted.\n\n"
            f"{request_message}"
        )

    # Any other denied read
    if _READ_DENIED_PATTERN.search(msg):
        return (
            "You don\u2019t have access to this dataset.\n\n"
            f"{request_message}"
        )

    return msg


def _rewrite_any(obj, request_message, key=None):
    """Recursively rewrite denied messages found under a `_MESSAGE_KEYS` key.

    Superset nests errors as `errors[].message`, as `result[].errors[].message`
    on `/api/v1/async_event/`, and as a bare `{"message": ...}`, so the key is
    carried down through lists as well as dicts.

    Args:
        obj: the payload, or part of it.
        request_message: the "request access" line to append.
        key: the dict key this value was found under, or the nearest enclosing
            one when it sits inside a list.

    Returns:
        The payload with any data-access denial rewritten.
    """
    if isinstance(obj, str):
        if key in _MESSAGE_KEYS:
            return _rewrite_permission_denied_string(obj, request_message)
        return obj

    if isinstance(obj, list):
        return [_rewrite_any(x, request_message, key) for x in obj]

    if isinstance(obj, dict):
        return {k: _rewrite_any(v, request_message, k) for k, v in obj.items()}

    return obj


def _should_consider_path(path):
    """Limit rewriting to API-style routes to reduce risk of modifying HTML responses.

    Covers:
    - /api/v1/... (including /api/v1/async_event/)
    - /superset/... JSON endpoints (legacy explore/sql lab)
    """
    if not path:
        return False
    return path.startswith("/api/") or path.startswith("/superset/")


def attach_error_rewriter(app, request_url=None):
    """Register an after_request hook rewriting permission-denied JSON errors.

    Args:
        app: the Flask application to attach the hook to.
        request_url: optional URL users are directed to when requesting access.
    """
    request_message = _build_request_message(request_url)

    @app.after_request
    def _rewrite_response(response):
        try:
            # Only rewrite JSON-ish payloads.
            # Use "json" substring to include: application/json,
            # application/problem+json, etc.
            ctype = (response.headers.get("Content-Type") or "").lower()
            if "json" not in ctype:
                return response

            path = request.path or ""
            if not _should_consider_path(path):
                return response

            # Limit scanning to error responses, plus async-event payloads which
            # carry query errors as HTTP 200 with the error embedded in the body.
            if response.status_code < 400 and not path.startswith(
                _ASYNC_EVENT_PATH
            ):
                return response

            if getattr(response, "direct_passthrough", False):
                return response

            payload = response.get_json(silent=True)
            if payload is None:
                return response

            new_payload = _rewrite_any(payload, request_message)
            if new_payload == payload:
                # Nothing matched, so leave the response as Superset serialised it.
                return response

            response.set_data(json.dumps(new_payload))
            response.headers["Content-Length"] = str(len(response.get_data()))
            return response
        except Exception:
            # Never block responses if rewriting fails
            return response

    return app
