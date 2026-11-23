import { t } from "../i18n";
import type { ChartConfig, Grain, Pill } from "../chart/types";
import { SHELVES } from "../chart/types";
import type { QueryColumn } from "../types";
import type { DashDataset, DashFilter, DateValue, FilterValue } from "./types";

type Spec = Record<string, unknown>;

export function isActive(f: DashFilter, v: FilterValue): boolean {
  if (v === null || v === undefined || v === "") return false;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "object" && "min" in v) return v.min !== null || v.max !== null;
  if (typeof v === "object" && "mode" in v) return v.mode === "relative" || !!(v.from || v.to);
  return f.kind === "text" ? String(v).trim() !== "" : true;
}

/** The query filters one dashboard filter adds to the charts of a dataset. */
export function filterSpecs(f: DashFilter, v: FilterValue, datasetId: number): Spec[] {
  if (!isActive(f, v)) return [];
  return f.targets
    .filter((t) => t.dataset === datasetId)
    .map((t) => {
      const base = { field: t.field, ...(f.grain ? { grain: f.grain } : {}) };
      if (f.kind === "values") return { ...base, op: "in", values: v as unknown[] };
      if (f.kind === "text") return { ...base, op: "contains", value: String(v) };
      if (f.kind === "range") {
        const r = v as { min: number | null; max: number | null };
        return { ...base, op: "between", values: [r.min, r.max] };
      }
      const d = v as DateValue;
      return d.mode === "relative" ? { ...base, op: "relative", unit: d.unit, n: d.n } : { ...base, op: "between", values: [d.from || null, d.to || null] };
    });
}

const UNIT_NAME: Record<string, [string, string]> = {
  day: ["Today", "Last {n} days"],
  week: ["This week", "Last {n} weeks"],
  month: ["This month", "Last {n} months"],
  quarter: ["This quarter", "Last {n} quarters"],
  year: ["This year", "Last {n} years"],
  fiscal_quarter: ["This fiscal quarter", "Last {n} fiscal quarters"],
  fiscal_year: ["This fiscal year", "Last {n} fiscal years"],
};

/** "This month", "Last 3 months", "This fiscal year"… */
export function relativeLabel(unit: string, n: number): string {
  const [one, many] = UNIT_NAME[unit] ?? ["This " + unit, "Last {n} " + unit + "s"];
  return n === 1 ? t(one) : t(many, { n });
}

export function describeValue(f: DashFilter, v: FilterValue): string {
  if (!isActive(f, v)) return t("All");
  if (Array.isArray(v)) return v.length <= 2 ? v.map((x) => (x === null ? "(empty)" : String(x))).join(", ") : t("{n} selected", { n: v.length });
  if (typeof v === "object" && v && "min" in v) return `${v.min ?? "…"} – ${v.max ?? "…"}`;
  if (typeof v === "object" && v && "mode" in v) {
    if (v.mode === "relative") return relativeLabel(v.unit, v.n);
    return `${v.from ?? "…"} – ${v.to ?? "…"}`;
  }
  return `"${v}"`;
}

// ------------------------------------------------------------------ cross-filtering and drilling
export interface CrossFilter {
  source: string; // tile id
  label: string;
  filters: Spec[]; // field = field name
}

/** Cross filters apply to other tiles whose dataset has the same field names. */
export function crossFor(cross: CrossFilter | null, tileId: string, ds?: DashDataset): Spec[] {
  if (!cross || cross.source === tileId || !ds) return [];
  const names = new Set(ds.fields.map((f) => f.name));
  return cross.filters.filter((f) => names.has(String(f.field)));
}

export interface Drill {
  steps: { key: string; field: string; grain?: Grain }[];
  filters: Spec[];
  path: string[];
}

const DATE_DRILL: Grain[] = ["year", "quarter", "month", "day"];

export function dimensionPills(cfg: ChartConfig): Pill[] {
  return SHELVES.flatMap((s) => cfg.shelves[s]).filter((p) => p.role === "dimension");
}

/** The next level below a dimension: a finer date level, or the next field of a hierarchy. */
export function nextLevel(p: Pill, ds: DashDataset): { field: string; grain?: Grain; label: string } | null {
  const f = ds.fields.find((x) => x.name === p.field);
  if (f && (f.data_type === "date" || f.data_type === "datetime") && p.grain && DATE_DRILL.indexOf(p.grain) >= 0 && p.grain !== "day") {
    const g = DATE_DRILL[DATE_DRILL.indexOf(p.grain) + 1];
    return { field: p.field, grain: g, label: g };
  }
  for (const h of ds.hierarchies) {
    const i = h.levels.indexOf(p.field);
    if (i >= 0 && i < h.levels.length - 1) {
      const next = h.levels[i + 1];
      return { field: next, label: ds.fields.find((x) => x.name === next)?.label ?? next };
    }
  }
  return null;
}

/** The chart's own config with drill steps applied, so labels and formats follow. */
export function drilledConfig(cfg: ChartConfig, drill?: Drill): ChartConfig {
  if (!drill?.steps.length) return cfg;
  const shelves = { ...cfg.shelves };
  for (const s of SHELVES) {
    shelves[s] = cfg.shelves[s].map((p) => {
      const step = [...drill.steps].reverse().find((d) => d.key === p.id);
      return step ? { ...p, field: step.field, grain: step.grain, part: undefined } : p;
    });
  }
  return { ...cfg, shelves };
}

/** Filters that pin one mark (its dimension values) for cross-filtering or drilling. */
export function markSpecs(cfg: ChartConfig, columns: QueryColumn[], row: unknown[]): { specs: Spec[]; label: string } {
  const specs: Spec[] = [];
  const parts: string[] = [];
  const pills = dimensionPills(cfg);
  columns.forEach((c, i) => {
    if (c.role !== "dimension") return;
    const p = pills.find((x) => x.id === c.key);
    if (!p) return;
    const base = { field: p.field, ...(p.grain ? { grain: p.grain } : {}), ...(p.part ? { part: p.part } : {}) };
    specs.push(row[i] === null ? { ...base, op: "is_null" } : { ...base, op: "eq", value: row[i] });
    parts.push(`${c.label}: ${row[i] === null ? "(empty)" : String(row[i])}`);
  });
  return { specs, label: parts.join(", ") };
}

// ------------------------------------------------------------------ links that keep the filters
export interface ViewState {
  f: Record<string, FilterValue>;
  p: Record<string, unknown>;
  t?: string;
}

export function encodeState(s: ViewState): string {
  const json = JSON.stringify(s);
  return btoa(unescape(encodeURIComponent(json))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function decodeState(raw: string | null): ViewState | null {
  if (!raw) return null;
  try {
    const b64 = raw.replace(/-/g, "+").replace(/_/g, "/");
    const parsed = JSON.parse(decodeURIComponent(escape(atob(b64)))) as ViewState;
    return parsed && typeof parsed === "object" ? { f: parsed.f ?? {}, p: parsed.p ?? {}, t: parsed.t } : null;
  } catch {
    return null;
  }
}
