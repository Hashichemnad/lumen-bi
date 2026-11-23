/**
 * Query result → ECharts option, for every chart type except table and number (drawn in React).
 *
 * House rules (docs/CHARTS.md): one value axis (bars + line share it); categorical colours in a fixed
 * order, following the value (not its rank) and folding past the cap into "Other"; thin bars (≤ 24px) with
 * 4px rounded ends; 2px lines; ≥ 8px markers with a white ring; area wash at 10%; hairline solid grid;
 * a legend only for two or more series; values in text colours, never the series colour; every mark has
 * a tooltip, and every value is also reachable in the table view.
 */
import type { EChartsCoreOption } from "echarts/core";

import type { QueryColumn, QueryResult } from "../types";
import { compactNumber, escapeHtml, formatValue } from "./format";
import { countryCenters, countryIndex, countryName, loadWorld, place, regionAround } from "./geo";
import { ALL_PAIRS_CAP, CATEGORICAL, FONT, INK, OTHER, OTHER_LABEL, colorMap, inkOn, sequential, SEQUENTIAL } from "./palette";
import { buildMore, MORE_TYPES } from "./build2";
import type { ChartConfig, ChartType, Pill } from "./types";
import { t } from "../i18n";

export interface Built {
  option: EChartsCoreOption;
  maps?: Record<string, object>;
  notes: string[];
}

export type Row = unknown[];
export type Obj = Record<string, unknown>;

// ------------------------------------------------------------------ layout: which pill plays which part
export interface Layout {
  horizontal: boolean;
  cats: Pill[];
  values: Pill[];
  colorDim?: Pill;
  colorMeasure?: Pill;
  size?: Pill;
  detail: Pill[];
  tooltip: Pill[];
  label: Pill[];
}

export function layout(cfg: ChartConfig): Layout {
  const s = cfg.shelves;
  const dim = (p: Pill) => p.role === "dimension";
  const mea = (p: Pill) => p.role === "measure";
  const measCols = s.columns.filter(mea);
  const measRows = s.rows.filter(mea);
  const horizontal = measCols.length > 0 && measRows.length === 0;
  let values = horizontal ? measCols : measRows;
  if (!values.length) values = [...s.size, ...s.label, ...s.tooltip, ...s.detail, ...s.color].filter(mea).slice(0, 1);
  let cats = (horizontal ? s.rows : s.columns).filter(dim);
  if (!cats.length) cats = (horizontal ? s.columns : s.rows).filter(dim);
  return {
    horizontal,
    cats,
    values,
    colorDim: s.color.find(dim),
    colorMeasure: s.color.find(mea),
    size: s.size.find(mea),
    detail: s.detail.filter(dim).filter((p) => !cats.includes(p)),
    tooltip: s.tooltip,
    label: s.label,
  };
}

/** The dimension whose values get categorical colours (so the caller can fetch its full value list). */
export function identityPill(cfg: ChartConfig, type: ChartType): Pill | undefined {
  const L = layout(cfg);
  if (L.colorDim) return L.colorDim;
  const dims = [...L.cats, ...L.detail];
  if (["donut", "treemap", "origami", "radar", "sankey"].includes(type)) return dims[0];
  if (type === "stacked" && L.cats.length > 1) return L.cats[L.cats.length - 1];
  return undefined;
}

// ------------------------------------------------------------------ shared pieces
export class Result {
  index: Map<string, number>;
  constructor(public r: QueryResult) {
    this.index = new Map(r.columns.map((c, i) => [c.key, i]));
  }
  col(p: Pill): QueryColumn | undefined {
    const i = this.index.get(p.id);
    return i === undefined ? undefined : this.r.columns[i];
  }
  get(row: Row, p: Pill): unknown {
    const i = this.index.get(p.id);
    return i === undefined ? undefined : row[i];
  }
  text(row: Row, p: Pill): string {
    const v = this.get(row, p);
    return v === null || v === undefined ? "(empty)" : formatValue(v, this.col(p));
  }
  num(row: Row, p: Pill): number | null {
    const v = this.get(row, p);
    return typeof v === "number" && Number.isFinite(v) ? v : null;
  }
  label(p: Pill): string {
    return this.col(p)?.label ?? p.field;
  }
}

export function weightsOf(R: Result, rows: Row[], dim: Pill, m?: Pill): Map<string, number> {
  const w = new Map<string, number>();
  for (const r of rows) {
    const k = String(R.get(r, dim));
    w.set(k, (w.get(k) ?? 0) + Math.abs((m ? R.num(r, m) : 1) ?? 0));
  }
  return w;
}

export const additive = (col?: QueryColumn) => !!col && !col.calc && (col.agg === "sum" || col.agg === "count");

export function tooltipBox(title: string, lines: { color?: string; value: string; name: string }[], extra: string[] = []) {
  const items = lines
    .map(
      (l) =>
        `<div style="display:flex;align-items:center;gap:8px;margin-top:4px">` +
        (l.color ? `<span style="display:inline-block;width:12px;height:2px;background:${l.color}"></span>` : "") +
        `<b style="color:${INK.primary}">${escapeHtml(l.value)}</b>` +
        `<span style="color:${INK.secondary}">${escapeHtml(l.name)}</span></div>`,
    )
    .join("");
  const more = extra.map((e) => `<div style="color:${INK.secondary};margin-top:2px">${escapeHtml(e)}</div>`).join("");
  return `<div style="font-family:${FONT};font-size:12px">${title ? `<div style="color:${INK.secondary}">${escapeHtml(title)}</div>` : ""}${items}${more}</div>`;
}

export function extraLines(R: Result, row: Row | undefined, pills: Pill[]): string[] {
  if (!row) return [];
  return pills.map((p) => `${R.label(p)}: ${R.text(row, p)}`);
}

export const TOOLTIP_BASE = {
  get backgroundColor() {
    return INK.surface;
  },
  get borderColor() {
    return INK.tooltipBorder;
  },
  borderWidth: 1,
  padding: [8, 10],
  extraCssText: "box-shadow:0 4px 16px rgba(0,0,0,0.08);border-radius:8px;",
  get textStyle() {
    return { fontFamily: FONT, color: INK.primary };
  },
};

export function valueAxis(fmtCol: QueryColumn | undefined, percent = false): Obj {
  return {
    type: "value",
    axisLabel: {
      color: INK.muted,
      fontFamily: FONT,
      formatter: (v: number) => (percent ? `${Math.round(v * 100)}%` : fmtCol?.format.type === "percent" ? formatValue(v, fmtCol) : compactNumber(v)),
    },
    splitLine: { lineStyle: { color: INK.grid, width: 1, type: "solid" } },
    axisLine: { show: false },
    axisTick: { show: false },
    max: percent ? 1 : undefined,
  };
}

export function categoryAxis(data: string[]): Obj {
  return {
    type: "category",
    data,
    axisLabel: { color: INK.muted, fontFamily: FONT, hideOverlap: true, interval: data.length <= 40 ? 0 : "auto", overflow: "break", width: data.length > 8 ? 70 : 100, lineHeight: 14 },
    axisLine: { lineStyle: { color: INK.axis } },
    axisTick: { show: false },
  };
}

export function legend(names: { name: string; icon: string }[], show: boolean): Obj {
  const lines = names.length > 0 && names.every((n) => n.icon === LINE_ICON);
  return {
    show: show && names.length > 1,
    top: 0,
    left: 0,
    type: "scroll",
    itemWidth: lines ? 16 : 14,
    itemHeight: lines ? 3 : 8,
    textStyle: { color: INK.secondary, fontFamily: FONT },
    itemStyle: { borderWidth: 0 },
    data: names,
  };
}

export const LINE_ICON = "path://M0,0 L24,0 L24,3 L0,3 Z";

function refValue(values: number[], kind: string, constant?: number): number | null {
  const v = values.filter(Number.isFinite);
  if (kind === "constant") return constant ?? null;
  if (!v.length) return null;
  if (kind === "avg") return v.reduce((a, b) => a + b, 0) / v.length;
  if (kind === "min") return Math.min(...v);
  if (kind === "max") return Math.max(...v);
  const s = [...v].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

export function regression(points: [number, number][]): [number, number] | null {
  if (points.length < 3) return null;
  const n = points.length;
  const sx = points.reduce((a, p) => a + p[0], 0), sy = points.reduce((a, p) => a + p[1], 0);
  const sxx = points.reduce((a, p) => a + p[0] * p[0], 0), sxy = points.reduce((a, p) => a + p[0] * p[1], 0);
  const d = n * sxx - sx * sx;
  if (!d) return null;
  const b = (n * sxy - sx * sy) / d;
  return [(sy - b * sx) / n, b];
}

const REF_NAMES: Record<string, string> = { avg: "Average", median: "Median", min: "Minimum", max: "Maximum", constant: "" };

// ------------------------------------------------------------------ bars, lines, areas, stacked, combo
const BAND = "\u200bband:"; // hidden helper series of forecast ranges (never shown in tooltips or legends)

function cartesian(type: ChartType, cfg: ChartConfig, R: Result, identity: unknown[]): Built {
  const L = layout(cfg);
  const notes: string[] = [];
  const barLike = type === "bar" || type === "stacked" || type === "combo";
  const horizontal = barLike && L.horizontal;
  let cats = L.cats;
  let seriesDim: Pill | undefined = L.colorDim && !cats.includes(L.colorDim) ? L.colorDim : undefined;
  if (type === "stacked" && !seriesDim && cats.length > 1) {
    seriesDim = cats[cats.length - 1];
    cats = cats.slice(0, -1);
  }
  // Extra dimensions on Detail split marks into segments (bars) or separate lines, in one colour
  const segmentDim = !seriesDim ? L.detail[0] : undefined;
  if (type === "combo" && seriesDim) {
    notes.push(t("Bars + line ignores the Color shelf"));
    seriesDim = undefined;
  }
  const rows = R.r.rows;
  const catLabel = (row: Row) => (cats.length ? cats.map((p) => R.text(row, p)).join(" · ") : "All");
  const categories: string[] = [];
  const catIndex = new Map<string, number>();
  const stacked = type === "stacked" || (barLike && !!segmentDim) || (type === "area" && !!seriesDim);
  // Forecast periods (line and area over a date): extra categories after the real ones
  const fc = (type === "line" || type === "area") && !stacked && !segmentDim ? R.r.forecast : undefined;
  const fcRows = fc && !fc.error ? fc.rows : [];
  if (fc?.error) notes.push(t(fc.error));
  for (const row of [...rows, ...fcRows]) {
    const k = catLabel(row);
    if (!catIndex.has(k)) {
      catIndex.set(k, categories.length);
      categories.push(k);
    }
  }
  const measures = L.values;
  const series: Obj[] = [];
  const fcSeries: Obj[] = [];
  const fcMeasure = new Map<number, Pill>();
  const legendItems: { name: string; icon: string }[] = [];
  const percent = type === "stacked" && !!cfg.options.percentStack;
  const labelsAll = cfg.options.labels === "all";
  const labelsEnds = cfg.options.labels === "ends";

  const make = (name: string, color: string, m: Pill, kind: "bar" | "line", data: Obj[], stackId?: string) => {
    const s: Obj = { name, type: kind, data, itemStyle: { color }, emphasis: { focus: "none" } };
    if (kind === "bar") {
      s.barMaxWidth = 24;
      s.barGap = "10%";
      s.itemStyle = {
        color,
        borderRadius: stackId ? 0 : horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
        ...(stackId ? { borderColor: INK.surface, borderWidth: 1 } : {}),
      };
      if (stackId) s.stack = stackId;
      if (labelsAll && !stackId) {
        s.label = {
          show: true,
          position: horizontal ? "right" : "top",
          color: INK.secondary,
          fontFamily: FONT,
          formatter: (p: { value: number | null }) => (p.value === null ? "" : formatValue(p.value, R.col(m), true)),
        };
      }
    } else {
      s.lineStyle = { width: 2, color, cap: "round", join: "round" };
      s.symbol = "circle";
      s.symbolSize = 8;
      s.showSymbol = data.length <= 40;
      s.itemStyle = { color, borderColor: INK.surface, borderWidth: 2 };
      s.smooth = !!cfg.options.smooth;
      s.connectNulls = false;
      if (type === "area") {
        s.areaStyle = { color, opacity: 0.1 };
        if (stackId) s.stack = stackId;
      }
      if (labelsEnds || labelsAll) {
        s.endLabel = {
          show: true,
          color: INK.secondary,
          fontFamily: FONT,
          formatter: (p: { value: number | null }) => (p.value === null ? "" : formatValue(p.value, R.col(m), true)),
        };
      }
    }
    series.push(s);
    legendItems.push(kind === "bar" || type === "area" ? { name, icon: "roundRect" } : { name, icon: LINE_ICON });
  };

  /** A dashed forecast line from the last real point, over a shaded band of the likely range. */
  const addForecast = (name: string, color: string, m: Pill, match: (row: Row) => boolean, base: Obj[]) => {
    const idx = R.index.get(m.id);
    if (!fc || !fcRows.length || idx === undefined) return;
    const data: Obj[] = categories.map(() => ({ value: null, row: -1 }));
    const lo: (number | null)[] = categories.map(() => null);
    const hi: (number | null)[] = categories.map(() => null);
    let any = false;
    fcRows.forEach((row, i) => {
      const v = row[idx];
      const ci = catIndex.get(catLabel(row));
      if (!match(row) || typeof v !== "number" || ci === undefined) return;
      data[ci] = { value: v, row: -1 };
      lo[ci] = fc.lower[i]?.[idx] as number;
      hi[ci] = fc.upper[i]?.[idx] as number;
      any = true;
    });
    if (!any) return;
    let last = -1;
    base.forEach((d, i) => d.value !== null && d.value !== undefined && (last = i));
    if (last >= 0 && data[last].value === null) {
      data[last] = { value: base[last].value, row: -1 };
      lo[last] = hi[last] = base[last].value as number;
    }
    const stack = `${BAND}${fcSeries.length}`;
    // stackStrategy "all": the band is stacked on the lower bound even when that bound is below zero
    const quiet = { symbol: "none", silent: true, lineStyle: { opacity: 0 }, tooltip: { show: false }, z: 1, smooth: !!cfg.options.smooth, stackStrategy: "all" };
    fcSeries.push({ ...quiet, name: `${BAND}lo${name}`, type: "line", stack, data: lo.map((v) => ({ value: v })) });
    fcSeries.push({
      ...quiet, name: `${BAND}hi${name}`, type: "line", stack, areaStyle: { color, opacity: 0.14 },
      data: hi.map((v, i) => ({ value: v === null || lo[i] === null ? null : v - (lo[i] as number) })),
    });
    fcSeries.push({
      name: t("{v0} (forecast)", { v0: name }), type: "line", data, z: 3, smooth: !!cfg.options.smooth,
      lineStyle: { width: 2, color, type: "dashed" }, itemStyle: { color }, symbol: "circle", symbolSize: 6, showSymbol: false,
    });
    fcMeasure.set(fcSeries.length - 1, m);
  };

  const splitDim = seriesDim ?? segmentDim;
  if (splitDim) {
    const present = rows.map((r) => R.get(r, splitDim));
    const cm = colorMap(seriesDim ? identity : [], present, CATEGORICAL.length, weightsOf(R, rows, splitDim, measures[0]));
    const keys = [...cm.kept, ...(cm.hasOther ? [OTHER_LABEL] : [])];
    let dropped = 0;
    measures.forEach((m, mi) => {
      const col = R.col(m);
      const canFold = additive(col);
      for (const key of keys) {
        const data: Obj[] = categories.map(() => ({ value: null, row: -1 }));
        for (let ri = 0; ri < rows.length; ri++) {
          const row = rows[ri];
          const raw = R.get(row, splitDim);
          const folded = cm.folded(raw);
          if ((folded ? OTHER_LABEL : String(raw)) !== key) continue;
          const ci = catIndex.get(catLabel(row))!;
          const v = R.num(row, m);
          if (folded) {
            if (!canFold) {
              dropped++;
              continue;
            }
            data[ci] = { value: ((data[ci].value as number | null) ?? 0) + (v ?? 0), row: -1 };
          } else data[ci] = { value: v, row: ri };
        }
        const label = key === OTHER_LABEL ? OTHER_LABEL : formatValue(key === "null" ? null : rows.find((r) => String(R.get(r, splitDim)) === key)?.[R.index.get(splitDim.id)!], R.col(splitDim)) || "(empty)";
        const name = measures.length > 1 ? `${label} · ${R.label(m)}` : label;
        const color = seriesDim ? (key === OTHER_LABEL ? OTHER : cm.color(key)) : CATEGORICAL[mi % CATEGORICAL.length];
        make(name, color, m, barLike ? "bar" : "line", data, stacked ? `s${mi}` : undefined);
        addForecast(name, color, m, (row) => (cm.folded(R.get(row, splitDim)) ? OTHER_LABEL : String(R.get(row, splitDim))) === key, data);
      }
    });
    if (dropped) notes.push(`Some ${R.label(splitDim)} values are not shown: averages and similar totals cannot be combined into "Other". Filter to see them.`);
    else if (cm.hasOther && seriesDim) notes.push(`Smaller ${R.label(splitDim)} values are combined into "Other".`);
    if (segmentDim) legendItems.length = 0; // segments share one colour: no legend of values
  } else {
    if (measures.length > CATEGORICAL.length) notes.push(`Only the first ${CATEGORICAL.length} measures are shown.`);
    measures.slice(0, CATEGORICAL.length).forEach((m, mi) => {
      const data: Obj[] = categories.map(() => ({ value: null, row: -1 }));
      rows.forEach((row, ri) => {
        data[catIndex.get(catLabel(row))!] = { value: R.num(row, m), row: ri };
      });
      const kind = type === "combo" ? (mi === 0 ? "bar" : "line") : barLike ? "bar" : "line";
      make(R.label(m), CATEGORICAL[mi], m, kind, data);
      addForecast(R.label(m), CATEGORICAL[mi], m, () => true, data);
    });
  }

  if (percent) {
    const totals = categories.map((_, ci) => series.reduce((a, s) => a + Math.abs(Number(((s.data as Obj[])[ci].value as number) ?? 0)), 0));
    for (const s of series) for (const [ci, d] of (s.data as Obj[]).entries()) if (d.value !== null) d.value = totals[ci] ? (d.value as number) / totals[ci] : null;
  }
  // Radius only on the outer end of a stack
  if (stacked && barLike && series.length) {
    const last = series[series.length - 1];
    last.itemStyle = { ...(last.itemStyle as Obj), borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] };
  }

  const first = series[0];
  const firstMeasure = measures[0];
  const fmtCol = firstMeasure ? R.col(firstMeasure) : undefined;
  const ref = cfg.options.refLine;
  if (first && ref) {
    const vals = (first.data as Obj[]).map((d) => d.value as number).filter((v) => typeof v === "number");
    const rv = refValue(vals, ref.kind, ref.value);
    if (rv !== null) {
      first.markLine = {
        symbol: "none",
        silent: true,
        lineStyle: { color: INK.secondary, width: 1, type: "dashed" },
        label: { color: INK.secondary, fontFamily: FONT, position: horizontal ? "insideEndTop" : "insideEndTop", formatter: `${ref.label || REF_NAMES[ref.kind]} ${formatValue(rv, fmtCol, true)}`.trim() },
        data: [horizontal ? { xAxis: rv } : { yAxis: rv }],
      };
    }
  }
  if (cfg.options.trend && first && !splitDim) {
    const pts = (first.data as Obj[]).map((d, i) => [i, d.value as number] as [number, number]).filter((p) => typeof p[1] === "number");
    const fit = regression(pts);
    if (fit) {
      series.push({
        name: "Trend",
        type: "line",
        data: categories.map((_, i) => ({ value: fit[0] + fit[1] * i, row: -1 })),
        symbol: "none",
        silent: true,
        lineStyle: { color: INK.muted, width: 1.5 },
        itemStyle: { color: INK.muted },
        tooltip: { show: false },
        z: 1,
      });
    }
  }

  const baseCount = series.length;
  if (fcSeries.length) {
    series.push(...fcSeries);
    const pct = Math.round((fc?.interval ?? 0.95) * 100);
    notes.push(t("Dashed line: forecast. The shaded band is where values will likely fall ({v0}% of the time).", { v0: pct }));
  }
  const extra = [...L.tooltip, ...L.label.filter((p) => !measures.includes(p))];
  const axisTrigger = !barLike || type === "combo";
  const tooltip: Obj = {
    ...TOOLTIP_BASE,
    trigger: axisTrigger ? "axis" : "item",
    axisPointer: axisTrigger ? { type: "line", lineStyle: { color: INK.axis, width: 1 } } : undefined,
    formatter: (params: unknown) => {
      const list = (Array.isArray(params) ? params : [params]) as { seriesName: string; color: string; value: number | null; data: Obj; name: string; seriesIndex: number }[];
      const shown = list.filter((p) => p.seriesName !== "Trend" && !p.seriesName.startsWith(BAND) && p.value !== null && p.value !== undefined);
      if (!shown.length) return "";
      const row = shown.length === 1 && typeof shown[0].data?.row === "number" && (shown[0].data.row as number) >= 0 ? R.r.rows[shown[0].data.row as number] : undefined;
      const segment = segmentDim && row ? ` · ${R.text(row, segmentDim)}` : "";
      return tooltipBox(
        shown[0].name + segment,
        shown.map((p) => {
          const m = p.seriesIndex >= baseCount
            ? fcMeasure.get(p.seriesIndex - baseCount)
            : measures[Math.min(measures.length - 1, splitDim ? Math.floor(p.seriesIndex / Math.max(1, baseCount / Math.max(1, measures.length))) : p.seriesIndex)];
          return {
            color: p.color,
            value: percent ? `${((p.value as number) * 100).toFixed(1)}%` : formatValue(p.value, m ? R.col(m) : undefined),
            name: segmentDim ? (m ? R.label(m) : p.seriesName) : p.seriesName,
          };
        }),
        extraLines(R, row, extra),
      );
    },
  };

  const showLegend = cfg.options.legend !== false && legendItems.length > 1;
  const option: EChartsCoreOption = {
    animationDuration: 300,
    textStyle: { fontFamily: FONT },
    grid: { left: 8, right: labelsEnds || labelsAll ? 48 : 16, top: showLegend ? 36 : 12, bottom: 8, containLabel: true },
    legend: legend(legendItems, showLegend),
    tooltip,
    xAxis: horizontal ? valueAxis(fmtCol, percent) : categoryAxis(categories),
    yAxis: horizontal ? { ...categoryAxis(categories), inverse: true } : valueAxis(fmtCol, percent),
    series,
  };
  if (L.colorMeasure && !splitDim && series.length === 1) {
    const vals = (series[0].data as Obj[]).map((d) => d.value as number).filter((v) => typeof v === "number");
    const cvals = rows.map((r) => R.num(r, L.colorMeasure!)).filter((v): v is number => v !== null);
    (series[0].data as Obj[]).forEach((d) => {
      const row = R.r.rows[d.row as number];
      const cv = row ? R.num(row, L.colorMeasure!) : null;
      const [lo, hi] = [Math.min(...cvals), Math.max(...cvals)];
      if (cv !== null) d.itemStyle = { color: sequential(hi > lo ? (cv - lo) / (hi - lo) * 0.85 + 0.15 : 0.6) };
    });
    void vals;
    notes.push(`Colour shows ${R.label(L.colorMeasure)}: darker is higher.`);
  }
  if (categories.length > 60 && barLike) notes.push(`${categories.length} bars: consider a filter or top N.`);
  return { option, notes };
}

// ------------------------------------------------------------------ scatter
function scatter(cfg: ChartConfig, R: Result, identity: unknown[]): Built {
  const s = cfg.shelves;
  const xs = s.columns.filter((p) => p.role === "measure");
  const ys = s.rows.filter((p) => p.role === "measure");
  const all = [...xs, ...ys, ...s.size, ...s.label, ...s.tooltip, ...s.detail, ...s.color].filter((p) => p.role === "measure");
  const x = xs[0] ?? all[0];
  const y = ys[0] ?? all.find((p) => p !== x);
  const notes: string[] = [];
  if (!x || !y) return { option: {}, notes: ["Put one measure on Columns and one on Rows."] };
  const L = layout(cfg);
  const rows = R.r.rows;
  const size = L.size;
  const sizes = size ? rows.map((r) => R.num(r, size) ?? 0) : [];
  const maxSize = Math.max(1, ...sizes.map(Math.abs));
  const sym = (row: Row) => (size ? 8 + 24 * Math.sqrt(Math.abs(R.num(row, size) ?? 0) / maxSize) : 10);
  const dims = [...s.columns, ...s.rows, ...s.detail, ...s.label, ...s.tooltip].filter((p) => p.role === "dimension");
  const groups = new Map<string, { color: string; name: string; data: Obj[] }>();
  const cm = L.colorDim ? colorMap(identity, rows.map((r) => R.get(r, L.colorDim!)), ALL_PAIRS_CAP + 1, weightsOf(R, rows, L.colorDim, size ?? y)) : null;
  rows.forEach((row, ri) => {
    const xv = R.num(row, x), yv = R.num(row, y);
    if (xv === null || yv === null) return;
    const raw = L.colorDim ? R.get(row, L.colorDim) : null;
    const key = cm ? (cm.folded(raw) ? OTHER_LABEL : String(raw)) : "all";
    if (!groups.has(key)) {
      groups.set(key, {
        color: cm ? (key === OTHER_LABEL ? OTHER : cm.color(raw)) : CATEGORICAL[0],
        name: cm ? (key === OTHER_LABEL ? OTHER_LABEL : R.text(row, L.colorDim!)) : `${R.label(y)} by ${R.label(x)}`,
        data: [],
      });
    }
    groups.get(key)!.data.push({ value: [xv, yv], row: ri, symbolSize: sym(row) });
  });
  if (cm?.hasOther) notes.push(`Scatter charts show at most ${ALL_PAIRS_CAP} colours; the rest are grey "Other".`);
  const ordered = [...groups.entries()].sort(([a], [b]) => Number(a === OTHER_LABEL) - Number(b === OTHER_LABEL)).map(([, g]) => g);
  const series: Obj[] = ordered.map((g) => ({
    name: g.name,
    type: "scatter",
    data: g.data,
    itemStyle: { color: g.color, borderColor: INK.surface, borderWidth: 2, opacity: 0.9 },
    emphasis: { scale: 1.2 },
  }));
  if (cfg.options.trend) {
    const pts = rows.map((r) => [R.num(r, x), R.num(r, y)]).filter((p): p is [number, number] => p[0] !== null && p[1] !== null);
    const fit = regression(pts);
    if (fit) {
      const lo = Math.min(...pts.map((p) => p[0])), hi = Math.max(...pts.map((p) => p[0]));
      series.push({ name: "Trend", type: "line", data: [[lo, fit[0] + fit[1] * lo], [hi, fit[0] + fit[1] * hi]], symbol: "none", silent: true, lineStyle: { color: INK.muted, width: 1.5 }, tooltip: { show: false } });
    }
  }
  const showLegend = cfg.options.legend !== false && groups.size > 1;
  return {
    notes,
    option: {
      textStyle: { fontFamily: FONT },
      grid: { left: 8, right: 16, top: showLegend ? 60 : 32, bottom: 24, containLabel: true },
      legend: legend(ordered.map((g) => ({ name: g.name, icon: "circle" })), showLegend),
      tooltip: {
        ...TOOLTIP_BASE,
        trigger: "item",
        formatter: (p: { data: Obj; color: string }) => {
          const row = rows[p.data.row as number];
          if (!row) return "";
          const title = dims.map((d) => R.text(row, d)).join(" · ");
          return tooltipBox(title, [
            { color: p.color, value: formatValue(R.get(row, x), R.col(x)), name: R.label(x) },
            { value: formatValue(R.get(row, y), R.col(y)), name: R.label(y) },
            ...(size ? [{ value: formatValue(R.get(row, size), R.col(size)), name: R.label(size) }] : []),
          ], extraLines(R, row, L.tooltip.filter((t) => t.role === "measure" && t !== x && t !== y)));
        },
      },
      xAxis: { ...valueAxis(R.col(x)), name: R.label(x), nameLocation: "middle", nameGap: 28, nameTextStyle: { color: INK.secondary }, scale: true },
      yAxis: { ...valueAxis(R.col(y)), name: R.label(y), nameTextStyle: { color: INK.secondary, align: "left" }, scale: true },
      series,
    },
  };
}

// ------------------------------------------------------------------ heat map
function heatmap(cfg: ChartConfig, R: Result): Built {
  const L = layout(cfg);
  const dims = [...cfg.shelves.columns, ...cfg.shelves.rows, ...cfg.shelves.detail].filter((p) => p.role === "dimension");
  const xd = cfg.shelves.columns.find((p) => p.role === "dimension") ?? dims[0];
  const yd = cfg.shelves.rows.find((p) => p.role === "dimension") ?? dims.find((p) => p !== xd);
  const m = L.colorMeasure ?? L.values[0] ?? [...cfg.shelves.size, ...cfg.shelves.label].find((p) => p.role === "measure");
  if (!xd || !yd || !m) return { option: {}, notes: ["Put a dimension on Columns, one on Rows and a measure on Color."] };
  const rows = R.r.rows;
  const xsv: string[] = [], ysv: string[] = [];
  const xi = new Map<string, number>(), yi = new Map<string, number>();
  for (const r of rows) {
    const a = R.text(r, xd), b = R.text(r, yd);
    if (!xi.has(a)) { xi.set(a, xsv.length); xsv.push(a); }
    if (!yi.has(b)) { yi.set(b, ysv.length); ysv.push(b); }
  }
  const data = rows.map((r, ri) => ({ value: [xi.get(R.text(r, xd)), yi.get(R.text(r, yd)), R.num(r, m)], row: ri }));
  const vals = data.map((d) => d.value[2] as number | null).filter((v): v is number => v !== null);
  const col = R.col(m);
  return {
    notes: [],
    option: {
      textStyle: { fontFamily: FONT },
      grid: { left: 8, right: 16, top: 12, bottom: 48, containLabel: true },
      tooltip: {
        ...TOOLTIP_BASE,
        formatter: (p: { data: { row: number } }) => {
          const r = rows[p.data.row];
          return tooltipBox(`${R.text(r, xd)} · ${R.text(r, yd)}`, [{ value: formatValue(R.get(r, m), col), name: R.label(m) }], extraLines(R, r, L.tooltip));
        },
      },
      xAxis: { ...categoryAxis(xsv), splitArea: { show: false } },
      yAxis: { ...categoryAxis(ysv), inverse: true },
      visualMap: {
        min: vals.length ? Math.min(...vals) : 0,
        max: vals.length ? Math.max(...vals) : 1,
        calculable: false,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        itemHeight: 160,
        itemWidth: 10,
        inRange: { color: SEQUENTIAL },
        text: [formatValue(vals.length ? Math.max(...vals) : 1, col, true), formatValue(vals.length ? Math.min(...vals) : 0, col, true)],
        textStyle: { color: INK.muted },
      },
      series: [{
        type: "heatmap",
        data,
        itemStyle: { borderColor: INK.surface, borderWidth: 2, borderRadius: 2 },
        label: {
          show: cfg.options.labels === "all",
          fontFamily: FONT,
          formatter: (p: { value: [number, number, number | null] }) => (p.value[2] === null ? "" : formatValue(p.value[2], col, true)),
        },
      }],
    },
  };
}

// ------------------------------------------------------------------ treemap and donut
function treemap(cfg: ChartConfig, R: Result, identity: unknown[]): Built {
  const L = layout(cfg);
  const dims = [...L.cats, ...(L.colorDim && !L.cats.includes(L.colorDim) ? [L.colorDim] : []), ...L.detail].slice(0, 2);
  const m = L.size ?? L.values[0];
  if (!dims.length || !m) return { option: {}, notes: ["Add a dimension and a measure (Size)."] };
  const rows = R.r.rows;
  const top = dims[0];
  const cm = colorMap(identity, rows.map((r) => R.get(r, top)), CATEGORICAL.length, weightsOf(R, rows, top, m));
  const nodes = new Map<string, Obj & { children: Obj[]; value: number }>();
  rows.forEach((r, ri) => {
    const v = R.num(r, m);
    if (v === null || v <= 0) return;
    const raw = R.get(r, top);
    const key = cm.folded(raw) ? OTHER_LABEL : String(raw);
    const color = cm.folded(raw) ? OTHER : cm.color(raw);
    if (!nodes.has(key)) nodes.set(key, { name: key === OTHER_LABEL ? OTHER_LABEL : R.text(r, top), value: 0, children: [], itemStyle: { color }, label: { color: inkOn(color) }, upperLabel: { color: inkOn(color) }, row: -1 });
    const n = nodes.get(key)!;
    n.value += v;
    if (dims[1]) n.children.push({ name: R.text(r, dims[1]), value: v, row: ri, itemStyle: { color }, label: { color: inkOn(color) } });
    else n.row = ri;
  });
  const col = R.col(m);
  return {
    notes: cm.hasOther ? [`Smaller ${R.label(top)} values are combined into "Other".`] : [],
    option: {
      textStyle: { fontFamily: FONT },
      tooltip: { ...TOOLTIP_BASE, formatter: (p: { name: string; value: number; treePathInfo: { name: string }[] }) => tooltipBox(p.treePathInfo.slice(1).map((t) => t.name).join(" · "), [{ value: formatValue(p.value, col), name: R.label(m) }]) },
      series: [{
        type: "treemap",
        data: [...nodes.values()].map((n) => (dims[1] ? n : { ...n, children: undefined })),
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        width: "100%",
        height: "100%",
        top: 0,
        left: 0,
        itemStyle: { borderColor: INK.surface, borderWidth: 2, gapWidth: 2, borderRadius: 4 },
        upperLabel: { show: !!dims[1], height: 22, fontFamily: FONT },
        levels: dims[1] ? [{ itemStyle: { borderWidth: 0, gapWidth: 2 } }, { itemStyle: { gapWidth: 1, borderWidth: 1, borderColor: "rgba(255,255,255,0.6)" }, colorSaturation: [0.55, 0.75] }] : undefined,
        label: {
          show: true,
          fontFamily: FONT,
          formatter: (p: { name: string; value: number }) => `${p.name}\n${formatValue(p.value, col, true)}`,
        },
      }],
    },
  };
}

function donut(cfg: ChartConfig, R: Result, identity: unknown[]): Built {
  const L = layout(cfg);
  const d = L.colorDim ?? L.cats[0] ?? L.detail[0];
  const m = L.size ?? L.values[0] ?? L.colorMeasure;
  if (!d || !m) return { option: {}, notes: ["Add a dimension (Color) and a measure (Size)."] };
  const rows = R.r.rows;
  const col = R.col(m);
  const items = rows.map((r, ri) => ({ raw: R.get(r, d), name: R.text(r, d), value: R.num(r, m) ?? 0, row: ri })).filter((x) => x.value > 0).sort((a, b) => b.value - a.value);
  const keepN = items.length > 6 ? 5 : items.length;
  const kept = items.slice(0, keepN);
  const cm = colorMap(identity, kept.map((k) => k.raw));
  const rest = items.slice(keepN);
  const data: Obj[] = kept.map((k) => ({ name: k.name, value: k.value, row: k.row, itemStyle: { color: cm.color(k.raw) } }));
  const notes: string[] = [];
  if (rest.length) {
    if (additive(col)) data.push({ name: OTHER_LABEL, value: rest.reduce((a, b) => a + b.value, 0), row: -1, itemStyle: { color: OTHER } });
    notes.push(`A donut reads best with few parts: ${rest.length} smaller values ${additive(col) ? 'are combined into "Other"' : "are not shown"}. Bars compare values more exactly.`);
  }
  const total = data.reduce((a, b) => a + (b.value as number), 0);
  const showLegend = cfg.options.legend !== false && data.length > 1;
  return {
    notes,
    option: {
      textStyle: { fontFamily: FONT },
      legend: { ...legend(data.map((x) => ({ name: x.name as string, icon: "roundRect" })), showLegend), orient: "vertical", left: "right", top: "middle", type: "scroll" },
      tooltip: { ...TOOLTIP_BASE, trigger: "item", formatter: (p: { name: string; value: number; percent: number; color: string }) => tooltipBox(p.name, [{ color: p.color, value: formatValue(p.value, col), name: `${p.percent.toFixed(1)}% of total` }]) },
      series: [{
        type: "pie",
        radius: ["52%", "74%"],
        center: [showLegend ? "40%" : "50%", "50%"],
        data,
        itemStyle: { borderColor: INK.surface, borderWidth: 2, borderRadius: 4 },
        label: { show: cfg.options.labels !== "none", color: INK.secondary, fontFamily: FONT, formatter: (p: { name: string; percent: number }) => `${p.name}\n${p.percent.toFixed(0)}%` },
        labelLine: { lineStyle: { color: INK.axis } },
      }],
      graphic: undefined,
      title: {
        text: formatValue(total, col, true),
        subtext: "Total",
        left: showLegend ? "40%" : "50%",
        top: "center",
        textAlign: "center",
        textStyle: { color: INK.primary, fontSize: 20, fontWeight: 600, fontFamily: FONT },
        subtextStyle: { color: INK.muted, fontFamily: FONT },
        show: additive(col),
      },
    },
  };
}

// ------------------------------------------------------------------ maps
async function map(cfg: ChartConfig, R: Result): Promise<Built> {
  const L = layout(cfg);
  const d = [...L.cats, ...(L.colorDim ? [L.colorDim] : []), ...L.detail][0];
  const m = L.colorMeasure ?? L.size ?? L.values[0];
  if (!d || !m) return { option: {}, notes: ["Add a country, emirate or city and a measure."] };
  const world = await loadWorld();
  const { names, lower } = countryIndex(world);
  const rows = R.r.rows;
  const col = R.col(m);
  const countries = rows.map((r) => countryName(R.get(r, d), names, lower));
  const countryHits = countries.filter(Boolean).length;
  const places = rows.map((r) => place(R.get(r, d)));
  const placeHits = places.filter(Boolean).length;
  const notes: string[] = [];
  const unknown = (hits: (unknown | null)[]) => rows.filter((_, i) => !hits[i]).map((r) => R.text(r, d));
  const tooltip = {
    ...TOOLTIP_BASE,
    trigger: "item",
    formatter: (p: { data?: { row: number } }) => {
      const r = p.data ? rows[p.data.row] : undefined;
      return r ? tooltipBox(R.text(r, d), [{ value: formatValue(R.get(r, m), col), name: R.label(m) }], extraLines(R, r, L.tooltip)) : "";
    },
  };
  const geoBase = { roam: true, itemStyle: { areaColor: INK.wash, borderColor: INK.surface, borderWidth: 1 }, emphasis: { itemStyle: { areaColor: INK.wash2 }, label: { show: false } }, label: { show: false }, select: { disabled: true } };

  if (countryHits >= placeHits && countryHits > 0) {
    const matched = countries.filter((c): c is string => !!c);
    const region = matched.length <= 12 ? regionAround(world, countryCenters(world, matched), 6) : { geo: world };
    const name = `region-${[...new Set(matched)].sort().join("|")}`;
    const vals = rows.map((r) => R.num(r, m)).filter((v): v is number => v !== null);
    const miss = unknown(countries);
    if (miss.length) notes.push(`Not on the map: ${miss.slice(0, 5).join(", ")}${miss.length > 5 ? ` and ${miss.length - 5} more` : ""}.`);
    return {
      notes,
      maps: { [name]: region.geo },
      option: {
        textStyle: { fontFamily: FONT },
        tooltip,
        visualMap: {
          min: vals.length ? Math.min(...vals) : 0, max: vals.length ? Math.max(...vals) : 1, orient: "horizontal", left: 0, bottom: 0, itemHeight: 140, itemWidth: 10,
          inRange: { color: SEQUENTIAL }, text: [formatValue(Math.max(...vals, 0), col, true), formatValue(Math.min(...vals, 0), col, true)], textStyle: { color: INK.muted }, calculable: false,
        },
        series: [{
          type: "map", map: name, ...geoBase, nameProperty: "name", boundingCoords: region.bounds,
          data: rows.map((r, ri) => (countries[ri] ? { name: countries[ri], value: R.num(r, m), row: ri } : null)).filter(Boolean),
        }],
      },
    };
  }
  const pts = places.filter((p): p is [number, number, string] => !!p);
  if (!pts.length) return { option: {}, notes: [`None of the ${R.label(d)} values are known places. Maps know countries, the emirates and major Gulf cities.`] };
  const region = regionAround(world, pts.map((p) => [p[0], p[1]]), 1.5);
  const name = `places-${pts.map((p) => p[2]).sort().join("|")}`;
  const vals = rows.map((r) => Math.abs(R.num(r, m) ?? 0));
  const max = Math.max(1, ...vals);
  const miss = unknown(places);
  if (miss.length) notes.push(`Not on the map: ${miss.slice(0, 5).join(", ")}${miss.length > 5 ? ` and ${miss.length - 5} more` : ""}.`);
  notes.push(t("Bubble size shows the value."));
  return {
    notes,
    maps: { [name]: region.geo },
    option: {
      textStyle: { fontFamily: FONT },
      tooltip,
      geo: { map: name, ...geoBase, boundingCoords: region.bounds },
      series: [{
        type: "scatter",
        coordinateSystem: "geo",
        data: rows.map((r, ri) => (places[ri] ? { name: places[ri]![2], value: [places[ri]![0], places[ri]![1], R.num(r, m)], row: ri, symbolSize: 8 + 32 * Math.sqrt(Math.abs(R.num(r, m) ?? 0) / max) } : null)).filter(Boolean),
        itemStyle: { color: CATEGORICAL[0], opacity: 0.85, borderColor: INK.surface, borderWidth: 2 },
        label: { show: cfg.options.labels !== "none", formatter: "{b}", position: "right", color: INK.secondary, fontFamily: FONT },
      }],
    },
  };
}

// ------------------------------------------------------------------ entry
export async function buildChart(type: ChartType, cfg: ChartConfig, result: QueryResult, identity: unknown[]): Promise<Built> {
  const R = new Result(result);
  if (MORE_TYPES.has(type)) return buildMore(type, cfg, R, identity);
  switch (type) {
    case "scatter": return scatter(cfg, R, identity);
    case "heatmap": return heatmap(cfg, R);
    case "treemap": return treemap(cfg, R, identity);
    case "donut": return donut(cfg, R, identity);
    case "map": return map(cfg, R);
    default: return cartesian(type, cfg, R, identity);
  }
}

export { inkOn };
