# Query API

One query format is used by charts, dashboards, AI, exports and email reports. Row-level security always
applies (see [SECURITY.md](SECURITY.md)).

`POST /api/v1/query` (any signed-in user with access to the dataset)

```json
{
  "dataset": 12,
  "dimensions": [{"field": "Order Date", "grain": "month"}, "Region"],
  "measures": [
    {"field": "Amount"},
    {"field": "Amount", "agg": "avg", "calc": {"type": "pct_change"}},
    {"field": "Margin"},
    {"field": "*", "agg": "count"}
  ],
  "filters": [
    {"field": "Status", "op": "in", "values": ["Won"]},
    {"field": "Order Date", "op": "relative", "unit": "month", "n": 12},
    {"field": "Region", "op": "top", "n": 5, "by": {"field": "Amount", "agg": "sum"}},
    {"field": "Amount", "agg": "sum", "op": "gt", "value": 1000}
  ],
  "sort": [{"key": "sum:Amount", "dir": "desc"}],
  "limit": 5000,
  "params": {"FX Rate": 3.67},
  "totals": true
}
```

| Part | Options |
| --- | --- |
| Dimension | `field`; `grain` (year, quarter, month, week, day, hour, minute: truncates a date) or `part` (year, quarter, month, week, weekday 1=Mon, day, hour: a number); `key` |
| Measure | `field` (or `*` for number of rows); `agg` (sum, avg, count, count_distinct, min, max, median, percentile with `p`, stdev, var; default: the field's default total; ignored for aggregate calculated fields); `calc`; `key` |
| Table calculation | `type`: running_total, pct_total, difference, pct_change, rank, moving_avg (`window`, default 3). `along`: dimension key to compute along (default: first date dimension, else the first). `partition_by`: dimension keys to restart at (default: all other dimensions; `[]` = whole table). A top-level `table_calc` applies to every measure without its own. |
| Filter | `op`: eq, neq, in, not_in (`null` allowed in values), gt, gte, lt, lte, between (`[from, to]`, either may be null), contains, not_contains, starts_with, ends_with, is_null, not_null, relative (`unit`, `n`: last n units including the current one), top / bottom (`n`, `by`). `grain` / `part` filter on a date's grain or part. With `agg` (or on an aggregate field) the filter applies to totals per group. Top/bottom N are ranked after the other filters. |
| Sort | `key` of a dimension or measure; default: dimensions ascending |
| Limit | default 5000, at most `QUERY_MAX_ROWS` (50,000); `truncated` says if there were more |
| Totals | `totals: true` adds grand totals of each measure over the filtered rows (and the groups that pass aggregate filters) |
| Rows mode | `"mode": "rows", "fields": [...]`: plain rows (row-level fields only), for "view data" and exports |

Keys: a dimension is `Field` or `Field:grain` (`Order Date:month`) or `Field:part:month`; a measure is
`agg:Field` (`sum:Amount`), the field name for aggregate fields, `count:*` for rows, plus `:calc` when it
has a table calculation (`sum:Amount:pct_total`). Set `key` to choose your own.

Response:

```json
{"columns": [{"key": "Order Date:month", "label": "Order Date (Month)", "field": "Order Date",
              "role": "dimension", "type": "date", "format": {"type": "date", "grain": "month"}, ...}],
 "rows": [["2026-01-01", "UAE", 1200.5, null, 0.31, 48]],
 "totals": [null, null, 52000.0, null, 0.29, 2100],
 "truncated": false, "row_count": 1, "restricted": true, "cached": false, "ms": 42}
```

`restricted` is true when row-level security filtered the rows for this person.

Other endpoints:

- `POST /api/v1/query/export`: the same body, returns CSV (Explorers and up, up to `EXPORT_MAX_ROWS`; Excel
  export comes with milestone 10). Text starting with `=`, `+`, `-` or `@` is prefixed with `'` so
  spreadsheets do not run it.
- `GET /api/v1/datasets/{id}/values?field=Region&search=&order=value|frequency&limit=200`: distinct values
  the user may see, for filter lists.
- `GET /api/v1/catalog/datasets` and `/catalog/datasets/{id}`: datasets the user may query, with fields,
  hierarchies and parameters.
- Owners and admins can add `"as_user_id": 7` to a query to see it as that person (recorded in the audit log).

Errors are `400` with a readable `detail` (`Unknown field [Regoin]`, `Sum of needs a number field`); a query
running longer than `QUERY_TIMEOUT_SECONDS` is stopped with `504`.
