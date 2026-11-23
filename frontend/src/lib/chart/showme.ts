import type { Field } from "../types";
import { dimensionsOf, measuresOf } from "./query";
import type { ChartConfig, ChartType } from "./types";

export interface ChartKind {
  type: ChartType;
  label: string;
  group: string;
  needs: string;
  ok: (c: Counts) => boolean;
}

interface Counts {
  dims: number;
  measures: number;
  dateDims: number; // dates, at any level
  exactDates: number; // dates without a level (Gantt start/end)
  measuresOnBothAxes: boolean;
  colorDim: boolean;
}

export const CHART_GROUPS = ["Tables", "Bars", "Lines", "Dates & times", "Gauges", "Circles & dots", "Grids", "Maps", "Pipelines"];

export const CHART_KINDS: ChartKind[] = [
  { type: "table", group: "Tables", label: "Table", needs: "Any fields (a dimension on Columns makes a pivot / matrix)", ok: (c) => c.dims + c.measures > 0 },
  { type: "kpi", group: "Tables", label: "Number", needs: "1 or more measures", ok: (c) => c.measures > 0 },
  { type: "rating", group: "Tables", label: "Rating", needs: "1 measure (stars out of the maximum in Options)", ok: (c) => c.measures > 0 },

  { type: "bar", group: "Bars", label: "Bars", needs: "1+ dimensions, 1+ measures (measures on Columns: horizontal)", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "stacked", group: "Bars", label: "Stacked", needs: "2 dimensions (or 1 + Color), 1 measure", ok: (c) => c.dims > 1 && c.measures > 0 },
  { type: "pyramid", group: "Bars", label: "Pyramid", needs: "Categories on Rows; 2 groups on Color or 2 measures", ok: (c) => c.dims > 0 && (c.measures > 1 || (c.colorDim && c.measures > 0)) },
  { type: "stacked_pyramid", group: "Bars", label: "Stacked pyramid", needs: "Categories on Rows, a dimension on Color, 2 measures", ok: (c) => c.dims > 1 && c.measures > 1 && c.colorDim },

  { type: "line", group: "Lines", label: "Line", needs: "1 date or dimension, 1+ measures", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "area", group: "Lines", label: "Area", needs: "1 date or dimension, 1+ measures", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "combo", group: "Lines", label: "Bars + line", needs: "1 dimension, 2 measures on one scale", ok: (c) => c.dims > 0 && c.measures > 1 },
  { type: "parallel", group: "Lines", label: "Parallel", needs: "2+ measures (one axis each), items on Detail", ok: (c) => c.measures > 1 },
  { type: "radar", group: "Lines", label: "Radar", needs: "3+ measures (spokes), items on Color", ok: (c) => c.measures > 2 },

  { type: "timeline", group: "Dates & times", label: "Timeline", needs: "A date and 1+ measures; zoom with the slider", ok: (c) => c.dateDims > 0 && c.measures > 0 },
  { type: "time_bar", group: "Dates & times", label: "Time bars", needs: "A date and a measure", ok: (c) => c.dateDims > 0 && c.measures > 0 },
  { type: "time_combo", group: "Dates & times", label: "Time combo", needs: "A date and 2 measures (bars + line)", ok: (c) => c.dateDims > 0 && c.measures > 1 },
  { type: "calendar", group: "Dates & times", label: "Calendar", needs: "A date by Day and a measure", ok: (c) => c.dateDims > 0 && c.measures > 0 },
  { type: "gantt", group: "Dates & times", label: "Gantt", needs: "Task on Rows; start and end dates (exact) on Columns", ok: (c) => c.exactDates > 1 && c.dims > 2 },

  { type: "gauge", group: "Gauges", label: "Gauge", needs: "1 measure (+ a target measure)", ok: (c) => c.measures > 0 },
  { type: "polar_gauge", group: "Gauges", label: "Ring", needs: "1 measure (+ a target measure)", ok: (c) => c.measures > 0 },
  { type: "flat_gauge", group: "Gauges", label: "Progress bar", needs: "1 measure (+ a target measure)", ok: (c) => c.measures > 0 },
  { type: "bullet", group: "Gauges", label: "Bullet", needs: "Actual and target measures (+ items on Rows)", ok: (c) => c.measures > 0 },

  { type: "donut", group: "Circles & dots", label: "Donut", needs: "1 dimension, 1 measure (few parts)", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "scatter", group: "Circles & dots", label: "Scatter", needs: "2 measures (+ detail)", ok: (c) => c.measures > 1 },
  { type: "dot_h", group: "Circles & dots", label: "Dots across", needs: "Items on Rows, 1+ measures", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "dot_v", group: "Circles & dots", label: "Dots up", needs: "Items on Columns, 1+ measures", ok: (c) => c.dims > 0 && c.measures > 0 },

  { type: "heatmap", group: "Grids", label: "Heat map", needs: "2 dimensions, 1 measure", ok: (c) => c.dims > 1 && c.measures > 0 },
  { type: "treemap", group: "Grids", label: "Treemap", needs: "1+ dimensions, 1 measure", ok: (c) => c.dims > 0 && c.measures > 0 },

  { type: "map", group: "Maps", label: "World map", needs: "1 country, emirate or city, 1 measure", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "geomap", group: "Maps", label: "Geo map", needs: "Area names + a measure; choose the map in Options", ok: (c) => c.dims > 0 && c.measures > 0 },

  { type: "waterfall", group: "Pipelines", label: "Waterfall", needs: "Steps (a dimension) and a measure", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "stacked_waterfall", group: "Pipelines", label: "Stacked waterfall", needs: "Steps, a dimension on Color, a measure", ok: (c) => c.dims > 1 && c.measures > 0 && c.colorDim },
  { type: "funnel", group: "Pipelines", label: "Funnel", needs: "Stages (a dimension) and a measure", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "origami", group: "Pipelines", label: "Origami", needs: "A dimension on Color and a measure on Size", ok: (c) => c.dims > 0 && c.measures > 0 },
  { type: "sankey", group: "Pipelines", label: "Sankey", needs: "2+ dimensions (from → to) and a measure", ok: (c) => c.dims > 1 && c.measures > 0 },
];

function counts(cfg: ChartConfig, fields: Field[]): Counts {
  const dims = dimensionsOf(cfg);
  const byName = new Map(fields.map((f) => [f.name, f]));
  const isDate = (name: string) => ["date", "datetime"].includes(byName.get(name)?.data_type ?? "");
  return {
    dims: dims.length,
    measures: measuresOf(cfg).length,
    dateDims: dims.filter((p) => isDate(p.field) && !p.part).length,
    exactDates: dims.filter((p) => isDate(p.field) && !p.part).length,
    measuresOnBothAxes:
      cfg.shelves.columns.some((p) => p.role === "measure") && cfg.shelves.rows.some((p) => p.role === "measure"),
    colorDim: cfg.shelves.color.some((p) => p.role === "dimension"),
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
