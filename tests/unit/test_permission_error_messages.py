# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the permission-denied error message rewriter.

The rewriter lives in templates/permission_error_messages.py and is loaded by
every Superset process at startup via PYTHONPATH. These tests stub out Flask so
the module can be imported and exercised with no installed Flask/Superset
package or running Juju model required.
"""

import importlib.util
import pathlib
import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Bootstrap: import the module with a stubbed `flask` package
# ---------------------------------------------------------------------------

_request_stub = types.SimpleNamespace(path="")
_flask_stub = types.ModuleType("flask")
setattr(_flask_stub, "request", _request_stub)
sys.modules.setdefault("flask", _flask_stub)

_MODULE_PATH = (
    pathlib.Path(__file__).parent.parent.parent
    / "templates"
    / "permission_error_messages.py"
)
_spec = importlib.util.spec_from_file_location(
    "permission_error_messages", _MODULE_PATH
)
assert _spec is not None and _spec.loader is not None
pem = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pem)


# ---------------------------------------------------------------------------
# Test doubles for the Flask app/response used by attach_error_rewriter
# ---------------------------------------------------------------------------


class _FakeApp:
    """Captures the after_request hook so tests can invoke it directly.

    Attrs:
        hook: the function registered via after_request.
    """

    def __init__(self):
        """Initialise with no hook registered."""
        self.hook = None

    def after_request(self, func):
        """Store the registered hook and return it unchanged.

        Args:
            func: the after_request callback.

        Returns:
            The callback, unchanged.
        """
        self.hook = func
        return func


class _FakeResponse:
    """Minimal stand-in for a Flask response object.

    Attrs:
        status_code: the HTTP status code of the response.
        headers: the response headers.
        direct_passthrough: whether the response streams data directly.
    """

    def __init__(
        self, payload, status_code=200, content_type="application/json"
    ):
        """Initialise with a JSON payload and metadata.

        Args:
            payload: the object returned by get_json.
            status_code: the HTTP status code.
            content_type: the Content-Type header value.
        """
        self._payload = payload
        self._data = b""
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.direct_passthrough = False

    def get_json(self, silent=False):
        """Return the stored payload.

        Args:
            silent: ignored; present for API compatibility.

        Returns:
            The stored payload.
        """
        return self._payload

    def get_data(self):
        """Return the bytes set via set_data.

        Returns:
            The stored response body bytes.
        """
        return self._data

    def set_data(self, data):
        """Store the response body.

        Args:
            data: the new body, as str or bytes.
        """
        self._data = data.encode() if isinstance(data, str) else data


class TestRequestMessage(unittest.TestCase):
    """The "request access" line reflects the configured URL."""

    def test_with_url(self):
        """A configured URL appears in the request-access message."""
        msg = pem._build_request_message("https://example.com/request")
        self.assertIn("https://example.com/request", msg)

    def test_without_url_falls_back_to_admin(self):
        """An unset URL yields the contact-administrator message."""
        self.assertEqual(
            pem._build_request_message(None),
            "Request access from your administrator",
        )


REQ = "Request access from your administrator"


class TestRewriteString(unittest.TestCase):
    """Permission-denied strings are rewritten; others pass through."""

    def test_column_denied(self):
        """A column-level denial names the table and restricted columns."""
        msg = (
            "Access Denied: Cannot select from columns "
            "[html_url, login] in table or view users"
        )
        out = pem._rewrite_permission_denied_string(msg, REQ)
        self.assertIn("restricted columns in 'users'", out)
        self.assertIn("html_url, login", out)
        self.assertIn(REQ, out)

    def test_catalog_denied(self):
        """A catalog-level denial names the restricted catalog."""
        msg = "Access Denied: Cannot access catalog sales"
        out = pem._rewrite_permission_denied_string(msg, REQ)
        self.assertIn("catalog 'sales'", out)
        self.assertIn(REQ, out)

    def test_generic_permission_denied(self):
        """Generic denial markers map to the default dataset message."""
        for msg in ("PERMISSION_DENIED", "User is not authorized"):
            out = pem._rewrite_permission_denied_string(msg, REQ)
            self.assertIn("don’t have access to this dataset", out)

    def test_non_denied_unchanged(self):
        """A non-permission error is returned unchanged."""
        msg = "Query timed out after 30 seconds"
        self.assertEqual(pem._rewrite_permission_denied_string(msg, REQ), msg)

    def test_rewrite_any_nested(self):
        """Denied strings nested in dicts/lists are rewritten in place."""
        payload = {"errors": [{"message": "Cannot access catalog sales"}]}
        out = pem._rewrite_any(payload, REQ)
        self.assertIn("catalog 'sales'", out["errors"][0]["message"])


class TestHookGating(unittest.TestCase):
    """The after_request hook only rewrites the intended responses."""

    def setUp(self):
        """Register the hook on a fake app and capture it."""
        self.app = _FakeApp()
        pem.attach_error_rewriter(self.app, request_url=None)
        self.hook = self.app.hook

    def _run(self, response, path):
        """Invoke the hook for a response on a given request path.

        Args:
            response: the fake response to pass through the hook.
            path: the request path to simulate.

        Returns:
            The (possibly mutated) response.
        """
        _request_stub.path = path
        return self.hook(response)

    def test_error_response_rewritten(self):
        """A 4xx JSON error on an API path is rewritten."""
        resp = _FakeResponse(
            {"message": "Cannot access catalog sales"}, status_code=403
        )
        self._run(resp, "/api/v1/chart/data")
        self.assertIn("catalog 'sales'", resp.get_data().decode())

    def test_async_event_200_rewritten(self):
        """An async-event 200 with an embedded error is rewritten."""
        resp = _FakeResponse(
            {"message": "Cannot access catalog sales"}, status_code=200
        )
        self._run(resp, "/api/v1/async_event/")
        self.assertIn("catalog 'sales'", resp.get_data().decode())

    def test_successful_non_async_response_untouched(self):
        """A 2xx non-async response is left untouched."""
        resp = _FakeResponse(
            {"message": "Cannot access catalog sales"}, status_code=200
        )
        self._run(resp, "/api/v1/chart/data")
        # Hook returned early without re-serialising the body.
        self.assertEqual(resp.get_data(), b"")

    def test_non_json_untouched(self):
        """A non-JSON response is left untouched."""
        resp = _FakeResponse(
            {"message": "Cannot access catalog sales"},
            status_code=500,
            content_type="text/html",
        )
        self._run(resp, "/api/v1/chart/data")
        self.assertEqual(resp.get_data(), b"")

    def test_non_api_path_untouched(self):
        """A response on a non-API path is left untouched."""
        resp = _FakeResponse(
            {"message": "Cannot access catalog sales"}, status_code=500
        )
        self._run(resp, "/static/something")
        self.assertEqual(resp.get_data(), b"")


if __name__ == "__main__":
    unittest.main()
