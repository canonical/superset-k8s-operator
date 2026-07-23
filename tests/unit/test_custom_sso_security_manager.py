# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the CustomSecurityManager raise_for_access override.

The override lives in templates/custom_sso_security_manager.py and is loaded
by every Superset process at startup.  These tests stub the Superset and
Flask dependencies so the class can be instantiated and exercised without an
installed Superset package or running Juju model.
"""

import os
import pathlib
import sys
import types
import unittest
from unittest import mock


class _AccessDenied(Exception):
    """Represent a denied call from the stub security manager."""


def _setup_stubs():  # pylint: disable=too-many-locals
    """Create lightweight stubs so the template can import.

    Returns:
        Tuple of (Query class, flask g namespace, module mapping).
    """
    query_mod = types.ModuleType("superset.models.sql_lab")

    class Query:
        """Stub for superset.models.sql_lab.Query.

        Attrs:
            user_id: The owning user's ID.
            database: The database used by the query.
            sql: The SQL submitted through SQL Lab.
        """

        user_id = None
        database = None
        sql = None

    query_mod.Query = Query

    class SupersetSecurityManager:
        """Stub base security manager that records call arguments."""

        _is_admin = False
        _last_call = None
        _can_access_database = False
        _deny_datasource = False
        _deny_query = False

        def raise_for_access(self, *args, **kwargs):
            """Record the call arguments and apply configured denials.

            Args:
                args: Positional arguments.
                kwargs: Keyword arguments.

            Raises:
                _AccessDenied: If the configured resource is denied.
            """
            self._last_call = (args, kwargs)
            if "datasource" in kwargs and self._deny_datasource:
                raise _AccessDenied
            if "query" in kwargs and self._deny_query:
                raise _AccessDenied

        def can_access_database(self, database):
            """Return the configured database access decision.

            Args:
                database: Database being authorized.

            Returns:
                The configured database access decision.
            """
            del database
            return self._can_access_database

        def is_admin(self):
            """Return the pre-configured admin flag.

            Returns:
                The value of _is_admin.
            """
            return self._is_admin

    security_mod = types.ModuleType("superset.security")
    security_mod.SupersetSecurityManager = SupersetSecurityManager

    flask_mod = types.ModuleType("flask")
    flask_g = types.SimpleNamespace(user=None)
    flask_mod.g = flask_g

    fab = types.ModuleType("flask_appbuilder")
    fab_models = types.ModuleType("flask_appbuilder.models")
    fab_sqla = types.ModuleType("flask_appbuilder.models.sqla")
    fab_filters = types.ModuleType("flask_appbuilder.models.sqla.filters")

    class FilterContains:
        """Stub for flask_appbuilder FilterContains."""

        def __init__(self, col, datamodel):
            """Accept and discard constructor arguments.

            Args:
                col: Column name.
                datamodel: Data model instance.
            """

    fab_filters.FilterContains = FilterContains

    fab_security = types.ModuleType("flask_appbuilder.security")
    fab_security_sqla = types.ModuleType("flask_appbuilder.security.sqla")
    fab_apis = types.ModuleType("flask_appbuilder.security.sqla.apis")

    class PermissionViewMenuApi:
        """Stub for flask_appbuilder PermissionViewMenuApi."""

    fab_apis.PermissionViewMenuApi = PermissionViewMenuApi

    mods = {
        "superset": types.ModuleType("superset"),
        "superset.models": types.ModuleType("superset.models"),
        "superset.models.sql_lab": query_mod,
        "superset.security": security_mod,
        "flask": flask_mod,
        "flask_appbuilder": fab,
        "flask_appbuilder.models": fab_models,
        "flask_appbuilder.models.sqla": fab_sqla,
        "flask_appbuilder.models.sqla.filters": fab_filters,
        "flask_appbuilder.security": fab_security,
        "flask_appbuilder.security.sqla": fab_security_sqla,
        "flask_appbuilder.security.sqla.apis": fab_apis,
    }
    return Query, flask_g, mods


def _load_manager_class(stubs):
    """Load CustomSecurityManager by exec-ing the template file."""
    src_path = (
        pathlib.Path(__file__).parent.parent.parent
        / "templates"
        / "custom_sso_security_manager.py"
    )
    ns: dict = {}
    code = compile(src_path.read_text(), src_path, "exec")
    with mock.patch.dict(sys.modules, stubs):
        exec(code, ns)  # nosec B102  # pylint: disable=exec-used
    return ns["CustomSecurityManager"]


_Query, _flask_g, _stubs = _setup_stubs()
_CustomSM = _load_manager_class(_stubs)
_DEFAULT_DATABASE = object()


def _make_manager(is_admin=False):
    """Create a manager instance with the given admin status."""
    mgr = _CustomSM.__new__(_CustomSM)
    mgr._is_admin = is_admin
    mgr._last_call = None
    mgr._can_access_database = False
    mgr._deny_datasource = False
    mgr._deny_query = False
    return mgr


def _make_query(
    user_id=None,
    sql="SELECT * FROM allowed_table",
    database=_DEFAULT_DATABASE,
):
    """Create a Query stub with the given owner user_id."""
    q = _Query()
    q.user_id = user_id
    q.sql = sql
    q.database = object() if database is _DEFAULT_DATABASE else database
    return q


class _StubbedTestCase(unittest.TestCase):
    """Provide scoped import stubs for each test."""

    def setUp(self):
        """Install import stubs and reset the current user."""
        self._module_patch = mock.patch.dict(sys.modules, _stubs)
        self._module_patch.start()
        self.addCleanup(self._module_patch.stop)
        self._environment_patch = mock.patch.dict(
            os.environ, {"ENABLE_RAISE_FOR_ACCESS_PATCH": "true"}
        )
        self._environment_patch.start()
        self.addCleanup(self._environment_patch.stop)
        _flask_g.user = None


class TestRaiseForAccessPatchDisabled(_StubbedTestCase):
    """Verify the disabled patch preserves upstream authorization."""

    def test_owner_query_uses_datasource_authorization_when_unset(self):
        """An owner Query remains a datasource without the environment value."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        q = _make_query(user_id=42)
        with mock.patch.dict(os.environ, {}, clear=True):
            mgr.raise_for_access(datasource=q)
        _, kw = mgr._last_call
        self.assertIs(kw["datasource"], q)
        self.assertNotIn("query", kw)

    def test_owner_query_uses_datasource_authorization_by_default(self):
        """An owner Query remains a datasource when the patch is disabled."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        q = _make_query(user_id=42)
        with mock.patch.dict(
            os.environ, {"ENABLE_RAISE_FOR_ACCESS_PATCH": "false"}
        ):
            mgr.raise_for_access(datasource=q)
        _, kw = mgr._last_call
        self.assertIs(kw["datasource"], q)
        self.assertNotIn("query", kw)


class TestRaiseForAccessQueryRerouting(_StubbedTestCase):
    """Verify ownership and admin checks for Query datasource rerouting."""

    def test_owner_query_rerouted(self):
        """Owner's Query is rerouted through the query= branch."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        q = _make_query(user_id=42)
        mgr.raise_for_access(datasource=q)
        _, kw = mgr._last_call
        self.assertNotIn("datasource", kw)
        self.assertIs(kw["query"], q)

    def test_non_owner_query_not_rerouted(self):
        """Another user's Query is not rerouted."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        q = _make_query(user_id=99)
        mgr.raise_for_access(datasource=q)
        _, kw = mgr._last_call
        self.assertIs(kw["datasource"], q)
        self.assertNotIn("query", kw)

    def test_admin_accesses_any_query(self):
        """Admin can reroute another user's Query."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager(is_admin=True)
        q = _make_query(user_id=99)
        mgr.raise_for_access(datasource=q)
        _, kw = mgr._last_call
        self.assertNotIn("datasource", kw)
        self.assertIs(kw["query"], q)

    def test_null_query_owner_denied(self):
        """Query with user_id=None is not rerouted for ordinary user."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        q = _make_query(user_id=None)
        mgr.raise_for_access(datasource=q)
        _, kw = mgr._last_call
        self.assertIs(kw["datasource"], q)
        self.assertNotIn("query", kw)

    def test_null_query_owner_admin_allowed(self):
        """Query with user_id=None is rerouted for admin."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager(is_admin=True)
        q = _make_query(user_id=None)
        mgr.raise_for_access(datasource=q)
        _, kw = mgr._last_call
        self.assertNotIn("datasource", kw)
        self.assertIs(kw["query"], q)

    def test_no_current_user_not_rerouted(self):
        """No current user (g.user is None) prevents rerouting."""
        _flask_g.user = None
        mgr = _make_manager()
        q = _make_query(user_id=42)
        mgr.raise_for_access(datasource=q)
        _, kw = mgr._last_call
        self.assertIs(kw["datasource"], q)
        self.assertNotIn("query", kw)

    def test_templated_query_with_database_access_rerouted(self):
        """Owner's templated Query reroutes with database access."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        mgr._can_access_database = True
        q = _make_query(user_id=42, sql="SELECT * FROM {{ target }}")
        mgr.raise_for_access(datasource=q)
        _, kw = mgr._last_call
        self.assertIs(kw["query"], q)
        self.assertNotIn("datasource", kw)

    def test_templated_query_without_database_access_not_rerouted(self):
        """Owner's templated Query fails closed without database access."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        mgr._deny_datasource = True
        q = _make_query(user_id=42, sql="SELECT * FROM {% raw %}x{% endraw %}")
        with self.assertRaises(_AccessDenied):
            mgr.raise_for_access(datasource=q)
        _, kw = mgr._last_call
        self.assertIs(kw["datasource"], q)
        self.assertNotIn("query", kw)

    def test_owner_query_without_sql_not_rerouted(self):
        """An incomplete Query does not gain the owner reroute."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        mgr._deny_datasource = True
        q = _make_query(user_id=42, sql=None)
        with self.assertRaises(_AccessDenied):
            mgr.raise_for_access(datasource=q)

    def test_owner_query_without_database_not_rerouted(self):
        """A Query without a database does not gain the owner reroute."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        mgr._deny_datasource = True
        q = _make_query(user_id=42, database=None)
        with self.assertRaises(_AccessDenied):
            mgr.raise_for_access(datasource=q)

    def test_owner_query_with_blank_sql_not_rerouted(self):
        """A Query with blank SQL does not gain the owner reroute."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        mgr._deny_datasource = True
        q = _make_query(user_id=42, sql="  ")
        with self.assertRaises(_AccessDenied):
            mgr.raise_for_access(datasource=q)


class TestRaiseForAccessOutcomes(_StubbedTestCase):
    """Verify authorization outcomes across the delegation boundary."""

    def test_owner_allowed_by_query_authorization(self):
        """An owner succeeds when query authorization permits access."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        mgr._deny_datasource = True
        mgr.raise_for_access(datasource=_make_query(user_id=42))

    def test_owner_denied_by_query_authorization(self):
        """An owner remains denied when query authorization rejects access."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        mgr._deny_query = True
        with self.assertRaises(_AccessDenied):
            mgr.raise_for_access(datasource=_make_query(user_id=42))

    def test_non_owner_denied_by_datasource_authorization(self):
        """A non-owner cannot reach the query authorization path."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        mgr._deny_datasource = True
        with self.assertRaises(_AccessDenied):
            mgr.raise_for_access(datasource=_make_query(user_id=99))

    def test_admin_allowed_by_query_authorization(self):
        """An admin can reroute another user's Query."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager(is_admin=True)
        mgr._deny_datasource = True
        mgr.raise_for_access(datasource=_make_query(user_id=99))


class TestStubIsolation(unittest.TestCase):
    """Verify template loading restores existing imported modules."""

    def test_load_manager_restores_existing_modules(self):
        """Loading the template does not replace process-wide modules."""
        module_names = ("flask", "superset", "flask_appbuilder")
        existing = {name: types.ModuleType(name) for name in module_names}
        with mock.patch.dict(sys.modules, existing):
            _load_manager_class(_stubs)
            for name, module in existing.items():
                self.assertIs(sys.modules[name], module)


class TestRaiseForAccessPassthrough(_StubbedTestCase):
    """Verify non-Query calls pass through unchanged."""

    def test_non_query_datasource_unchanged(self):
        """A non-Query datasource is forwarded as-is."""
        mgr = _make_manager()
        ds = object()
        mgr.raise_for_access(datasource=ds)
        _, kw = mgr._last_call
        self.assertIs(kw["datasource"], ds)
        self.assertNotIn("query", kw)

    def test_no_datasource_kwarg_unchanged(self):
        """Calls without datasource= are forwarded unchanged."""
        mgr = _make_manager()
        mgr.raise_for_access(query="something")
        _, kw = mgr._last_call
        self.assertEqual(kw["query"], "something")
        self.assertNotIn("datasource", kw)

    def test_both_datasource_and_query_unchanged(self):
        """Conflicting datasource and query kwargs pass through."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        q = _make_query(user_id=42)
        mgr.raise_for_access(datasource=q, query="other")
        _, kw = mgr._last_call
        self.assertIs(kw["datasource"], q)
        self.assertEqual(kw["query"], "other")


class TestRaiseForAccessArgPreservation(_StubbedTestCase):
    """Verify positional and keyword argument forwarding."""

    def test_positional_args_on_reroute(self):
        """Positional args are preserved when a Query is rerouted."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        q = _make_query(user_id=42)
        mgr.raise_for_access("a", "b", datasource=q)
        args, kw = mgr._last_call
        self.assertEqual(args, ("a", "b"))
        self.assertIs(kw["query"], q)
        self.assertNotIn("datasource", kw)

    def test_extra_kwargs_on_reroute(self):
        """Unrelated kwargs are preserved when a Query is rerouted."""
        _flask_g.user = types.SimpleNamespace(id=42)
        mgr = _make_manager()
        q = _make_query(user_id=42)
        mgr.raise_for_access(datasource=q, force_dataset_match=True)
        _, kw = mgr._last_call
        self.assertIs(kw["query"], q)
        self.assertNotIn("datasource", kw)
        self.assertTrue(kw["force_dataset_match"])

    def test_positional_args_without_datasource(self):
        """Positional args are forwarded when no datasource kwarg."""
        mgr = _make_manager()
        mgr.raise_for_access("a", "b")
        args, kw = mgr._last_call
        self.assertEqual(args, ("a", "b"))
        self.assertNotIn("datasource", kw)
