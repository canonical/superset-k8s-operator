# Superset MCP Notes

## Pagination

- Superset's API uses **1-based pagination** (`page: 1` is the first page).
- Passing `page: 0` causes an internal error with a non-descriptive message (e.g., `err_178514...`).
- Omit `page` entirely or start from `page: 1` to avoid this.

## Tool Parameters

All tools accept a `request` object (Pydantic model). The `parameters_hint` field in `search_tools` results tells you the argument name.

### `get_chart_data`

```json
{
  "request": {
    "identifier": 99,
    "format": "json",
    "limit": null,
    "use_cache": true,
    "force_refresh": false,
    "cache_timeout": null,
    "extra_form_data": null,
    "form_data_key": null
  }
}
```

- `identifier` accepts numeric ID or UUID string. Aliases: `id`, `chart_id`.
- Either `identifier` or `form_data_key` must be provided — omitting both causes a validation error (reported as opaque internal error).
- `format`: `"json"` (default), `"csv"`, or `"excel"`.

### `list_dashboards` / `list_charts`

- Use `select_columns: ["id", "dashboard_title", "charts", ...]` to request extra fields like `charts`.
- Filtering: `filter_columns` + `filter_values` arrays.

## Source

This MCP is bundled in Apache Superset (`superset/mcp_service/`). Tool schemas are defined in:
- `superset/mcp_service/chart/schemas.py` — `GetChartDataRequest`, `GetChartInfoRequest`, etc.
- `superset/mcp_service/common/cache_schemas.py` — `QueryCacheControl` (base for cache params).

## Dashboard Data Gotchas

- Chart data returns what the chart is configured to show (its saved query), not raw table data.
- **Timeseries charts may lose the time dimension** when using the fallback query path (i.e. when chart has no saved `query_context`). Root cause: `extract_x_axis_col` in `chart_helpers.py` only reads the newer `x_axis` field from `form_data`, but ignores the older `granularity_sqla` field. Charts configured with `granularity_sqla` (like the birth_names "Trends" chart) return totals instead of per-year rows.
  - Source: `superset/mcp_service/chart/chart_helpers.py` → `extract_x_axis_col()`
  - This is a **bug in the fallback path**, not a fundamental limitation. Charts with a saved `query_context` should work correctly.
- For time-based analysis on affected charts, fall back to `execute_sql`. The time column in `birth_names` is `ds` (e.g., `strftime('%Y', ds)` to extract year in SQLite)..
