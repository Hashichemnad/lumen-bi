import type { Field, FieldFormat, Hierarchy, Parameter } from "../types";

export type Agg =
  | "sum" | "avg" | "count" | "count_distinct" | "min" | "max" | "median" | "percentile" | "stdev" | "var";
export type Grain = "fiscal_year" | "fiscal_quarter" | "year" | "quarter" | "month" | "week" | "day" | "hour" | "minute";
export type Part = "fiscal_year" | "fiscal_quarter" | "year" | "quarter" | "month" | "week" | "weekday" | "day" | "hour";
export type DateUnit = "day" | "week" | "month" | "quarter" | "year" | "fiscal_quarter" | "fiscal_year";
export type CalcType = "running_total" | "pct_total" | "difference" | "pct_change" | "rank" | "moving_avg";
export type ShelfName = "columns" | "rows" | "color" | "size" | "label" | "tooltip" | "detail";
export type ChartType =
  | "table" | "kpi" | "bar" | "stacked" | "line" | "area" | "combo" | "donut" | "scatter" | "heatmap"
  | "treemap" | "map"
  | "pyramid" | "stacked_pyramid" | "parallel" | "radar" | "timeline" | "time_bar" | "time_combo" | "calendar"
  | "gantt" | "gauge" | "polar_gauge" | "flat_gauge" | "bullet" | "rating" | "dot_h" | "dot_v" | "waterfall"
  | "stacked_waterfall" | "funnel" | "origami" | "sankey" | "geomap";

export interface TableCalc {
  type: CalcType;
  window?: number;
  along?: string;
  partition_by?: string[];
}

/** A field placed on a shelf. Its id is also its key in query results. */
export interface Pill {
  id: string;
  field: string; // field name, or "*" for number of rows
  role: "dimension" | "measure";
  agg?: Agg;
  p?: number;
  grain?: Grain;
  part?: Part;
  calc?: TableCalc;
}

export type FilterKind = "values" | "range" | "relative" | "top" | "text";

export interface FilterPill {
  id: string;
  field: string;
  kind: FilterKind;
  grain?: Grain;
  part?: Part;
  // values
  values?: unknown[];
  exclude?: boolean;
  // range (min/max may be numbers or ISO dates); with agg it applies to totals per mark
  min?: number | string | null;
  max?: number | string | null;
  agg?: Agg;
  // relative dates
  unit?: DateUnit;
  n?: number;
  // top / bottom N
  top?: "top" | "bottom";
  by?: { field: string; agg?: Agg };
  // text
  op?: "contains" | "not_contains" | "starts_with" | "ends_with";
  text?: string;
  /** Shown as a quick filter control above the chart (for viewers) */
  show?: boolean;
}

export interface RefLine {
  kind: "constant" | "avg" | "median" | "min" | "max";
  value?: number;
  label?: string;
}

export interface ChartOptions {
  title?: string;
  subtitle?: string;
  labels?: "none" | "ends" | "all";
  legend?: boolean;
  totals?: boolean;
  refLine?: RefLine | null;
  trend?: boolean;
  colorScale?: boolean; // tables: shade measure cells
  percentStack?: boolean; // stacked bars as 100%
  smooth?: boolean;
  /** gauges: the maximum when no target measure is given */
  gaugeMax?: number;
  /** rating: the value that means full marks (default 5) */
  ratingMax?: number;
  /** waterfall, origami, funnel: show the total */
  showTotal?: boolean;
  /** funnel, origami: order by value (default) or keep the query's order */
  order?: "value" | "data";
  /** geo map: uploaded boundary file id */
  boundary?: number;
  /** line and area charts over dates: extend the series (statistics on the server, not AI) */
  forecast?: { periods: number; interval: 0.8 | 0.9 | 0.95 | 0.99 } | null;
}

export interface ChartConfig {
  version: 1;
  type: ChartType | "auto";
  shelves: Record<ShelfName, Pill[]>;
  filters: FilterPill[];
  sort: { pill: string; dir: "asc" | "desc" } | null;
  options: ChartOptions;
  params: Record<string, unknown>;
  limit?: number;
}

export interface Catalog {
  id: number;
  name: string;
  description: string;
  row_count: number;
  fields: Field[];
  hierarchies: Hierarchy[];
  parameters: Parameter[];
}

export interface SavedChart {
  id: number;
  name: string;
  description: string;
  dataset_id: number;
  dataset_name: string;
  owner_id: number | null;
  owner_name: string;
  config: ChartConfig;
  query: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  can_edit: boolean;
  workspace_id: number | null;
  workspace_name: string;
  level: "view" | "edit" | "manage";
}

export const SHELVES: ShelfName[] = ["columns", "rows", "color", "size", "label", "tooltip", "detail"];

export const emptyConfig = (): ChartConfig => ({
  version: 1,
  type: "auto",
  shelves: { columns: [], rows: [], color: [], size: [], label: [], tooltip: [], detail: [] },
  filters: [],
  sort: null,
  options: { labels: "none", legend: true },
  params: {},
});

let counter = 0;
export const newId = (prefix = "p") => `${prefix}${Date.now().toString(36)}${(counter++).toString(36)}`;

export type { FieldFormat };
