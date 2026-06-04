# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the QueryObject cache-key SQL normaliser patch.

The patch lives in templates/superset_config.py and is loaded by every
Superset process at startup via PYTHONPATH. These tests extract the patch
functions and exercise them in isolation with no installed Superset package
or running Juju model required.
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

    Returns a dict of names defined by the fix block:
    _qo_norm_sql_str, _qo_norm, _qo_norm_orderby, _qo_patched_cache_key.
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
    end_marker = "# End fix: QueryObject cache-key SQL normalisation"
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

_norm = _P["_qo_norm_sql_str"]
_norm_expr = _P["_qo_norm"]
_norm_ob = _P["_qo_norm_orderby"]
_patched_ck = _P["_qo_patched_cache_key"]


def _mk_metric(sql, label="m"):
    """Return a minimal adhoc-SQL metric dict."""
    return {"expressionType": "SQL", "sqlExpression": sql, "label": label}


def _orig_returning_h(*args, **kwargs):
    """Stand-in for _qo_orig_cache_key that returns a fixed hash."""
    del args, kwargs
    return "H"


# ---------------------------------------------------------------------------
# _qo_norm_sql_str
# ---------------------------------------------------------------------------


class TestNormSqlStr(  # pylint: disable=too-many-public-methods
    unittest.TestCase
):
    """Tests for the core SQL normaliser function."""

    # ---- Production patterns ------------------------------------------- #

    def test_p1_multiline_case_converge(self):
        """Worker (multiline) and UI (single-line) CASE produce same output."""
        worker = (
            "SUM(CASE \n        WHEN \"Stage\" = 'Closed Won' \n"
            "        AND amount > 0\n    END)\n\n    "
        )
        ui = "SUM(CASE WHEN \"Stage\" = 'Closed Won' AND amount>0 END)"
        self.assertEqual(_norm(worker), _norm(ui))

    def test_p1_trailing_whitespace_stripped(self):
        """Trailing newlines and spaces are removed."""
        self.assertEqual(_norm("SUM(x)\n\n    "), "SUM(x)")

    def test_p1_internal_newlines_collapsed(self):
        """Internal newlines and indentation are collapsed to a single space."""
        worker = "SUM(CASE\n        WHEN x>0\n        THEN 1\n    END)"
        ui = "SUM(CASE WHEN x>0 THEN 1 END)"
        self.assertEqual(_norm(worker), _norm(ui))

    def test_p2_operator_spacing_converge(self):
        """Worker (no spaces) and UI (with spaces) around + produce same output."""
        no_spaces = 'SUM("Y1 Renewal"+"Y1 Not Renewal")'
        with_spaces = 'SUM("Y1 Renewal" + "Y1 Not Renewal")'
        self.assertEqual(_norm(no_spaces), _norm(with_spaces))

    def test_p2_operator_spacing_all_variants(self):
        """All whitespace variants around an operator produce the same form."""
        forms = ["a+b", "a +b", "a+ b", "a + b", "a   +   b"]
        results = {_norm(f) for f in forms}
        self.assertEqual(
            len(results),
            1,
            f"Not all collapsed to one form: {results}",
        )

    # ---- String literal safety ----------------------------------------- #

    def test_literal_operator_chars_preserved(self):
        """Operator-like chars inside single-quoted literals are untouched."""
        sql = "CASE WHEN stage = 'Closed - Won' THEN 1 END"
        self.assertIn("'Closed - Won'", _norm(sql))

    def test_literal_multiple_spaces_preserved(self):
        """Multiple consecutive spaces inside a literal are left as-is."""
        sql = "MAX('A   B   C')"
        self.assertIn("'A   B   C'", _norm(sql))

    def test_literal_arithmetic_plus_preserved(self):
        """Plus sign inside a string literal is not treated as an operator."""
        sql = "CASE WHEN label = 'revenue + discount' THEN 1 END"
        self.assertIn("'revenue + discount'", _norm(sql))

    def test_literal_slash_preserved(self):
        """Forward slash inside a literal is not treated as division."""
        sql = "CASE WHEN type = 'A/B test' THEN 1 END"
        self.assertIn("'A/B test'", _norm(sql))

    def test_literal_star_preserved(self):
        """Asterisk inside a literal is not treated as multiplication."""
        sql = "CASE WHEN name = '5 * 5 = 25' THEN 1 END"
        self.assertIn("'5 * 5 = 25'", _norm(sql))

    def test_literal_doubled_quote_escape_preserved(self):
        """SQL escaped single quote (doubled) is copied verbatim."""
        sql = "MAX('it''s a - test')"
        self.assertIn("'it''s a - test'", _norm(sql))

    def test_literal_comment_markers_not_treated_as_comments(self):
        """Comment markers inside a literal are not parsed as comments."""
        sql1 = "CASE WHEN note = '-- not a comment' THEN 1 END"
        self.assertIn("'-- not a comment'", _norm(sql1))

        sql2 = "CASE WHEN note = '/* also not */' THEN 1 END"
        self.assertIn("'/* also not */'", _norm(sql2))

    def test_literal_empty_string_preserved(self):
        """Empty string literal is preserved."""
        self.assertIn("''", _norm("COALESCE(x, '')"))

    def test_double_quoted_identifier_content_preserved(self):
        """Operator-like chars inside double-quoted identifiers are untouched."""
        self.assertIn('"Gross - Net"', _norm('SUM("Gross - Net")'))

    def test_double_quoted_identifier_spaces_preserved(self):
        """Multiple spaces inside a double-quoted identifier are preserved."""
        self.assertIn('"Head  Count"', _norm('SUM("Head  Count")'))

    def test_double_quoted_identifier_doubled_escape_preserved(self):
        """Doubled double-quote escape inside identifier is copied verbatim."""
        self.assertIn('"col""name"', _norm('SELECT "col""name"'))

    def test_backtick_identifier_preserved(self):
        """Content inside backtick identifiers is copied verbatim."""
        self.assertIn("`col - name`", _norm("SUM(`col - name`)"))

    def test_mixed_literals_and_code(self):
        """Code operators are collapsed while literal content is preserved."""
        sql = "CASE WHEN s = 'Closed - Won' THEN revenue + discount ELSE 0 END"
        out = _norm(sql)
        self.assertIn("'Closed - Won'", out)
        self.assertIn("revenue+discount", out)
        self.assertNotIn("revenue + discount", out)

    # ---- Comment safety ------------------------------------------------- #

    def test_line_comment_content_preserved(self):
        """Operators inside a line comment are not modified."""
        sql = "a -- minus b\n + c"
        self.assertIn("-- minus b", _norm(sql))

    def test_block_comment_content_preserved(self):
        """Operators inside a block comment are not modified."""
        sql = "a /* x - y */ + b"
        self.assertIn("/* x - y */", _norm(sql))

    def test_block_comment_multiline_preserved(self):
        """Multi-line block comment content is copied verbatim."""
        sql = "x /* line1\nline2\nline3 */ + y"
        self.assertIn("/* line1\nline2\nline3 */", _norm(sql))

    def test_comment_at_end_preserved(self):
        """A trailing line comment is copied verbatim."""
        self.assertIn("-- total revenue", _norm("SUM(x) -- total revenue"))

    # ---- Operator / whitespace normalisation ---------------------------- #

    def test_all_arithmetic_operators_tightened(self):
        """Spaces around all arithmetic operators are removed."""
        self.assertEqual(_norm("a + b"), "a+b")
        self.assertEqual(_norm("a - b"), "a-b")
        self.assertEqual(_norm("a * b"), "a*b")
        self.assertEqual(_norm("a / b"), "a/b")
        self.assertEqual(_norm("a % b"), "a%b")

    def test_comparison_operators_tightened(self):
        """Spaces around comparison operators are removed."""
        for sql, want in [
            ("a > b", "a>b"),
            ("a < b", "a<b"),
            ("a >= b", "a>=b"),
            ("a <= b", "a<=b"),
            ("a = b", "a=b"),
            ("a != b", "a!=b"),
            ("a <> b", "a<>b"),
        ]:
            with self.subTest(sql=sql):
                self.assertEqual(_norm(sql), want)

    def test_pipe_operator_tightened(self):
        """Spaces around || string concat operator are removed."""
        self.assertEqual(_norm("a || b"), "a||b")

    def test_mixed_operator_chain(self):
        """Spaces around all operators in a chain are removed."""
        self.assertEqual(_norm("a + b * c - d / e"), "a+b*c-d/e")

    def test_paren_spaces_tightened(self):
        """Spaces inside function-call parentheses are removed."""
        self.assertEqual(_norm("SUM( x )"), "SUM(x)")
        self.assertEqual(_norm("COALESCE( a , b )"), "COALESCE(a,b)")

    def test_tab_whitespace_collapsed(self):
        """Tabs around operators and between keywords are handled."""
        self.assertEqual(_norm("a\t+\tb"), "a+b")
        self.assertEqual(_norm("CASE\tWHEN\tx"), "CASE WHEN x")

    def test_mixed_newline_formats(self):
        """CRLF and bare CR in SQL code are treated as whitespace."""
        crlf = "CASE\r\nWHEN x > 0\r\nTHEN 1 END"
        lf = "CASE\nWHEN x > 0\nTHEN 1 END"
        cr = "CASE\rWHEN x > 0\rTHEN 1 END"
        self.assertEqual(_norm(crlf), _norm(lf))
        self.assertEqual(_norm(cr), _norm(lf))

    def test_leading_whitespace_stripped(self):
        """Leading whitespace is removed."""
        self.assertEqual(_norm("   SUM(x)"), "SUM(x)")

    def test_trailing_whitespace_stripped(self):
        """Trailing whitespace and newlines are removed."""
        self.assertEqual(_norm("SUM(x)   "), "SUM(x)")
        self.assertEqual(_norm("SUM(x)\n\n    "), "SUM(x)")

    def test_keywords_separated_by_single_space(self):
        """Multiple spaces between keyword tokens collapse to one."""
        self.assertEqual(
            _norm("CASE   WHEN   x   THEN   y   END"),
            "CASE WHEN x THEN y END",
        )

    # ---- SQL constructs that must not be corrupted ---------------------- #

    def test_count_star_safe(self):
        """COUNT(*) is preserved."""
        self.assertEqual(_norm("COUNT(*)"), "COUNT(*)")

    def test_count_star_with_spaces(self):
        """COUNT( * ) normalises to COUNT(*)."""
        self.assertEqual(_norm("COUNT( * )"), "COUNT(*)")

    def test_cast_expression(self):
        """CAST with spaces normalises correctly."""
        self.assertEqual(
            _norm("CAST( amount AS FLOAT )"), "CAST(amount AS FLOAT)"
        )

    def test_between_expression(self):
        """BETWEEN expression is handled correctly."""
        self.assertEqual(
            _norm("amount BETWEEN 0 AND 100"),
            "amount BETWEEN 0 AND 100",
        )

    def test_is_null(self):
        """IS NULL and IS NOT NULL are handled correctly."""
        self.assertEqual(_norm("x IS NULL"), "x IS NULL")
        self.assertEqual(_norm("x IS NOT NULL"), "x IS NOT NULL")

    def test_unary_minus_not_fused_into_comment(self):
        """'a - -b' must not become 'a--b' (a SQL comment start)."""
        out = _norm("a - -b")
        self.assertNotIn("--", out)

    def test_unary_plus_not_fused_into_block_comment(self):
        """'a / *b' must not become 'a/*b' (a SQL block-comment start)."""
        self.assertNotIn("/*", _norm("a / *b"))

    def test_schema_qualified_table(self):
        """schema.table dot notation is preserved."""
        self.assertEqual(_norm("schema.table"), "schema.table")

    def test_colon_cast_postgres(self):
        """Colon cast (::) for PostgreSQL is tightened."""
        self.assertEqual(_norm("value :: INT"), "value::INT")

    # ---- Edge cases ----------------------------------------------------- #

    def test_empty_string(self):
        """Empty string returns empty string."""
        self.assertEqual(_norm(""), "")

    def test_whitespace_only(self):
        """Whitespace-only input returns empty string."""
        self.assertEqual(_norm("   "), "")
        self.assertEqual(_norm("\n\n  \t"), "")

    def test_single_identifier(self):
        """Single identifier is returned unchanged."""
        self.assertEqual(_norm("revenue"), "revenue")

    def test_deeply_nested_parens(self):
        """Deeply nested function calls are normalised correctly."""
        self.assertEqual(_norm("f( g( h( a + b ) ) )"), "f(g(h(a+b)))")

    def test_very_long_multiline_case(self):
        """Realistic multi-branch CASE collapses to a single line."""
        sql = (
            "SUM(CASE\n"
            "    WHEN stage = 'Closed Won' AND amount > 0\n"
            "    THEN amount\n"
            "    WHEN stage = 'In Progress'\n"
            "    THEN amount * 0.5\n"
            "    ELSE 0\n"
            "END)\n\n    "
        )
        out = _norm(sql)
        self.assertNotIn("\n", out)
        self.assertIn("'Closed Won'", out)
        self.assertIn("'In Progress'", out)
        self.assertIn("0.5", out)

    def test_unclosed_string_literal_no_crash(self):
        """Scanner reaching EOF inside a literal must not raise."""
        try:
            out = _norm("CASE WHEN x = 'unclosed")
            self.assertIsInstance(out, str)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.fail(f"Raised exception on unclosed literal: {exc}")

    def test_unclosed_block_comment_no_crash(self):
        """Scanner reaching EOF inside a block comment must not raise."""
        try:
            out = _norm("x + /* unclosed comment")
            self.assertIsInstance(out, str)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.fail(f"Raised exception on unclosed comment: {exc}")

    def test_idempotent(self):
        """Applying the normaliser twice gives the same result as once."""
        sql = (
            "SUM(CASE\n  WHEN a > 0\n  THEN revenue + cost\n  ELSE 0\nEND)\n\n"
        )
        once = _norm(sql)
        self.assertEqual(_norm(once), once)


# ---------------------------------------------------------------------------
# _qo_norm (adhoc metric dict wrapper)
# ---------------------------------------------------------------------------


class TestNormExpr(unittest.TestCase):
    """Tests for the adhoc-SQL dict normaliser."""

    def test_non_dict_passthrough(self):
        """Non-dict values are returned unchanged."""
        for val in ("raw_string", 42, None, ["list"]):
            with self.subTest(val=val):
                self.assertIs(_norm_expr(val), val)

    def test_dict_without_expression_type_passthrough(self):
        """Dict without expressionType is returned unchanged."""
        d = {"label": "m", "sqlExpression": "SUM(x + y)"}
        self.assertIs(_norm_expr(d), d)

    def test_dict_with_non_sql_expression_type_passthrough(self):
        """Dict with expressionType != 'SQL' is returned unchanged."""
        d = {"expressionType": "SIMPLE", "column": "revenue"}
        self.assertIs(_norm_expr(d), d)

    def test_adhoc_sql_dict_normaliseised(self):
        """Dict with expressionType='SQL' gets sqlExpression normalised."""
        d = {
            "expressionType": "SQL",
            "sqlExpression": "SUM( x + y )",
            "label": "m",
        }
        self.assertEqual(_norm_expr(d)["sqlExpression"], "SUM(x+y)")

    def test_original_dict_not_mutated(self):
        """The original dict object is not modified in place."""
        original_sql = "SUM( x + y )"
        d = {"expressionType": "SQL", "sqlExpression": original_sql}
        _norm_expr(d)
        self.assertEqual(d["sqlExpression"], original_sql)

    def test_other_fields_preserved(self):
        """Non-sqlExpression fields in the dict are preserved unchanged."""
        d = {
            "expressionType": "SQL",
            "sqlExpression": "SUM( x )",
            "label": "my metric",
            "optionName": "abc123",
        }
        result = _norm_expr(d)
        self.assertEqual(result["label"], "my metric")
        self.assertEqual(result["optionName"], "abc123")

    def test_result_is_new_dict(self):
        """The returned dict is a new object, not the original."""
        d = {"expressionType": "SQL", "sqlExpression": "SUM( x )"}
        self.assertIsNot(_norm_expr(d), d)

    def test_production_metric_pattern1(self):
        """Multiline CASE metric is flattened and literal is preserved."""
        sql = "SUM(CASE \n        WHEN \"Stage\" = 'Closed Won'\n    END)\n\n    "
        d = {"expressionType": "SQL", "sqlExpression": sql, "label": "m"}
        result = _norm_expr(d)
        self.assertNotIn("\n", result["sqlExpression"])
        self.assertIn("'Closed Won'", result["sqlExpression"])

    def test_production_metric_pattern2(self):
        """Operator-spacing variants converge to the same normalised form."""
        sql = 'SUM("Y1 Renewal" + "Y1 Not Renewal")'
        d_spaces = {"expressionType": "SQL", "sqlExpression": sql}
        d_nospace = {
            "expressionType": "SQL",
            "sqlExpression": sql.replace(" + ", "+"),
        }
        self.assertEqual(
            _norm_expr(d_spaces)["sqlExpression"],
            _norm_expr(d_nospace)["sqlExpression"],
        )


# ---------------------------------------------------------------------------
# _qo_norm_orderby (orderby item wrapper)
# ---------------------------------------------------------------------------


class TestNormOrderby(unittest.TestCase):
    """Tests for the orderby item wrapper."""

    def test_non_sequence_passthrough(self):
        """Non-list/non-tuple values are returned unchanged."""
        for val in ("string", 42, None):
            with self.subTest(val=val):
                self.assertIs(_norm_ob(val), val)

    def test_empty_list_passthrough(self):
        """Empty list is returned unchanged."""
        val: list = []
        self.assertIs(_norm_ob(val), val)

    def test_empty_tuple_passthrough(self):
        """Empty tuple is returned unchanged."""
        val: tuple = ()
        self.assertIs(_norm_ob(val), val)

    def test_list_item_first_element_normaliseised(self):
        """First element of a list orderby item is normalised."""
        metric = _mk_metric("SUM( x + y )")
        result = _norm_ob([metric, True])
        self.assertEqual(result[0]["sqlExpression"], "SUM(x+y)")
        self.assertEqual(result[1], True)

    def test_tuple_item_converted_to_list(self):
        """Tuple orderby item is converted to list and normalised."""
        metric = _mk_metric("SUM( x + y )")
        result = _norm_ob((metric, False))
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["sqlExpression"], "SUM(x+y)")
        self.assertEqual(result[1], False)

    def test_non_sql_first_element_passthrough(self):
        """Non-adhoc-SQL first element is passed through unchanged."""
        result = _norm_ob(["plain_column", True])
        self.assertEqual(result[0], "plain_column")

    def test_original_metric_dict_not_mutated(self):
        """The original metric dict inside the orderby item is not modified."""
        original_sql = "SUM( x + y )"
        metric = _mk_metric(original_sql)
        _norm_ob([metric, True])
        self.assertEqual(metric["sqlExpression"], original_sql)

    def test_result_is_new_list(self):
        """The returned list is a new object."""
        metric = _mk_metric("SUM( x )")
        item = [metric, True]
        self.assertIsNot(_norm_ob(item), item)

    def test_extra_elements_preserved(self):
        """Elements beyond [metric, bool] are preserved."""
        metric = _mk_metric("SUM( x )")
        result = _norm_ob([metric, True, "extra"])
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
        """Replace the orig function seen by the patched closure."""
        _P["_qo_orig_cache_key"] = fn

    def _make_obj(self, metrics=None, columns=None, orderby=None, slm=None):
        """Create a minimal stub that patched_cache_key can operate on."""

        class Obj:
            """Minimal QueryObject stub."""

            def __init__(self):
                """Initialise stub with None defaults for all QueryObject fields."""
                self.metrics = None
                self.columns = None
                self.orderby = None
                self.series_limit_metric = None

        obj = Obj()
        obj.metrics = metrics if metrics is not None else []
        obj.columns = columns if columns is not None else []
        obj.orderby = orderby if orderby is not None else []
        obj.series_limit_metric = slm
        return obj

    # ---- Restore behaviour --------------------------------------------- #

    def test_metrics_restored_after_call(self):
        """Original metrics list and dict objects are restored after hashing."""
        metric = _mk_metric("SUM( x + y )")
        obj = self._make_obj(metrics=[metric])
        self._set_orig(_orig_returning_h)

        _patched_ck(obj)

        self.assertIs(obj.metrics[0], metric)
        self.assertEqual(obj.metrics[0]["sqlExpression"], "SUM( x + y )")

    def test_columns_restored_after_call(self):
        """Original columns are restored after hashing."""
        col = _mk_metric("MAX( a )")
        obj = self._make_obj(columns=[col])
        self._set_orig(_orig_returning_h)
        _patched_ck(obj)
        self.assertIs(obj.columns[0], col)

    def test_orderby_restored_after_call(self):
        """Original orderby is restored after hashing."""
        metric = _mk_metric("SUM( x )")
        ob = [metric, True]
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
        """None series_limit_metric is not normalised and stays None."""
        obj = self._make_obj(slm=None)
        self._set_orig(_orig_returning_h)
        _patched_ck(obj)
        self.assertIsNone(obj.series_limit_metric)

    def test_attributes_restored_even_when_orig_raises(self):
        """Attributes are restored via finally even if orig raises."""
        metric = _mk_metric("SUM( x + y )")
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
        self.assertEqual(obj.metrics[0]["sqlExpression"], "SUM( x + y )")

    # ---- Normalised SQL seen during the call ---------------------------- #

    def test_normalise_sql_seen_during_call(self):
        """The orig receives normalised SQL, not the original strings."""
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
        metric = _mk_metric("SUM( a + b )")
        obj = self._make_obj(metrics=[metric], orderby=[[metric, True]])

        _patched_ck(obj)

        self.assertEqual(seen["metrics"], ["SUM(a+b)"])
        self.assertEqual(seen["orderby"], ["SUM(a+b)"])

    def test_original_dict_not_mutated_by_call(self):
        """The original metric dict is not modified in place during hashing."""
        original_sql = "SUM( a + b )"
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

    def test_none_metrics_treated_as_empty(self):
        """None attributes are treated as empty during hashing, then restored."""
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
        obj = self._make_obj()
        self.assertEqual(_patched_ck(obj), "abc123")

    def test_extra_kwargs_forwarded(self):
        """Keyword arguments such as datasource and rls are forwarded to orig."""
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
        obj = self._make_obj()
        _patched_ck(obj, datasource="ds:1", rls="[]", changed_on="2026-01-01")
        self.assertEqual(received["datasource"], "ds:1")
        self.assertEqual(received["rls"], "[]")


# ---------------------------------------------------------------------------
# End-to-end convergence
# ---------------------------------------------------------------------------


class TestConvergence(unittest.TestCase):
    """Tests that worker and UI SQL forms converge to the same normalised hash input."""

    def _normalise(self, sql):
        """Normalise an sqlExpression string via the adhoc dict wrapper."""
        d = {"expressionType": "SQL", "sqlExpression": sql}
        return _norm_expr(d)["sqlExpression"]

    def test_multiline_case_worker_vs_ui(self):
        """Worker (multiline) and UI (single-line) CASE converge."""
        worker_sql = (
            "SUM(CASE \n        WHEN \"Stage\" = 'Closed Won' \n"
            "        AND amount > 0\n    END)\n\n    "
        )
        ui_sql = "SUM(CASE WHEN \"Stage\" = 'Closed Won' AND amount>0 END)"
        self.assertEqual(self._normalise(worker_sql), self._normalise(ui_sql))

    def test_operator_spacing_worker_vs_ui(self):
        """Worker (no spaces around +) and UI (with spaces) converge."""
        worker_sql = 'SUM("Y1 Renewal"+"Y1 Not Renewal")'
        ui_sql = 'SUM("Y1 Renewal" + "Y1 Not Renewal")'
        self.assertEqual(self._normalise(worker_sql), self._normalise(ui_sql))

    def test_crlf_vs_lf(self):
        """CRLF and LF line endings produce the same normalised form."""
        crlf = "SUM(CASE\r\n  WHEN x > 0\r\n  THEN 1\r\nEND)"
        lf = "SUM(CASE\n  WHEN x > 0\n  THEN 1\nEND)"
        self.assertEqual(self._normalise(crlf), self._normalise(lf))

    def test_indentation_variants(self):
        """Tab-indented and space-indented SQL produce the same normalised form."""
        tab = "SUM(CASE\n\tWHEN x > 0\n\tTHEN 1\nEND)"
        spaces = "SUM(CASE\n    WHEN x > 0\n    THEN 1\nEND)"
        self.assertEqual(self._normalise(tab), self._normalise(spaces))

    def test_trailing_whitespace_variants(self):
        """All trailing-whitespace variants produce the same normalised form."""
        forms = [
            "SUM(x)",
            "SUM(x)   ",
            "SUM(x)\n",
            "SUM(x)\n\n    ",
            "SUM(x)\t\t",
        ]
        normed = {self._normalise(f) for f in forms}
        self.assertEqual(
            len(normed), 1, f"Trailing whitespace forms diverged: {normed}"
        )

    def test_orderby_and_metric_converge(self):
        """pivot_table_v2 sets orderby[0][0] = metrics[0]; both must converge."""
        sql = "SUM(CASE \n        WHEN \"Stage\" = 'Closed Won'\n    END)\n\n    "
        metric = {"expressionType": "SQL", "sqlExpression": sql, "label": "m"}
        ob_item = [metric, True]
        self.assertEqual(
            _norm_expr(metric)["sqlExpression"],
            _norm_ob(ob_item)[0]["sqlExpression"],
        )

    def test_five_metrics_all_converge(self):
        """Five SQL metric pairs (worker vs UI forms) all converge."""
        pairs = [
            (
                "SUM(CASE \n  WHEN s = 'Won'\n  THEN a\n  ELSE 0\nEND)\n\n    ",
                "SUM(CASE WHEN s='Won' THEN a ELSE 0 END)",
            ),
            ('SUM("Y1"+"Y2")', 'SUM("Y1" + "Y2")'),
            ("MAX( amount )", "MAX(amount)"),
            ("COUNT(DISTINCT   stage)", "COUNT(DISTINCT stage)"),
            ("SUM(target_amount\n  * 1.1)", "SUM(target_amount*1.1)"),
        ]
        for worker_sql, ui_sql in pairs:
            with self.subTest(worker_sql=worker_sql[:40]):
                self.assertEqual(
                    self._normalise(worker_sql), self._normalise(ui_sql)
                )

    def test_different_literals_do_not_collide(self):
        """Two queries differing only in a string value must not collide."""
        sql_a = "CASE WHEN stage = 'Closed - Won' THEN 1 END"
        sql_b = "CASE WHEN stage = 'ClosedWon' THEN 1 END"
        self.assertNotEqual(self._normalise(sql_a), self._normalise(sql_b))

    def test_different_sql_does_not_collide(self):
        """Two semantically distinct queries must produce different normalised forms."""
        self.assertNotEqual(
            self._normalise("SUM(amount)"), self._normalise("COUNT(amount)")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
