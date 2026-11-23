import type { Field, FieldFormat, Hierarchy, Parameter } from "../types";

export type Agg =
  | "sum" | "avg" | "count" | "count_distinct" | "min" | "max" | "median" | "percentile" | "stdev" | "var";
export type Grain = "year" | "quarter" | "month" | "week" | "day" | "hour" | "minute";
export type Part = "year" | "quarter" | "month" | "week" | "weekday" | "day" | "hour";
export type CalcType = "running_total" | "pct_total" | "difference" | "pct_change" | "rank" | "moving_avg";
export type ShelfName = "columns" | "rows" | "color" | "size" | "label" | "tooltip" | "detail";
export type ChartType =
  | "table" | "kpi" | "bar" | "stacked" | "line" | "area" | "combo" | "donut" | "scatter" | "heatmap"
  | "treemap" | "map";

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
  unit?: "day" | "week" | "month" | "quarter" | "year";
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
