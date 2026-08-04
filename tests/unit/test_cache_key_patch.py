# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the QueryObject cache-key SQL rendering patch.

The patch lives in templates/superset_config.py and is loaded by every
Superset process at startup via PYTHONPATH. These tests extract the patch
functions and exercise them in isolation with no installed Superset package
or running Juju model required.

The patch renders adhoc SQL with sqlglot, using the datasource's own dialect,
so that the worker's raw SQL and the UI's re-rendered SQL hash identically.
"""

import pathlib
import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Bootstrap: extract patch functions without importing Superset
# ---------------------------------------------------------------------------


def _load_patch_ns():
    """Exec only the fix block from superset_config.py with a stubbed QO.

    Returns:
        Dict of names defined by the fix block, including _qo_trim,
        _qo_dialect, _qo_render_sql, _qo_render_expr, _qo_render_orderby
        and _qo_patched_cache_key.

    Raises:
        FileNotFoundError: If superset_config.py cannot be located.
    """
    config_path = (
        pathlib.Path(__file__).parent.parent.parent
        / "templates"
        / "superset_config.py"
    )
    if not config_path.exists():
        raise FileNotFoundError(
            f"superset_config.py not found at {config_path}"
        )

    src = config_path.read_text()

    start_marker = (
        "from superset.common.query_object import QueryObject as _QO"
    )
    end_marker = "# End fix: QueryObject cache-key SQL rendering"
    start = src.index(start_marker)
    end = src.index(end_marker)
    block = src[start:end]

    class _StubQO:
        """Stand-in QueryObject used to load the patch without Superset."""

        def cache_key(self, **extra):
            """Return a fixed stub hash.

            Args:
                extra: Ignored extra keyword arguments.

            Returns:
                Fixed stub hash string.
            """
            del extra
            return "stub_hash"

    stub_module = types.ModuleType("superset.common.query_object")
    stub_module.QueryObject = _StubQO  # type: ignore[attr-defined]

    sys.modules.setdefault("superset", types.ModuleType("superset"))
    sys.modules.setdefault(
        "superset.common", types.ModuleType("superset.common")
    )
    sys.modules["superset.common.query_object"] = stub_module

    ns: dict = {}
    exec(block, ns)  # nosec B102  # pylint: disable=exec-used
    return ns


_P = _load_patch_ns()

_trim = _P["_qo_trim"]
_dialect = _P["_qo_dialect"]
_render = _P["_qo_render_sql"]
_render_expr = _P["_qo_render_expr"]
_render_ob = _P["_qo_render_orderby"]
_patched_ck = _P["_qo_patched_cache_key"]

TRINO = "trino"


def _mk_metric(sql, label="m"):
    """Return a minimal adhoc-SQL metric dict.

    Args:
        sql: The sqlExpression to embed.
        label: The metric label.

    Returns:
        An adhoc-SQL metric dict.
    """
    return {"expressionType": "SQL", "sqlExpression": sql, "label": label}


def _orig_returning_h(*args, **kwargs):
    """Stand-in for _qo_orig_cache_key that returns a fixed hash.

    Args:
        args: Ignored positional arguments.
        kwargs: Ignored keyword arguments.

    Returns:
        Fixed hash string.
    """
    del args, kwargs
    return "H"


def _mk_query_object(backend=None, engine=None, db_id=1):
    """Build a stub QueryObject exposing just what _qo_dialect reads.

    Args:
        backend: SQLAlchemy backend name, or None to omit url_object.
        engine: db_engine_spec.engine value, or None to omit the spec.
        db_id: Database id, used as the dialect memoisation key.

    Returns:
        An object with a .datasource.database chain.
    """

    class Url:
        """Stub SQLAlchemy URL."""

        def get_backend_name(self):
            """Return the backend name.

            Returns:
                The configured backend name.
            """
            return backend

    class Spec:
        """Stub db_engine_spec.

        Attrs:
            engine: The Superset engine name.
        """

        def __init__(self):
            """Record the engine name."""
            self.engine = engine

    class Database:
        """Stub Superset Database model."""

        def __init__(self):
            """Attach id and, when configured, url_object/db_engine_spec."""
            self.id = db_id
            if backend is not None:
                self.url_object = Url()
            if engine is not None:
                self.db_engine_spec = Spec()

    class Datasource:
        """Stub Superset datasource."""

        def __init__(self):
            """Attach the stub database."""
            self.database = Database()

    class QueryObject:
        """Stub QueryObject carrying only a datasource."""

        def __init__(self):
            """Attach the stub datasource."""
            self.datasource = Datasource()

    return QueryObject()


# ---------------------------------------------------------------------------
# _qo_trim
# ---------------------------------------------------------------------------


class TestTrim(unittest.TestCase):
    """Tests for the fallback used where sqlglot cannot help."""

    def test_crlf_normalised(self):
        """CRLF and bare CR become LF."""
        self.assertEqual(_trim("a\r\nb"), "a\nb")
        self.assertEqual(_trim("a\rb"), "a\nb")

    def test_leading_and_trailing_stripped(self):
        """Whitespace at both ends is removed."""
        self.assertEqual(_trim("   SUM(x)   "), "SUM(x)")
        self.assertEqual(_trim("SUM(x)\n\n    "), "SUM(x)")

    def test_orderby_item_list_pair_converges(self):
        """Forms differing only by a trailing space converge."""
        worker = "br_year DESC, br_num "
        ui = "br_year DESC, br_num"
        self.assertEqual(_trim(worker), _trim(ui))

    def test_internal_text_untouched(self):
        """Nothing inside the expression is rewritten."""
        sql = "CASE WHEN s = 'Closed - Won' THEN a + b   END"
        self.assertEqual(_trim(sql), sql)

    def test_literal_content_never_altered(self):
        """Whitespace inside a string literal survives."""
        self.assertEqual(_trim("MAX('A   B')"), "MAX('A   B')")

    def test_empty_and_whitespace_only(self):
        """Empty and whitespace-only inputs collapse to an empty string."""
        self.assertEqual(_trim(""), "")
        self.assertEqual(_trim("   \n\t "), "")

    def test_idempotent(self):
        """Trimming twice equals trimming once."""
        once = _trim("  SUM(x)\r\n  ")
        self.assertEqual(_trim(once), once)


# ---------------------------------------------------------------------------
# _qo_dialect
# ---------------------------------------------------------------------------


class TestDialect(unittest.TestCase):
    """Tests for dialect resolution."""

    def setUp(self):
        """Clear the per-database dialect memo before each test."""
        _P["_QO_DIALECTS"].clear()

    def tearDown(self):
        """Remove any injected Superset mapping module."""
        sys.modules.pop("superset.sql.parse", None)
        sys.modules.pop("superset.sql", None)
        _P["_QO_DIALECTS"].clear()

    def _inject_superset_mapping(self, mapping):
        """Install a stub superset.sql.parse exposing SQLGLOT_DIALECTS.

        Args:
            mapping: The dict to expose as SQLGLOT_DIALECTS.
        """
        pkg = types.ModuleType("superset.sql")
        mod = types.ModuleType("superset.sql.parse")
        mod.SQLGLOT_DIALECTS = mapping  # type: ignore[attr-defined]
        sys.modules["superset.sql"] = pkg
        sys.modules["superset.sql.parse"] = mod

    def test_superset_mapping_preferred(self):
        """Superset's own engine mapping is consulted first."""
        self._inject_superset_mapping({"trino": "trino"})
        obj = _mk_query_object(backend="postgresql", engine="trino", db_id=10)
        self.assertEqual(_dialect(obj), "trino")

    def test_backend_name_used_when_mapping_absent(self):
        """Without the mapping, the SQLAlchemy backend name is used."""
        obj = _mk_query_object(backend="trino", db_id=11)
        self.assertEqual(_dialect(obj), "trino")

    def test_backend_alias_applied(self):
        """Backend names sqlglot spells differently are translated."""
        cases = {"postgresql": "postgres", "mssql": "tsql"}
        for db_id, (backend, want) in enumerate(cases.items(), start=20):
            with self.subTest(backend=backend):
                _P["_QO_DIALECTS"].clear()
                obj = _mk_query_object(backend=backend, db_id=db_id)
                self.assertEqual(_dialect(obj), want)

    def test_unknown_backend_is_unresolved(self):
        """A backend sqlglot does not know resolves to None, never a guess."""
        obj = _mk_query_object(backend="not-a-real-engine", db_id=30)
        self.assertIsNone(_dialect(obj))

    def test_unknown_mapping_value_falls_through_to_backend(self):
        """A mapping value sqlglot rejects does not block the fallback."""
        self._inject_superset_mapping({"weird": "not-a-real-dialect"})
        obj = _mk_query_object(backend="trino", engine="weird", db_id=31)
        self.assertEqual(_dialect(obj), "trino")

    def test_no_datasource_returns_none(self):
        """A QueryObject without a datasource resolves to None."""

        class Bare:
            """QueryObject stub with no datasource attribute."""

        self.assertIsNone(_dialect(Bare()))

    def test_exploding_datasource_returns_none(self):
        """An attribute that raises must not escape as an exception."""

        class Exploding:
            """QueryObject stub whose datasource access raises.

            Attrs:
                datasource: Property that raises on access.
            """

            @property
            def datasource(self):
                """Raise on access.

                Raises:
                    RuntimeError: Always.
                """
                raise RuntimeError("boom")

        self.assertIsNone(_dialect(Exploding()))

    def test_result_is_memoised_per_database(self):
        """The resolved dialect is cached against the database id."""
        obj = _mk_query_object(backend="trino", db_id=40)
        _dialect(obj)
        self.assertIn(40, _P["_QO_DIALECTS"])
        self.assertEqual(_P["_QO_DIALECTS"][40], "trino")


# ---------------------------------------------------------------------------
# _qo_render_sql
# ---------------------------------------------------------------------------


class TestRenderSql(unittest.TestCase):
    """Tests for the sqlglot render used to build the hash input."""

    def setUp(self):
        """Clear the render memo before each test."""
        _P["_QO_RENDER_CACHE"].clear()

    def test_no_dialect_falls_back_to_trim(self):
        """Without a dialect the SQL is only trimmed, never rendered."""
        self.assertEqual(_render("  sum(a)+sum(b)  ", None), "sum(a)+sum(b)")

    def test_unparseable_falls_back_to_trim(self):
        """An expression sqlglot cannot parse is only trimmed."""
        self.assertEqual(
            _render("br_year DESC, br_num ", TRINO), "br_year DESC, br_num"
        )

    def test_render_is_idempotent(self):
        """Rendering an already-rendered string changes nothing."""
        once = _render("sum(a)+sum(b)", TRINO)
        self.assertEqual(_render(once, TRINO), once)

    def test_result_is_memoised(self):
        """A rendered expression is cached against (sql, dialect)."""
        _render("SUM(a)", TRINO)
        self.assertIn(("SUM(a)", TRINO), _P["_QO_RENDER_CACHE"])

    def test_cache_is_bounded(self):
        """The render cache clears rather than growing without limit."""
        limit = _P["_QO_RENDER_CACHE_MAX"]
        for i in range(limit + 5):
            _render(f"SUM(col_{i})", TRINO)
        self.assertLessEqual(len(_P["_QO_RENDER_CACHE"]), limit)

    def test_empty_string(self):
        """An empty expression is returned as an empty string."""
        self.assertEqual(_render("", TRINO), "")

    def test_never_raises_on_junk(self):
        """Malformed input returns a string instead of raising."""
        for junk in ("SELECT ((( unbalanced", "sum(a", "   "):
            with self.subTest(junk=junk):
                self.assertIsInstance(_render(junk, TRINO), str)


# ---------------------------------------------------------------------------
# _qo_render_expr (adhoc metric dict wrapper)
# ---------------------------------------------------------------------------


class TestRenderExpr(unittest.TestCase):
    """Tests for the adhoc-SQL dict wrapper."""

    def test_non_dict_passthrough(self):
        """Non-dict values are returned unchanged."""
        for val in ("raw_string", 42, None, ["list"]):
            with self.subTest(val=val):
                self.assertIs(_render_expr(val, TRINO), val)

    def test_dict_without_expression_type_passthrough(self):
        """Dict without expressionType is returned unchanged."""
        d = {"label": "m", "sqlExpression": "SUM(x + y)"}
        self.assertIs(_render_expr(d, TRINO), d)

    def test_dict_with_non_sql_expression_type_passthrough(self):
        """Dict with expressionType != 'SQL' is returned unchanged."""
        d = {"expressionType": "SIMPLE", "column": "revenue"}
        self.assertIs(_render_expr(d, TRINO), d)

    def test_adhoc_sql_dict_rendered(self):
        """Dict with expressionType='SQL' gets its sqlExpression rendered."""
        d = _mk_metric("sum(x)+sum(y)")
        self.assertEqual(
            _render_expr(d, TRINO)["sqlExpression"], "SUM(x) + SUM(y)"
        )

    def test_original_dict_not_mutated(self):
        """The original dict object is not modified in place."""
        original_sql = "sum(x)+sum(y)"
        d = _mk_metric(original_sql)
        _render_expr(d, TRINO)
        self.assertEqual(d["sqlExpression"], original_sql)

    def test_other_fields_preserved(self):
        """Non-sqlExpression fields in the dict are preserved unchanged."""
        d = _mk_metric("SUM( x )", label="my metric")
        d["optionName"] = "abc123"
        result = _render_expr(d, TRINO)
        self.assertEqual(result["label"], "my metric")
        self.assertEqual(result["optionName"], "abc123")

    def test_result_is_new_dict(self):
        """The returned dict is a new object, not the original."""
        d = _mk_metric("SUM( x )")
        self.assertIsNot(_render_expr(d, TRINO), d)


# ---------------------------------------------------------------------------
# _qo_render_orderby (orderby item wrapper)
# ---------------------------------------------------------------------------


class TestRenderOrderby(unittest.TestCase):
    """Tests for the orderby item wrapper."""

    def test_non_sequence_passthrough(self):
        """Non-list/non-tuple values are returned unchanged."""
        for val in ("string", 42, None):
            with self.subTest(val=val):
                self.assertIs(_render_ob(val, TRINO), val)

    def test_empty_sequence_passthrough(self):
        """Empty list and tuple are returned unchanged."""
        empty_list: list = []
        empty_tuple: tuple = ()
        self.assertIs(_render_ob(empty_list, TRINO), empty_list)
        self.assertIs(_render_ob(empty_tuple, TRINO), empty_tuple)

    def test_first_element_rendered(self):
        """Only the first element of the orderby item is rendered."""
        result = _render_ob([_mk_metric("sum(x)+sum(y)"), True], TRINO)
        self.assertEqual(result[0]["sqlExpression"], "SUM(x) + SUM(y)")
        self.assertEqual(result[1], True)

    def test_tuple_item_converted_to_list(self):
        """Tuple orderby item is converted to list and rendered."""
        result = _render_ob((_mk_metric("sum(x)"), False), TRINO)
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["sqlExpression"], "SUM(x)")

    def test_non_sql_first_element_passthrough(self):
        """Non-adhoc-SQL first element is passed through unchanged."""
        self.assertEqual(
            _render_ob(["plain_column", True], TRINO)[0], "plain_column"
        )

    def test_original_metric_dict_not_mutated(self):
        """The metric dict inside the orderby item is not modified."""
        original_sql = "sum(x)+sum(y)"
        metric = _mk_metric(original_sql)
        _render_ob([metric, True], TRINO)
        self.assertEqual(metric["sqlExpression"], original_sql)

    def test_extra_elements_preserved(self):
        """Elements beyond [metric, bool] are preserved."""
        result = _render_ob([_mk_metric("SUM(x)"), True, "extra"], TRINO)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[2], "extra")


# ---------------------------------------------------------------------------
# _qo_patched_cache_key (save/restore wrapper)
# ---------------------------------------------------------------------------


class TestPatchedCacheKey(unittest.TestCase):
    """Tests for the save/restore wrapper around QueryObject.cache_key.

    _qo_patched_cache_key calls _qo_orig_cache_key via a global lookup in
    the exec namespace (_P). setUp/tearDown save and restore that slot so
    each test can inject a stand-in that records what it was called with.
    """

    def setUp(self):
        """Save the original _qo_orig_cache_key before each test."""
        self._saved_orig = _P["_qo_orig_cache_key"]

    def tearDown(self):
        """Restore _qo_orig_cache_key after each test."""
        _P["_qo_orig_cache_key"] = self._saved_orig

    def _set_orig(self, fn):
        """Replace the orig function seen by the patched closure.

        Args:
            fn: The stand-in to install.
        """
        _P["_qo_orig_cache_key"] = fn

    def _make_obj(self, metrics=None, columns=None, orderby=None, slm=None):
        """Create a minimal stub that patched_cache_key can operate on.

        Args:
            metrics: Value for the metrics attribute.
            columns: Value for the columns attribute.
            orderby: Value for the orderby attribute.
            slm: Value for the series_limit_metric attribute.

        Returns:
            A QueryObject stub with a Trino datasource.
        """
        obj = _mk_query_object(backend="trino", db_id=99)
        obj.metrics = metrics if metrics is not None else []
        obj.columns = columns if columns is not None else []
        obj.orderby = orderby if orderby is not None else []
        obj.series_limit_metric = slm
        return obj

    # ---- Restore behaviour --------------------------------------------- #

    def test_metrics_restored_after_call(self):
        """Original metrics list and dict objects are restored."""
        metric = _mk_metric("sum( x + y )")
        obj = self._make_obj(metrics=[metric])
        self._set_orig(_orig_returning_h)

        _patched_ck(obj)

        self.assertIs(obj.metrics[0], metric)
        self.assertEqual(obj.metrics[0]["sqlExpression"], "sum( x + y )")

    def test_columns_restored_after_call(self):
        """Original columns are restored after hashing."""
        col = _mk_metric("MAX( a )")
        obj = self._make_obj(columns=[col])
        self._set_orig(_orig_returning_h)
        _patched_ck(obj)
        self.assertIs(obj.columns[0], col)

    def test_orderby_restored_after_call(self):
        """Original orderby is restored after hashing."""
        ob = [_mk_metric("SUM( x )"), True]
        obj = self._make_obj(orderby=[ob])
        self._set_orig(_orig_returning_h)
        _patched_ck(obj)
        self.assertIs(obj.orderby[0], ob)

    def test_series_limit_metric_restored(self):
        """Original series_limit_metric is restored after hashing."""
        slm = _mk_metric("MAX( z )")
        obj = self._make_obj(slm=slm)
        self._set_orig(_orig_returning_h)
        _patched_ck(obj)
        self.assertIs(obj.series_limit_metric, slm)

    def test_none_series_limit_metric_stays_none(self):
        """None series_limit_metric is left alone and stays None."""
        obj = self._make_obj(slm=None)
        self._set_orig(_orig_returning_h)
        _patched_ck(obj)
        self.assertIsNone(obj.series_limit_metric)

    def test_attributes_restored_even_when_orig_raises(self):
        """Attributes are restored via finally even if orig raises."""
        metric = _mk_metric("sum( x + y )")
        obj = self._make_obj(metrics=[metric])

        def exploding(*args, **kwargs):
            """Simulate a failing cache_key call.

            Args:
                args: Ignored positional arguments.
                kwargs: Ignored keyword arguments.

            Raises:
                RuntimeError: Always, to simulate failure.
            """
            del args, kwargs
            raise RuntimeError("cache key failed!")

        self._set_orig(exploding)

        with self.assertRaises(RuntimeError):
            _patched_ck(obj)

        self.assertIs(obj.metrics[0], metric)
        self.assertEqual(obj.metrics[0]["sqlExpression"], "sum( x + y )")

    # ---- Rendered SQL seen during the call ------------------------------ #

    def test_rendered_sql_seen_during_call(self):
        """The orig receives rendered SQL, not the original strings."""
        seen: dict = {}

        def recording_orig(s, **e):
            """Capture self.metrics and self.orderby at call time.

            Args:
                s: QueryObject stub to inspect.
                e: Ignored extra keyword arguments.

            Returns:
                Fixed stub hash string.
            """
            del e
            seen["metrics"] = [m.get("sqlExpression") for m in s.metrics]
            seen["orderby"] = [
                ob[0].get("sqlExpression") if isinstance(ob, list) else ob
                for ob in s.orderby
            ]
            return "H"

        self._set_orig(recording_orig)
        metric = _mk_metric("sum(a)+sum(b)")
        obj = self._make_obj(metrics=[metric], orderby=[[metric, True]])

        _patched_ck(obj)

        self.assertEqual(seen["metrics"], ["SUM(a) + SUM(b)"])
        self.assertEqual(seen["orderby"], ["SUM(a) + SUM(b)"])

    def test_original_dict_not_mutated_by_call(self):
        """The original metric dict is not modified in place."""
        original_sql = "sum( a + b )"
        metric = _mk_metric(original_sql)
        obj = self._make_obj(metrics=[metric])
        self._set_orig(_orig_returning_h)

        _patched_ck(obj)

        self.assertEqual(metric["sqlExpression"], original_sql)

    # ---- Empty / None inputs ------------------------------------------- #

    def test_empty_lists(self):
        """Empty attribute lists are handled without errors."""
        obj = self._make_obj()
        self._set_orig(_orig_returning_h)
        self.assertEqual(_patched_ck(obj), "H")
        self.assertEqual(obj.metrics, [])

    def test_none_attributes_treated_as_empty(self):
        """None attributes are treated as empty, then restored."""
        obj = self._make_obj()
        obj.metrics = None
        obj.columns = None
        obj.orderby = None
        self._set_orig(_orig_returning_h)

        _patched_ck(obj)

        self.assertIsNone(obj.metrics)

    def test_non_sql_metrics_passthrough(self):
        """Simple metric name strings are passed through unchanged."""
        seen: dict = {}

        def recording_orig(s, **e):
            """Capture self.metrics at call time.

            Args:
                s: QueryObject stub to inspect.
                e: Ignored extra keyword arguments.

            Returns:
                Fixed stub hash string.
            """
            del e
            seen["metrics"] = list(s.metrics)
            return "H"

        self._set_orig(recording_orig)
        obj = self._make_obj(metrics=["count", "sum__amount"])

        _patched_ck(obj)

        self.assertEqual(seen["metrics"], ["count", "sum__amount"])
        self.assertEqual(obj.metrics, ["count", "sum__amount"])

    # ---- Return value and kwargs --------------------------------------- #

    def test_return_value_forwarded(self):
        """The return value of the orig function is forwarded unchanged."""

        def returning_specific(*args, **kwargs):
            """Return a specific hash string.

            Args:
                args: Ignored positional arguments.
                kwargs: Ignored keyword arguments.

            Returns:
                Specific hash string.
            """
            del args, kwargs
            return "abc123"

        self._set_orig(returning_specific)
        self.assertEqual(_patched_ck(self._make_obj()), "abc123")

    def test_extra_kwargs_forwarded(self):
        """Keyword arguments such as datasource and rls reach orig."""
        received: dict = {}

        def capturing_orig(s, **e):
            """Capture extra kwargs at call time.

            Args:
                s: Ignored QueryObject stub.
                e: Extra keyword arguments to capture.

            Returns:
                Fixed stub hash string.
            """
            del s
            received.update(e)
            return "H"

        self._set_orig(capturing_orig)
        _patched_ck(
            self._make_obj(),
            datasource="ds:1",
            rls="[]",
            changed_on="2026-01-01",
        )
        self.assertEqual(received["datasource"], "ds:1")
        self.assertEqual(received["rls"], "[]")


# ---------------------------------------------------------------------------
# End-to-end convergence of the worker and UI SQL forms
# ---------------------------------------------------------------------------


class TestConvergence(unittest.TestCase):
    """The worker and UI SQL forms must hash identically.

    Each pair is a (worker, ui) sqlExpression: the raw text the Celery
    worker receives, and what Superset's QueryContext-cache rebuild
    produces from it for the UI. Both must reduce to one hash input.
    """

    def _assert_converges(self, worker_sql, ui_sql):
        """Assert both sides render to the same hash input.

        Args:
            worker_sql: The raw SQL the Celery worker received.
            ui_sql: The SQL the UI held after its rebuild.
        """
        self.assertEqual(_render(worker_sql, TRINO), _render(ui_sql, TRINO))

    def test_case_and_operator_spacing(self):
        """Lowercase function names and operator spacing converge."""
        self._assert_converges(
            "sum(opp_new_pipeline)+sum(opp_expansion_pipeline)\r\n",
            "SUM(opp_new_pipeline) + SUM(opp_expansion_pipeline)",
        )

    def test_multiline_case_collapsed(self):
        """Internal newlines and indentation converge."""
        self._assert_converges(
            "SUM(CASE \n WHEN \"Stage\" = 'Closed Won' \n THEN 1\n END)",
            "SUM(CASE WHEN \"Stage\" = 'Closed Won' THEN 1 END)",
        )

    def test_structural_rewrite_is_not_null(self):
        """IS NOT NULL rewritten as NOT ... IS NULL converges."""
        self._assert_converges(
            "COUNT(DISTINCT CASE\r\n WHEN lead_product IS NOT NULL\r\n"
            " THEN lead_id\r\nEND)",
            "COUNT(DISTINCT CASE WHEN NOT lead_product IS NULL "
            "THEN lead_id END)",
        )

    def test_string_literal_recased(self):
        """A date-part literal recased by the render converges."""
        self._assert_converges(
            "AVG(DATE_DIFF('day', submitted_date, approved_date))",
            "AVG(DATE_DIFF('DAY', submitted_date, approved_date))",
        )

    def test_unparseable_orderby_trailing_space(self):
        """An item list sqlglot cannot parse converges via the trim."""
        self._assert_converges("br_year DESC, br_num ", "br_year DESC, br_num")

    def test_quoted_identifier_operator_spacing(self):
        """Spacing around + between quoted identifiers converges."""
        self._assert_converges(
            'SUM("Y1 Renewal"+"Y1 Not Renewal")',
            'SUM("Y1 Renewal" + "Y1 Not Renewal")',
        )


# ---------------------------------------------------------------------------
# Collision safety
# ---------------------------------------------------------------------------


class TestNoCollision(unittest.TestCase):
    """Distinct queries must not be rendered into the same hash input."""

    def test_different_functions(self):
        """SUM and COUNT do not collide."""
        self.assertNotEqual(
            _render("SUM(amount)", TRINO), _render("COUNT(amount)", TRINO)
        )

    def test_different_string_literals(self):
        """Two queries differing only in a literal do not collide."""
        self.assertNotEqual(
            _render("CASE WHEN s = 'Closed - Won' THEN 1 END", TRINO),
            _render("CASE WHEN s = 'ClosedWon' THEN 1 END", TRINO),
        )

    def test_different_columns(self):
        """Different column references do not collide."""
        self.assertNotEqual(
            _render("SUM(revenue)", TRINO), _render("SUM(cost)", TRINO)
        )

    def test_literal_case_is_significant(self):
        """A literal that is not a date part keeps its case."""
        self.assertNotEqual(
            _render("MAX('abc')", TRINO), _render("MAX('ABC')", TRINO)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
