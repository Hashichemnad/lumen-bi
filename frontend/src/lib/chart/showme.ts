import type { Field } from "../types";
import { dimensionsOf, measuresOf } from "./query";
import type { ChartConfig, ChartType } from "./types";

export interface ChartKind {
  type: ChartType;
  label: string;
  needs: string;
  ok: (c: Counts) => boolean;
}

interface Counts {
  dims: number;
  measures: number;
  dateDims: number;
  measuresOnBothAxes: boolean;
  geoDims: number;
}

const GEO = /country|countries|emirate|nation|city|state|region|governorate|location|دولة|إمارة|مدينة/i;

export const CHART_KINDS: ChartKind[] = [
  { type: "table", label: "Table", needs: "Any fields", ok: (c) => c.dims + c.measures > 0 },
  { type: "kpi", label: "Number", needs: "1 or more measures", ok: (c) => c.measures > 0 },
  { type: "bar", label: "Bars", needs: "1+ dimensions, 1+ measures", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "stacked", label: "Stacked bars", needs: "2 dimensions, 1 measure", ok: (c) => c.dims > 1 && c.measures > 0 },
  { type: "line", label: "Line", needs: "1 date or dimension, 1+ measures", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "area", label: "Area", needs: "1 date or dimension, 1+ measures", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "combo", label: "Bars + line", needs: "1 dimension, 2 measures on one scale", ok: (c) => c.dims > 0 && c.measures > 1 },
  { type: "scatter", label: "Scatter", needs: "2 measures (+ detail)", ok: (c) => c.measures > 1 },
  { type: "heatmap", label: "Heat map", needs: "2 dimensions, 1 measure", ok: (c) => c.dims > 1 && c.measures > 0 },
  { type: "treemap", label: "Treemap", needs: "1+ dimensions, 1 measure", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "donut", label: "Donut", needs: "1 dimension, 1 measure (few parts)", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "map", label: "Map", needs: "1 country, emirate or city, 1 measure", ok: (c) => c.dims > 0 && c.measures > 0 },
];

function counts(cfg: ChartConfig, fields: Field[]): Counts {
  const dims = dimensionsOf(cfg);
  const byName = new Map(fields.map((f) => [f.name, f]));
  const isDate = (name: string) => ["date", "datetime"].includes(byName.get(name)?.data_type ?? "");
  return {
    dims: dims.length,
    measures: measuresOf(cfg).length,
    dateDims: dims.filter((p) => isDate(p.field) && !p.part).length,
    measuresOnBothAxes:
      cfg.shelves.columns.some((p) => p.role === "measure") && cfg.shelves.rows.some((p) => p.role === "measure"),
    geoDims: dims.filter((p) => GEO.test(p.field) || GEO.test(byName.get(p.field)?.label ?? "")).length,
  };
}

/** The chart Show Me picks when the type is "auto" (Tableau-like rules). */
export function recommend(cfg: ChartConfig, fields: Field[]): ChartType {
  const c = counts(cfg, fields);
  if (c.measures === 0) return "table";
  if (c.dims === 0) return c.measuresOnBothAxes ? "scatter" : "kpi";
  if (c.measuresOnBothAxes) return "scatter";
  const dimsOnBoth =
    cfg.shelves.columns.some((p) => p.role === "dimension") && cfg.shelves.rows.some((p) => p.role === "dimension");
  if (dimsOnBoth) return "table";
  if (c.dateDims > 0) return "line";
  return "bar";
}

export function available(cfg: ChartConfig, fields: Field[]): Set<ChartType> {
  const c = counts(cfg, fields);
  return new Set(CHART_KINDS.filter((k) => k.ok(c)).map((k) => k.type));
}

export function resolvedType(cfg: ChartConfig, fields: Field[]): ChartType {
  if (cfg.type === "auto") return recommend(cfg, fields);
  return available(cfg, fields).has(cfg.type) ? cfg.type : recommend(cfg, fields);
}
