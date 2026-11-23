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

Show Me groups the types like this. Hover a type to see what it needs; types that fit the fields on the
shelves are enabled.

| Group | Type | Build it with |
| --- | --- | --- |
| Tables | Table / pivot (matrix) | any fields; a dimension on Columns makes a pivot |
| | Number | measures (+ a date for a sparkline) |
| | Rating | a measure shown as stars; "Full marks at" in Options (default 5); items on Rows |
| Bars | Bars (column or bar), Stacked | dimension + measure (+ Color); measures on Columns = horizontal |
| | Pyramid | categories on Rows; two groups on Color (e.g. gender) or two measures |
| | Stacked pyramid | categories on Rows, a dimension on Color, two measures (one per side) |
| Lines | Line, Area, Bars + line | date or dimension + measures (bars + line share one scale) |
| | Parallel | 2+ measures (one axis each), items on Detail, groups on Color |
| | Radar | 3+ measures (spokes), items on Color |
| Dates & times | Timeline, Time bars, Time combo | a date + measures, on a true time axis with a zoom slider |
| | Calendar | a date at Day level + a measure (one square per day) |
| | Gantt | task on Rows; start and end dates (Exact date) on Columns; Color optional |
| Gauges | Gauge, Ring, Progress bar | a measure; a second measure is the target, or set a maximum in Options |
| | Bullet | actual and target measures, items on Rows |
| Circles & dots | Donut, Scatter | as before |
| | Dots across / Dots up | items + measures (e.g. min, average and max side by side) |
| Grids | Heat map, Treemap | as before |
| Maps | World map | countries (filled) or emirates and Gulf cities (bubbles) |
| | Geo map | area names + a measure, on a map uploaded under Data → Maps |
| Pipelines | Waterfall, Stacked waterfall | steps + a measure (+ Color for the stacked one) |
| | Funnel | stages + a measure, with % of the first stage |
| | Origami | a dimension on Color, a measure on Size: blocks sized by value joined by folds |
| | Sankey | 2+ dimensions in order (from → to) and a measure |

Options for these types: gauge maximum, rating full marks, show the total (waterfall, Origami), order by
value or as sorted (funnel, Origami), and the map (Geo map).

## Maps you upload

**Data → Maps** (Creators): upload a GeoJSON file (up to 25 MB) of emirates, districts, communities or sales
territories and choose the property holding each area's name. Shapefiles and TopoJSON can be converted with
mapshaper.org. Area names are matched ignoring case and spaces. The map can then be chosen in a Geo map
chart's Options.

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

Export the data as CSV or Excel (formats kept: currency, percent, dates).

Saving keeps the builder settings and the query. You see your own charts (admins see all); anyone who
opens a chart can save a copy. Dashboards (milestone 9) place charts together and share them.
