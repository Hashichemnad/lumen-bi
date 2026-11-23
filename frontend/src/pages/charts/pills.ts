import type { Agg, CalcType, Catalog, ChartConfig, FilterPill, Grain, Part, Pill, ShelfName } from "../../lib/chart/types";
import { SHELVES, newId } from "../../lib/chart/types";
import type { Field } from "../../lib/types";

export const AGG_LABEL: Record<Agg, string> = {
  sum: "Sum", avg: "Average", median: "Median", count: "Count", count_distinct: "Count distinct", min: "Minimum",
  max: "Maximum", percentile: "90th percentile", stdev: "Std. deviation", var: "Variance",
};
export const CALC_LABEL: Record<CalcType, string> = {
  running_total: "Running total", pct_total: "% of total", difference: "Difference from previous",
  pct_change: "% change from previous", rank: "Rank", moving_avg: "Moving average (3)",
};
export const GRAIN_LABEL: Record<Grain, string> = {
  year: "Year", quarter: "Quarter", month: "Month", week: "Week", day: "Day", hour: "Hour", minute: "Minute",
};
export const PART_LABEL: Record<Part, string> = {
  year: "Year number", quarter: "Quarter of year", month: "Month of year", week: "Week of year", weekday: "Day of week", day: "Day of month", hour: "Hour of day",
};
export const SHELF_LABEL: Record<ShelfName, string> = {
  columns: "Columns", rows: "Rows", color: "Color", size: "Size", label: "Label", tooltip: "Tooltip", detail: "Detail",
};
const DATE_DRILL: Grain[] = ["year", "quarter", "month", "day"];

export const isDate = (f?: Field) => !!f && (f.data_type === "date" || f.data_type === "datetime");
export const isNumber = (f?: Field) => !!f && (f.data_type === "integer" || f.data_type === "number");

export function fieldMap(catalog?: Catalog): Map<string, Field> {
  return new Map((catalog?.fields ?? []).map((f) => [f.name, f]));
}

export function fieldToPill(f: Field | "*", role?: "dimension" | "measure"): Pill {
  if (f === "*") return { id: newId(), field: "*", role: "measure", agg: "count" };
  const r = role ?? (f.is_aggregate ? "measure" : f.role);
  const pill: Pill = { id: newId(), field: f.name, role: r };
  if (r === "dimension" && isDate(f)) pill.grain = "month";
  if (r === "measure" && !f.is_aggregate && f.role === "dimension") pill.agg = "count_distinct";
  return pill;
}

export function pillText(p: Pill, fields: Map<string, Field>): string {
  if (p.field === "*") return "Number of rows";
  const f = fields.get(p.field);
  const name = f?.label || p.field;
  if (p.role === "measure") {
    if (f?.is_aggregate) return name + (p.calc ? ` · ${CALC_LABEL[p.calc.type]}` : "");
    const agg = p.agg ?? (f?.default_agg && f.default_agg !== "none" ? (f.default_agg as Agg) : isNumber(f) ? "sum" : "count");
    return `${name} (${AGG_LABEL[agg] ?? agg})${p.calc ? ` · ${CALC_LABEL[p.calc.type]}` : ""}`;
  }
  if (p.grain) return `${name} (${GRAIN_LABEL[p.grain]})`;
  if (p.part) return `${name} (${PART_LABEL[p.part]})`;
  return name;
}

/** Where a double-clicked field goes, like Tableau. */
export function autoShelf(cfg: ChartConfig, pill: Pill): ShelfName {
  if (pill.role === "measure") {
    if (cfg.shelves.rows.some((p) => p.role === "dimension") && !cfg.shelves.columns.some((p) => p.role === "measure")) return "columns";
    return "rows";
  }
  if (!cfg.shelves.columns.some((p) => p.role === "dimension") && !cfg.shelves.rows.some((p) => p.role === "dimension")) {
    return cfg.shelves.columns.some((p) => p.role === "measure") ? "rows" : "columns";
  }
  return cfg.shelves.columns.some((p) => p.role === "measure") ? "rows" : "columns";
}

export function findPill(cfg: ChartConfig, id: string): { shelf: ShelfName; index: number; pill: Pill } | null {
  for (const shelf of SHELVES) {
    const index = cfg.shelves[shelf].findIndex((p) => p.id === id);
    if (index >= 0) return { shelf, index, pill: cfg.shelves[shelf][index] };
  }
  return null;
}

export function updatePill(cfg: ChartConfig, id: string, change: (p: Pill) => Pill | null): ChartConfig {
  const shelves = { ...cfg.shelves };
  for (const s of SHELVES) {
    shelves[s] = cfg.shelves[s].flatMap((p) => {
      if (p.id !== id) return [p];
      const next = change(p);
      return next ? [next] : [];
    });
  }
  const sort = cfg.sort && !Object.values(shelves).flat().some((p) => p.id === cfg.sort!.pill) ? null : cfg.sort;
  return { ...cfg, shelves, sort };
}

// ------------------------------------------------------------------ drilling
export interface DrillStep {
  pill: Pill;
  label: string;
}

export function drillDown(p: Pill, catalog?: Catalog): DrillStep | null {
  const f = fieldMap(catalog).get(p.field);
  if (p.role !== "dimension") return null;
  if (isDate(f) && p.grain && DATE_DRILL.includes(p.grain) && p.grain !== "day") {
    const g = DATE_DRILL[DATE_DRILL.indexOf(p.grain) + 1];
    return { pill: { ...p, grain: g }, label: GRAIN_LABEL[g] };
  }
  for (const h of catalog?.hierarchies ?? []) {
    const i = h.levels.indexOf(p.field);
    if (i >= 0 && i < h.levels.length - 1) {
      const next = h.levels[i + 1];
      return { pill: { ...p, field: next, grain: undefined, part: undefined }, label: fieldMap(catalog).get(next)?.label || next };
    }
  }
  return null;
}

export function drillUp(p: Pill, catalog?: Catalog): DrillStep | null {
  const f = fieldMap(catalog).get(p.field);
  if (p.role !== "dimension") return null;
  if (isDate(f) && p.grain && DATE_DRILL.indexOf(p.grain) > 0) {
    const g = DATE_DRILL[DATE_DRILL.indexOf(p.grain) - 1];
    return { pill: { ...p, grain: g }, label: GRAIN_LABEL[g] };
  }
  for (const h of catalog?.hierarchies ?? []) {
    const i = h.levels.indexOf(p.field);
    if (i > 0) {
      const prev = h.levels[i - 1];
      return { pill: { ...p, field: prev }, label: fieldMap(catalog).get(prev)?.label || prev };
    }
  }
  return null;
}

/** Drilling up removes the "keep only" filters that drilling down added for that level. */
export function withoutDrillFilters(filters: FilterPill[], p: Pill): FilterPill[] {
  return filters.filter((f) => !(f.id.startsWith("drill") && f.field === p.field));
}

export function filterSummary(f: FilterPill, fields: Map<string, Field>): string {
  const name = fields.get(f.field)?.label || f.field;
  const fmt = (v: unknown) => (v === null ? "(empty)" : String(v));
  switch (f.kind) {
    case "values": {
      const vals = f.values ?? [];
      const shown = vals.slice(0, 2).map(fmt).join(", ") + (vals.length > 2 ? ` +${vals.length - 2}` : "");
      return `${name}: ${f.exclude ? "not " : ""}${vals.length ? shown : "none"}`;
    }
    case "range": {
      const agg = f.agg ? `${AGG_LABEL[f.agg]} of ` : "";
      if (f.min !== null && f.min !== undefined && f.min !== "" && f.max !== null && f.max !== undefined && f.max !== "") return `${agg}${name}: ${f.min} – ${f.max}`;
      if (f.min !== null && f.min !== undefined && f.min !== "") return `${agg}${name} ≥ ${f.min}`;
      return `${agg}${name} ≤ ${f.max}`;
    }
    case "relative":
      return `${name}: last ${f.n} ${f.unit}${f.n === 1 ? "" : "s"}`;
    case "top":
      return `${f.top === "bottom" ? "Bottom" : "Top"} ${f.n} ${name}`;
    case "text":
      return `${name} ${f.op?.replace("_", " ") ?? "contains"} "${f.text}"`;
  }
}
