# Building charts

**Charts → New chart**, choose a dataset you can use (Explorers and up). Row-level security applies to
everything you see while building: you only ever chart the rows you may see.

## The builder

- **Fields** (left): dimensions (blue, things you split by) and measures (green, things you add up),
  hierarchies (e.g. Location → Country → Emirate), parameters, and **Number of rows**. Drag a field to a
  shelf, or double-click it to place it automatically.
- **Columns and Rows**: dimensions on one and measures on the other give bars and lines. Measures on
  Columns make horizontal bars. Measures on both make a scatter plot.
- **Marks**: Color (a dimension gives a colour per value, a measure shades from light to dark), Size,
  Label, Tooltip (extra values when hovering), Detail (more marks without colour, e.g. one dot per branch).
- **Filters**: drop a field on Filters to pick values (with search), set a range, choose a relative
  period ("last 12 months"), keep the top or bottom N by a measure, or match text. A filter on a measure
  can compare each mark's total (e.g. regions with Sum of Amount above 1M).
- **Pill menu** (click a pill): change the total (sum, average, median, count, count distinct, min, max,
  90th percentile, std. deviation), add a quick calculation (running total, % of total, difference, %
  change, rank, moving average), change a date level (year, quarter, month, week, day, or parts such as
  month of year), drill down or up a hierarchy, sort, switch between dimension and measure, or filter.
- **Click a mark** (a bar, point, cell or row) to keep only it, exclude it, drill down into it, or see
  the rows behind it.
- **Show Me** (right) lists every chart type; the ones that fit the fields on the shelves are enabled,
  and **Automatic** picks the best one as you build.
- **Options**: title, value labels, legend, grand totals and shading (tables), 100% stacking, smooth lines,
  trend line, a reference line (average, median, min, max or a fixed target), and the most results.
- **Toolbar**: undo/redo, swap rows and columns, clear, view data, export CSV (Explorers and up), PNG
  image, save, save a copy.

## Chart types

| Type | Use it for | Build it with |
| --- | --- | --- |
| Table / pivot table | exact numbers; a pivot when a dimension is on Columns | any fields |
| Number | headline figures, with a trend line when a date is added | measures (+ a date) |
| Bars, stacked bars | comparing values; parts of a whole | dimension + measure (+ Color) |
| Line, area | change over time | date + measure (+ Color) |
| Bars + line | two measures on the same scale | dimension + 2 measures |
| Scatter | how two measures relate | measure on Columns and on Rows (+ Detail) |
| Heat map | a grid of two dimensions | dimension on Columns, on Rows, measure |
| Treemap, donut | share of a whole, few parts | dimension + measure (Size) |
| Map | countries (filled) or emirates and Gulf cities (bubbles) | a place dimension + measure |

Maps recognise country names and codes in English and Arabic (UAE, KSA, الإمارات…) and the seven
emirates, Al Ain and major Gulf cities. Places they do not know are listed under the map.

## How charts look (house rules)

Charts follow one set of rules so they read the same everywhere:

- One value axis per chart. "Bars + line" shares one scale; two measures on very different scales belong
  in two charts.
- Colours follow the value, not its position: "Retail" keeps its colour when a filter removes others.
  At most 8 colours (3 on scatter charts and maps); smaller values fold into a grey "Other".
- A single series uses one colour; a legend appears only for two or more series.
- Thin bars with rounded ends, 2px lines, hairline gridlines, values in text colours.
- Every mark has a tooltip, and every value can also be read in the table view or with View data.
- Donuts are limited to 6 parts; bars compare values more exactly.

## Saved charts

Saving keeps the builder settings and the query. You see your own charts (admins see all); anyone who
opens a chart can save a copy. Dashboards (milestone 9) place charts together and share them.
