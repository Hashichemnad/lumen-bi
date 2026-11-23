# Modelling data

Open a dataset → **Model**. Changes here are what people see when they build charts (milestone 8).

## Fields

Every column becomes a field when the dataset first syncs, with defaults you can change:

| Setting | Default | Notes |
| --- | --- | --- |
| Label | `order_date` → "Order Date", `AnnualRevenue` → "Annual Revenue" | The name people see |
| Role | Numbers are **measures** (added up); text, dates and IDs are **dimensions** (grouped by) | Numbers named …id, …code, …number, …year count as dimensions |
| Format | Number (0 or 2 decimals), Date, Text | Also Currency (e.g. AED) and Percent |
| Default total | Sum for measures | Average, count, distinct count, min, max, median |
| Hidden | Off | Hidden fields stay usable in formulas but are not offered in charts |

Your edits are kept when the dataset syncs again. New source columns get fields automatically; a column that
disappears is marked **not in source** (its settings come back if it returns) and can then be deleted.

## Calculated fields

Fields are written in `[brackets]`, parameters start with `@`. The editor checks the formula while you type,
shows its type and the first values, and points at the exact spot of any mistake.

```
[Sales] - [Cost]                                        profit per row
SUM([Sales] - [Cost]) / SUM([Sales])                     margin (an aggregate: correct at any level)
IF [Region] = 'UAE' THEN 'Home'
ELSEIF [Region] IN ('KSA', 'QAT', 'OMN') THEN 'GCC'
ELSE 'Other' END
DATETRUNC('month', [Order Date])                         first day of the month
DATEDIFF('day', [Order Date], [Ship Date])                days to ship
[Amount] / @[FX Rate]                                     uses a parameter
'Q' + STR(QUARTER([Order Date]))                          text: Q1, Q2…
```

Rules worth knowing:

- Division by zero gives an empty value, not an error.
- A formula either works row by row (`[Sales] - [Cost]`) or on totals (`SUM([Sales])`), not both mixed:
  `SUM([Sales]) - [Cost]` is refused. Aggregate both sides instead.
- Text is joined with `+`; wrap numbers and dates in `STR()` first.
- Dates: `#2026-01-31#`. Date parts: year, quarter, month, week, day, hour, minute, second.
- Renaming a calculated field updates the formulas that use it. A field used by others cannot be deleted.
- If a source column used by a formula disappears, the field is marked **broken** with the reason.

### Functions

| Kind | Functions |
| --- | --- |
| Aggregate | SUM, AVG, COUNT, COUNTD, MIN, MAX, MEDIAN, PERCENTILE(x, 0.9), STDEV, VAR |
| Logic | IF … THEN … ELSEIF … ELSE … END, CASE … WHEN … THEN … ELSE … END, IIF, IFNULL, COALESCE, ZN, ISNULL, IN, IS NULL, AND, OR, NOT |
| Number | ROUND(x, digits), ABS, CEILING, FLOOR, SQRT, POWER, EXP, LN, LOG(x[, base]), SIGN, DIV, MIN(a, b), MAX(a, b) |
| Text | LEN, UPPER, LOWER, TRIM, LTRIM, RTRIM, LEFT, RIGHT, MID, CONTAINS, STARTSWITH, ENDSWITH, REPLACE, SPLIT, FIND, STR |
| Date | YEAR, QUARTER, MONTH, WEEK, DAY, WEEKDAY, DATETRUNC, DATEPART, DATEADD, DATEDIFF, MAKEDATE, TODAY, NOW |
| Convert | INT, FLOAT, DATE, DATETIME |

Safety: formulas are parsed and translated by Lumen; only these functions exist, text values are always
treated as data, and nothing typed in a formula is passed to the database as code.

## Groups and bins

- **Group**: put values into named buckets (KSA, QAT, OMN → "GCC"), with a name for everything else or keep
  the original value.
- **Bins**: turn a number into ranges of a fixed size (order value in steps of 1,000).

## Hierarchies

A drill path such as Country → Emirate → Branch, used for drill-down on dashboards. Date fields drill year →
quarter → month → day by themselves.

## Parameters

A value people can change on a dashboard (exchange rate, target, top N), used in formulas as `@Name` or
`@[Name with spaces]`. Each has a type, a default and optionally a fixed list of allowed values.

## Joining and combining datasets

Data → **Join or combine datasets**.

- **Join**: start from one dataset and add columns from others by matching columns (orders + customer
  details by customer ID). "Keep all rows" keeps every row of the left side even without a match; "Only
  matches" keeps rows found on both sides; "All from both" keeps everything. Joined columns are named
  `prefix.column` (e.g. `Customers.name`).
- **Union**: stack rows of datasets with the same columns (one file per branch or month), optionally with a
  column saying where each row came from.

The result is a dataset like any other (its own fields, formulas, and later row-level security) and is
rebuilt automatically whenever one of its sources syncs. A dataset that others are built from cannot be
deleted until those are changed or deleted.
