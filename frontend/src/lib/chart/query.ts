import type { QueryColumn } from "../types";
import type { ChartConfig, ChartType, FilterPill, Pill } from "./types";
import { SHELVES } from "./types";

type Spec = Record<string, unknown>;

export function allPills(cfg: ChartConfig): Pill[] {
  return SHELVES.flatMap((s) => cfg.shelves[s]);
}

export const dimensionsOf = (cfg: ChartConfig) => allPills(cfg).filter((p) => p.role === "dimension");
export const measuresOf = (cfg: ChartConfig) => allPills(cfg).filter((p) => p.role === "measure");

export function filterToSpec(f: FilterPill): Spec[] {
  const base = { field: f.field, ...(f.grain ? { grain: f.grain } : {}), ...(f.part ? { part: f.part } : {}) };
  switch (f.kind) {
    case "values":
      if (!f.values) return [];
      return [{ ...base, op: f.exclude ? "not_in" : "in", values: f.values }];
    case "range":
      if ((f.min === null || f.min === undefined || f.min === "") && (f.max === null || f.max === undefined || f.max === "")) return [];
      return [{ ...base, ...(f.agg ? { agg: f.agg } : {}), op: "between", values: [f.min ?? null, f.max ?? null] }];
    case "relative":
      return f.unit && f.n ? [{ ...base, op: "relative", unit: f.unit, n: f.n }] : [];
    case "top":
      return f.n && f.by ? [{ ...base, op: f.top ?? "top", n: f.n, by: f.by }] : [];
    case "text":
      return f.text ? [{ ...base, op: f.op ?? "contains", value: f.text }] : [];
  }
}

function pillToDimension(p: Pill): Spec {
  return { field: p.field, key: p.id, ...(p.grain ? { grain: p.grain } : {}), ...(p.part ? { part: p.part } : {}) };
}

function pillToMeasure(p: Pill): Spec {
  return {
    field: p.field,
    key: p.id,
    ...(p.agg ? { agg: p.agg } : {}),
    ...(p.agg === "percentile" ? { p: p.p ?? 0.9 } : {}),
    ...(p.calc ? { calc: p.calc } : {}),
  };
}

/** The query spec (without "dataset") a chart runs. Every pill's id is its key in the result. */
export function buildQuery(cfg: ChartConfig, type: ChartType, extraFilters: Spec[] = []): Spec {
  const dims = dimensionsOf(cfg);
  const measures = measuresOf(cfg);
  const spec: Spec = {
    dimensions: dims.map(pillToDimension),
    measures: measures.map(pillToMeasure),
    filters: [...cfg.filters.flatMap(filterToSpec), ...extraFilters],
    limit: cfg.limit ?? 5000,
  };
  if (cfg.sort && [...dims, ...measures].some((p) => p.id === cfg.sort!.pill)) {
    spec.sort = [{ key: cfg.sort.pill, dir: cfg.sort.dir }];
  }
  if (cfg.options.totals || type === "kpi") spec.totals = true;
  if (Object.keys(cfg.params).length) spec.params = cfg.params;
  return spec;
}

/** Filters that pin one mark: each dimension of the row equals its value (same grain). */
export function markFilters(cfg: ChartConfig, columns: QueryColumn[], row: unknown[]): Spec[] {
  const out: Spec[] = [];
  columns.forEach((c, i) => {
    if (c.role !== "dimension") return;
    const pill = dimensionsOf(cfg).find((p) => p.id === c.key);
    if (!pill) return;
    const base = { field: pill.field, ...(pill.grain ? { grain: pill.grain } : {}), ...(pill.part ? { part: pill.part } : {}) };
    out.push(row[i] === null ? { ...base, op: "is_null" } : { ...base, op: "eq", value: row[i] });
  });
  return out;
}

/** "View data": the rows behind the chart, or behind one mark. */
export function underlyingQuery(cfg: ChartConfig, markSpec: Spec[] = []): Spec {
  return {
    mode: "rows",
    filters: [...cfg.filters.flatMap(filterToSpec).filter((f) => !("agg" in f)), ...markSpec],
    limit: 500,
    ...(Object.keys(cfg.params).length ? { params: cfg.params } : {}),
  };
}
