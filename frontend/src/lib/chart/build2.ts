/**
 * More chart types (milestone 10): pyramids, parallel coordinates, radar, time axis charts, calendar heat
 * map, Gantt, gauges, bullet, dot plots, waterfalls, funnel, Origami, Sankey and Geo Map.
 * Same house rules as build.ts: palette in fixed order folding into "Other", one value axis, thin marks,
 * labels only where they fit, tooltips on every mark, text in text colours.
 */
import type { EChartsCoreOption } from "echarts/core";

import { get } from "../api";
import type { QueryColumn } from "../types";
import {
  additive, categoryAxis, extraLines, layout, legend, LINE_ICON, Result, TOOLTIP_BASE, tooltipBox, valueAxis, weightsOf,
  type Built, type Obj, type Row,
} from "./build";
import { compactNumber, formatValue } from "./format";
import { CATEGORICAL, DIVERGING, FONT, INK, OTHER, OTHER_LABEL, SEQUENTIAL, colorMap, inkOn } from "./palette";
import type { ChartConfig, ChartType, Pill } from "./types";
import { SHELVES } from "./types";

const dims = (cfg: ChartConfig) => SHELVES.flatMap((s) => cfg.shelves[s]).filter((p) => p.role === "dimension");
const measures = (cfg: ChartConfig) => SHELVES.flatMap((s) => cfg.shelves[s]).filter((p) => p.role === "measure");
const isDateCol = (c?: QueryColumn) => !!c && (c.type === "date" || c.type === "datetime");
const empty = (note: string): Built => ({ option: {}, notes: [note] });
const BASE = { animationDuration: 300, textStyle: { fontFamily: FONT } };
const NEUTRAL = ["#f0efec", "#e1e0d9", "#c3c2b7"];

/** Rows in the order the chart should read; values folded into "Other" as one colour. */
function single(R: Result, row: Row | undefined, m: Pill): number | null {
  return row ? R.num(row, m) : null;
}

// ------------------------------------------------------------------ pyramids
function pyramid(cfg: ChartConfig, R: Result, identity: unknown[], stacked: boolean): Built {
  const L = layout(cfg);
  const cat = cfg.shelves.rows.find((p) => p.role === "dimension") ?? L.cats[0];
  const ms = measures(cfg);
  if (!cat) return empty("Put the categories (for example age bands) on Rows.");
  const rows = R.r.rows;
  const cats: string[] = [];
  for (const r of rows) { const k = R.text(r, cat); if (!cats.includes(k)) cats.push(k); }
  const ci = (r: Row) => cats.indexOf(R.text(r, cat));
  const series: Obj[] = [];
  const notes: string[] = [];
  let leftName = "", rightName = "";
  const bar = (name: string, color: string, data: (number | null)[], side: "l" | "r", stack: string, last: boolean) => series.push({
    name, type: "bar", stack, data: data.map((v, i) => ({ value: v === null ? null : side === "l" ? -Math.abs(v) : Math.abs(v), row: -1, ci: i })),
    barMaxWidth: 24, itemStyle: { color, borderRadius: last ? (side === "l" ? [4, 0, 0, 4] : [0, 4, 4, 0]) : 0, ...(stacked ? { borderColor: INK.surface, borderWidth: 1 } : {}) },
  });
  if (!stacked) {
    if (L.colorDim) {
      const present = [...new Set(rows.map((r) => R.get(r, L.colorDim!)))];
      const cm = colorMap(identity, present);
      const sides = cm.kept.slice(0, 2);
      if (present.length !== 2) notes.push(`A pyramid compares two groups; ${R.label(L.colorDim)} has ${present.length}.`);
      const m = ms[0];
      if (!m || sides.length < 2) return empty("Pyramids need two groups on Color (or two measures) and a measure.");
      sides.forEach((sv, i) => {
        const data: (number | null)[] = cats.map(() => null);
        rows.forEach((r) => { if (String(R.get(r, L.colorDim!)) === sv) data[ci(r)] = R.num(r, m); });
        const label = formatValue(rows.find((r) => String(R.get(r, L.colorDim!)) === sv)?.[R.index.get(L.colorDim!.id)!], R.col(L.colorDim!));
        bar(label, cm.color(sv), data, i === 0 ? "l" : "r", i === 0 ? "l" : "r", true);
        if (i === 0) leftName = label; else rightName = label;
      });
    } else {
      if (ms.length < 2) return empty("Pyramids need two measures (one per side) or two groups on Color.");
      ms.slice(0, 2).forEach((m, i) => {
        const data: (number | null)[] = cats.map(() => null);
        rows.forEach((r) => { data[ci(r)] = R.num(r, m); });
        bar(R.label(m), CATEGORICAL[i], data, i === 0 ? "l" : "r", i === 0 ? "l" : "r", true);
        if (i === 0) leftName = R.label(m); else rightName = R.label(m);
      });
    }
  } else {
    if (ms.length < 2 || !L.colorDim) return empty("Stacked pyramids need two measures (one per side) and a dimension on Color.");
    const present = rows.map((r) => R.get(r, L.colorDim!));
    const cm = colorMap(identity, present, CATEGORICAL.length, weightsOf(R, rows, L.colorDim, ms[0]));
    const keys = [...cm.kept, ...(cm.hasOther ? [OTHER_LABEL] : [])];
    ms.slice(0, 2).forEach((m, side) => {
      keys.forEach((k, ki) => {
        const data: (number | null)[] = cats.map(() => null);
        rows.forEach((r) => {
          const raw = R.get(r, L.colorDim!);
          if ((cm.folded(raw) ? OTHER_LABEL : String(raw)) !== k) return;
          const i = ci(r);
          data[i] = (data[i] ?? 0) + (R.num(r, m) ?? 0);
        });
        const label = k === OTHER_LABEL ? OTHER_LABEL : formatValue(rows.find((r) => String(R.get(r, L.colorDim!)) === k)?.[R.index.get(L.colorDim!.id)!], R.col(L.colorDim!));
        bar(side === 0 ? label : `${label} `, k === OTHER_LABEL ? OTHER : cm.color(k), data, side === 0 ? "l" : "r", side === 0 ? "l" : "r", ki === keys.length - 1);
      });
      if (side === 0) leftName = R.label(m); else rightName = R.label(m);
    });
  }
  const fmt = ms[0] ? R.col(ms[0]) : undefined;
  const names = [...new Set(series.map((s) => String(s.name).trim()))];
  return {
    notes,
    option: {
      ...BASE,
      legend: { ...legend(names.map((n) => ({ name: n, icon: "roundRect" })), cfg.options.legend !== false), data: names },
      grid: { left: 8, right: 16, top: 36, bottom: 28, containLabel: true },
      tooltip: {
        ...TOOLTIP_BASE, trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: "rgba(0,0,0,0.04)" } },
        formatter: (ps: { seriesName: string; value: number | null; color: string; name: string }[]) =>
          tooltipBox(ps[0]?.name ?? "", ps.filter((p) => p.value !== null).map((p) => ({ color: p.color, value: formatValue(Math.abs(p.value as number), fmt), name: p.seriesName.trim() }))),
      },
      xAxis: { ...valueAxis(fmt), axisLabel: { color: INK.muted, formatter: (v: number) => compactNumber(Math.abs(v)) }, name: `${leftName}  ◀   ▶  ${rightName}`, nameLocation: "middle", nameGap: 26, nameTextStyle: { color: INK.secondary } },
      yAxis: { ...categoryAxis(cats), inverse: true },
      series,
    },
  };
}

// ------------------------------------------------------------------ parallel coordinates and radar
function parallel(cfg: ChartConfig, R: Result, identity: unknown[]): Built {
  const ms = measures(cfg);
  if (ms.length < 2) return empty("Add two or more measures: each becomes an axis.");
  const L = layout(cfg);
  const rows = R.r.rows;
  const item = dims(cfg);
  const cm = L.colorDim ? colorMap(identity, rows.map((r) => R.get(r, L.colorDim!)), CATEGORICAL.length, weightsOf(R, rows, L.colorDim, ms[0])) : null;
  const groups = new Map<string, Obj[]>();
  rows.forEach((r, ri) => {
    const key = cm ? (cm.folded(R.get(r, L.colorDim!)) ? OTHER_LABEL : String(R.get(r, L.colorDim!))) : "all";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push({ value: ms.map((m) => R.num(r, m)), row: ri });
  });
  const series = [...groups.entries()].map(([k, data]) => ({
    name: cm ? (k === OTHER_LABEL ? OTHER_LABEL : formatValue(rows[data[0].row as number][R.index.get(L.colorDim!.id)!], R.col(L.colorDim!))) : "Items",
    type: "parallel", data, smooth: false,
    lineStyle: { width: 1.5, opacity: 0.65, color: cm ? (k === OTHER_LABEL ? OTHER : cm.color(k)) : CATEGORICAL[0] },
    emphasis: { lineStyle: { width: 3, opacity: 1 } },
  }));
  return {
    notes: [],
    option: {
      ...BASE,
      legend: legend(series.map((s) => ({ name: s.name, icon: LINE_ICON })), cfg.options.legend !== false && series.length > 1),
      parallel: { left: 64, right: 72, top: series.length > 1 ? 56 : 32, bottom: 24, parallelAxisDefault: { nameTextStyle: { color: INK.secondary, fontFamily: FONT }, axisLabel: { color: INK.muted, formatter: (v: number) => compactNumber(v) }, axisLine: { lineStyle: { color: INK.axis } }, splitLine: { show: false } } },
      parallelAxis: ms.map((m, i) => ({ dim: i, name: R.label(m), scale: true })),
      tooltip: { ...TOOLTIP_BASE, trigger: "item", formatter: (p: { data: Obj }) => { const r = rows[p.data.row as number]; return r ? tooltipBox(item.map((d) => R.text(r, d)).join(" · "), ms.map((m) => ({ value: formatValue(R.get(r, m), R.col(m)), name: R.label(m) }))) : ""; } },
      series,
    },
  };
}

function radar(cfg: ChartConfig, R: Result, identity: unknown[]): Built {
  const ms = measures(cfg);
  if (ms.length < 3) return empty("Add three or more measures: each becomes a spoke.");
  const L = layout(cfg);
  const rows = R.r.rows;
  const d = L.colorDim ?? dims(cfg)[0];
  const max = ms.map((m) => Math.max(0, ...rows.map((r) => R.num(r, m) ?? 0)));
  const cm = d ? colorMap(identity, rows.map((r) => R.get(r, d)), CATEGORICAL.length, weightsOf(R, rows, d, ms[0])) : null;
  const notes: string[] = [];
  const data = rows
    .map((r, ri) => ({ r, ri }))
    .filter(({ r }) => !cm || !cm.folded(R.get(r, d!)))
    .map(({ r, ri }) => {
      const color = cm ? cm.color(R.get(r, d!)) : CATEGORICAL[0];
      return { name: d ? R.text(r, d) : "All", value: ms.map((m) => R.num(r, m)), row: ri, itemStyle: { color }, lineStyle: { color, width: 2 }, areaStyle: { color, opacity: 0.1 } };
    });
  if (cm?.hasOther) notes.push(`Only the ${data.length} largest values are drawn; filter to see others.`);
  return {
    notes,
    option: {
      ...BASE,
      legend: legend(data.map((x) => ({ name: x.name, icon: LINE_ICON })), cfg.options.legend !== false),
      radar: {
        indicator: ms.map((m, i) => ({ name: R.label(m), max: max[i] ? max[i] * 1.1 : 1 })),
        radius: "56%", center: ["50%", "56%"], splitNumber: 4,
        axisName: { color: INK.secondary, fontFamily: FONT },
        splitLine: { lineStyle: { color: INK.grid } }, splitArea: { show: false }, axisLine: { lineStyle: { color: INK.grid } },
      },
      tooltip: { ...TOOLTIP_BASE, trigger: "item", formatter: (p: { data: { row: number; name: string } }) => { const r = rows[p.data.row]; return r ? tooltipBox(p.data.name, ms.map((m) => ({ value: formatValue(R.get(r, m), R.col(m)), name: R.label(m) }))) : ""; } },
      series: [{ type: "radar", data, symbol: "circle", symbolSize: 6 }],
    },
  };
}

// ------------------------------------------------------------------ time axis charts
function timeSeries(type: ChartType, cfg: ChartConfig, R: Result, identity: unknown[]): Built {
  const L = layout(cfg);
  const date = dims(cfg).find((p) => isDateCol(R.col(p)));
  const ms = L.values.length ? L.values : measures(cfg).slice(0, 1);
  if (!date || !ms.length) return empty("Time charts need a date and a measure.");
  const rows = R.r.rows;
  const series: Obj[] = [];
  const split = L.colorDim && L.colorDim !== date ? L.colorDim : undefined;
  const add = (name: string, color: string, _m: Pill, kind: "bar" | "line", data: Obj[]) => {
    series.push(kind === "bar"
      ? { name, type: "bar", data, barMaxWidth: 12, itemStyle: { color, borderRadius: [3, 3, 0, 0] } }
      : { name, type: "line", data, showSymbol: data.length <= 40, symbol: "circle", symbolSize: 8, lineStyle: { width: 2, color }, itemStyle: { color, borderColor: INK.surface, borderWidth: 2 }, smooth: !!cfg.options.smooth });
  };
  if (split) {
    const cm = colorMap(identity, rows.map((r) => R.get(r, split)), CATEGORICAL.length, weightsOf(R, rows, split, ms[0]));
    for (const k of cm.kept) {
      const data = rows.map((r, ri) => ({ r, ri })).filter(({ r }) => String(R.get(r, split)) === k).map(({ r, ri }) => ({ value: [R.get(r, date), R.num(r, ms[0])], row: ri }));
      add(formatValue(rows.find((r) => String(R.get(r, split)) === k)?.[R.index.get(split.id)!], R.col(split)), cm.color(k), ms[0], type === "time_bar" ? "bar" : "line", data);
    }
  } else {
    ms.slice(0, 8).forEach((m, i) => {
      const data = rows.map((r, ri) => ({ value: [R.get(r, date), R.num(r, m)], row: ri }));
      add(R.label(m), CATEGORICAL[i], m, type === "time_bar" || (type === "time_combo" && i === 0) ? "bar" : "line", data);
    });
  }
  const fmt = R.col(ms[0]);
  const showLegend = cfg.options.legend !== false && series.length > 1;
  return {
    notes: [],
    option: {
      ...BASE,
      legend: legend(series.map((s) => ({ name: s.name as string, icon: s.type === "bar" ? "roundRect" : LINE_ICON })), showLegend),
      grid: { left: 8, right: 16, top: showLegend ? 36 : 12, bottom: 56, containLabel: true },
      tooltip: {
        ...TOOLTIP_BASE, trigger: "axis", axisPointer: { type: "line", lineStyle: { color: INK.axis } },
        formatter: (ps: { seriesName: string; color: string; data: { value: [unknown, number | null]; row: number } }[]) => {
          const shown = ps.filter((p) => p.data.value[1] !== null);
          if (!shown.length) return "";
          return tooltipBox(formatValue(shown[0].data.value[0], R.col(date)), shown.map((p) => ({ color: p.color, value: formatValue(p.data.value[1], fmt), name: p.seriesName })));
        },
      },
      xAxis: { type: "time", axisLabel: { color: INK.muted, hideOverlap: true }, axisLine: { lineStyle: { color: INK.axis } }, splitLine: { show: false } },
      yAxis: valueAxis(fmt),
      dataZoom: [
        { type: "inside", filterMode: "none" },
        { type: "slider", height: 18, bottom: 8, borderColor: INK.grid, fillerColor: "rgba(42,120,214,0.12)", handleStyle: { color: "#fff", borderColor: INK.axis }, dataBackground: { lineStyle: { color: INK.axis }, areaStyle: { color: INK.grid } }, textStyle: { color: INK.muted } },
      ],
      series,
    },
  };
}

function calendar(cfg: ChartConfig, R: Result): Built {
  const date = dims(cfg).find((p) => isDateCol(R.col(p)));
  const m = layout(cfg).colorMeasure ?? measures(cfg)[0];
  if (!date || !m) return empty("A calendar needs a date (by day) and a measure.");
  const notes: string[] = [];
  if (date.grain && date.grain !== "day") notes.push("Set the date to Day (pill menu) for one square per day.");
  const rows = R.r.rows;
  const points = rows.map((r, ri) => ({ value: [String(R.get(r, date)).slice(0, 10), R.num(r, m)], row: ri })).filter((p) => p.value[0] !== "null");
  const years = [...new Set(points.map((p) => String(p.value[0]).slice(0, 4)))].sort().slice(-3);
  if (!years.length) return empty("No dates to show.");
  const vals = points.map((p) => p.value[1] as number).filter((v) => typeof v === "number");
  const col = R.col(m);
  return {
    notes,
    option: {
      ...BASE,
      tooltip: { ...TOOLTIP_BASE, formatter: (p: { data: { value: [string, number | null] } }) => tooltipBox(formatValue(p.data.value[0], { type: "date", format: { type: "date" }, grain: null, part: null }), [{ value: formatValue(p.data.value[1], col), name: R.label(m) }]) },
      visualMap: { min: Math.min(...vals, 0), max: Math.max(...vals, 1), orient: "horizontal", left: "center", bottom: 0, itemHeight: 160, itemWidth: 10, inRange: { color: SEQUENTIAL }, text: [formatValue(Math.max(...vals, 1), col, true), formatValue(Math.min(...vals, 0), col, true)], textStyle: { color: INK.muted }, calculable: false },
      calendar: years.map((y, i) => ({
        range: y, top: 30 + i * 135, left: 48, right: 16, cellSize: ["auto", 15], orient: "horizontal",
        itemStyle: { borderColor: INK.surface, borderWidth: 2, color: "#f7f7f5" }, splitLine: { show: false },
        yearLabel: { color: INK.secondary, fontFamily: FONT }, monthLabel: { color: INK.muted }, dayLabel: { color: INK.muted, firstDay: 1, nameMap: ["S", "M", "T", "W", "T", "F", "S"] },
      })),
      series: years.map((y, i) => ({ type: "heatmap", coordinateSystem: "calendar", calendarIndex: i, data: points.filter((p) => String(p.value[0]).startsWith(y)) })),
    },
  };
}

// ------------------------------------------------------------------ Gantt
function gantt(cfg: ChartConfig, R: Result, identity: unknown[]): Built {
  const task = cfg.shelves.rows.find((p) => p.role === "dimension") ?? dims(cfg).find((p) => !isDateCol(R.col(p)));
  const dates = dims(cfg).filter((p) => isDateCol(R.col(p)));
  if (!task || dates.length < 2) return empty("Gantt: put the task on Rows and the start and end dates on Columns (in that order, as exact dates).");
  const [start, end] = dates;
  const L = layout(cfg);
  const rows = R.r.rows;
  const tasks: string[] = [];
  for (const r of rows) { const k = R.text(r, task); if (!tasks.includes(k)) tasks.push(k); }
  const colorDim = L.colorDim && L.colorDim !== task ? L.colorDim : undefined;
  const cm = colorDim ? colorMap(identity, rows.map((r) => R.get(r, colorDim))) : null;
  const toTime = (v: unknown) => (v ? new Date(String(v)).getTime() : NaN);
  const data = rows.map((r, ri) => ({
    value: [tasks.indexOf(R.text(r, task)), toTime(R.get(r, start)), toTime(R.get(r, end))],
    row: ri,
    itemStyle: { color: cm ? cm.color(R.get(r, colorDim!)) : CATEGORICAL[0] },
  })).filter((d) => Number.isFinite(d.value[1]) && Number.isFinite(d.value[2]));
  return {
    notes: cm?.hasOther ? [`Smaller ${R.label(colorDim!)} values are grey.`] : [],
    option: {
      ...BASE,
      grid: { left: 8, right: 16, top: 12, bottom: 56, containLabel: true },
      tooltip: { ...TOOLTIP_BASE, formatter: (p: { data: { row: number } }) => { const r = rows[p.data.row]; return r ? tooltipBox(R.text(r, task), [{ value: `${R.text(r, start)} – ${R.text(r, end)}`, name: "" }], extraLines(R, r, L.tooltip)) : ""; } },
      xAxis: { type: "time", axisLabel: { color: INK.muted, hideOverlap: true }, axisLine: { lineStyle: { color: INK.axis } }, splitLine: { lineStyle: { color: INK.grid } } },
      yAxis: { ...categoryAxis(tasks), inverse: true },
      dataZoom: [{ type: "inside", filterMode: "weakFilter" }, { type: "slider", height: 18, bottom: 8, filterMode: "weakFilter", borderColor: INK.grid, textStyle: { color: INK.muted } }],
      series: [{
        type: "custom",
        encode: { x: [1, 2], y: 0 },
        data,
        renderItem: (_p: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[]; size: (v: number[]) => number[]; style: () => Obj }) => {
          const y = api.value(0);
          const a = api.coord([api.value(1), y]);
          const b = api.coord([api.value(2), y]);
          const h = Math.min(18, api.size([0, 1])[1] * 0.6);
          return { type: "rect", shape: { x: a[0], y: a[1] - h / 2, width: Math.max(2, b[0] - a[0]), height: h, r: 4 }, style: api.style() };
        },
      }],
    },
  };
}

// ------------------------------------------------------------------ gauges and bullet
function gaugeValues(cfg: ChartConfig, R: Result) {
  const ms = measures(cfg);
  const pick = (m?: Pill) => {
    if (!m) return null;
    const i = R.index.get(m.id);
    const t = i !== undefined ? R.r.totals?.[i] : undefined;
    return typeof t === "number" ? t : single(R, R.r.rows[0], m);
  };
  const value = pick(ms[0]);
  const target = pick(ms[1]);
  const max = target ?? cfg.options.gaugeMax ?? (value ? Math.pow(10, Math.ceil(Math.log10(Math.abs(value) * 1.25))) / (Math.abs(value) * 1.25 < Math.pow(10, Math.ceil(Math.log10(Math.abs(value) * 1.25))) / 2 ? 2 : 1) : 1);
  return { m: ms[0], t: ms[1], value, target, max };
}

function gauges(type: ChartType, cfg: ChartConfig, R: Result): Built {
  const { m, t, value, target, max } = gaugeValues(cfg, R);
  if (!m || value === null) return empty("Add a measure (and optionally a target measure, or set a maximum in Options).");
  const col = R.col(m);
  const share = max ? value / max : 0;
  const notes = dims(cfg).length ? ["Gauges show the total; dimensions are ignored."] : [];
  const caption = target !== null && t ? `${Math.round(share * 100)}% of ${R.label(t)} (${formatValue(target, R.col(t), true)})` : `of ${formatValue(max, col, true)}`;
  const color = CATEGORICAL[0];
  if (type === "gauge") {
    return {
      notes,
      option: {
        ...BASE,
        series: [{
          type: "gauge", startAngle: 210, endAngle: -30, min: 0, max, radius: "88%", center: ["50%", "58%"],
          progress: { show: true, width: 16, roundCap: true, itemStyle: { color } },
          axisLine: { roundCap: true, lineStyle: { width: 16, color: [[1, INK.grid]] } },
          pointer: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, anchor: { show: false },
          title: { offsetCenter: [0, "38%"], color: INK.secondary, fontSize: 13, fontFamily: FONT },
          detail: { offsetCenter: [0, "0%"], valueAnimation: true, color: INK.primary, fontSize: 32, fontWeight: 600, fontFamily: FONT, formatter: () => formatValue(value, col, true) },
          data: [{ value: Math.min(Math.max(value, 0), max), name: caption, row: -1 }],
        }],
        tooltip: { ...TOOLTIP_BASE, formatter: () => tooltipBox(R.label(m), [{ color, value: formatValue(value, col), name: caption }]) },
      },
    };
  }
  if (type === "polar_gauge") {
    return {
      notes,
      option: {
        ...BASE,
        polar: { radius: ["58%", "82%"], center: ["50%", "52%"] },
        angleAxis: { max, startAngle: 90, show: false },
        radiusAxis: { type: "category", data: [""], show: false },
        series: [{ type: "bar", coordinateSystem: "polar", data: [Math.min(Math.max(value, 0), max)], roundCap: true, barWidth: 18, showBackground: true, backgroundStyle: { color: INK.grid }, itemStyle: { color } }],
        title: { text: formatValue(value, col, true), subtext: caption, left: "center", top: "center", textStyle: { color: INK.primary, fontSize: 30, fontWeight: 600, fontFamily: FONT }, subtextStyle: { color: INK.secondary, fontFamily: FONT } },
        tooltip: { ...TOOLTIP_BASE, formatter: () => tooltipBox(R.label(m), [{ color, value: formatValue(value, col), name: caption }]) },
      },
    };
  }
  // flat gauge: a horizontal track with the value, a target tick when there is one
  return {
    notes,
    option: {
      ...BASE,
      grid: { left: 16, right: 16, top: "40%", bottom: "40%", containLabel: false },
      xAxis: { type: "value", max, min: 0, show: false },
      yAxis: { type: "category", data: [""], show: false },
      title: { text: formatValue(value, col, true), subtext: caption, left: 16, top: "8%", textStyle: { color: INK.primary, fontSize: 28, fontWeight: 600, fontFamily: FONT }, subtextStyle: { color: INK.secondary, fontFamily: FONT } },
      series: [{ type: "bar", data: [Math.min(Math.max(value, 0), max)], barWidth: 16, showBackground: true, backgroundStyle: { color: INK.grid, borderRadius: 8 }, itemStyle: { color, borderRadius: 8 } }],
      tooltip: { ...TOOLTIP_BASE, formatter: () => tooltipBox(R.label(m), [{ color, value: formatValue(value, col), name: caption }]) },
    },
  };
}

function bullet(cfg: ChartConfig, R: Result): Built {
  const ms = measures(cfg);
  const [m, t] = ms;
  if (!m) return empty("Add the actual measure and a target measure.");
  const item = cfg.shelves.rows.find((p) => p.role === "dimension") ?? dims(cfg)[0];
  const rows = R.r.rows;
  const cats = item ? rows.map((r) => R.text(r, item)) : [R.label(m)];
  const actual = rows.map((r) => R.num(r, m));
  const target = t ? rows.map((r) => R.num(r, t)) : [];
  const top = cats.map((_, i) => Math.max(actual[i] ?? 0, (target[i] ?? 0) * 1.2, 1));
  const band = (f: number) => top.map((v, i) => (t && target[i] ? (target[i] as number) * f : v * f));
  const b1 = band(t ? 0.6 : 0.5), b2 = band(t ? 0.9 : 0.75);
  const col = R.col(m);
  const bands = [b1, b2.map((v, i) => v - b1[i]), top.map((v, i) => Math.max(0, v - b2[i]))];
  return {
    notes: t ? [] : ["Add a second measure as the target to show the target mark."],
    option: {
      ...BASE,
      grid: { left: 8, right: 24, top: 12, bottom: 24, containLabel: true },
      tooltip: { ...TOOLTIP_BASE, trigger: "axis", axisPointer: { type: "none" }, formatter: (ps: { dataIndex: number }[]) => { const i = ps[0]?.dataIndex ?? 0; return tooltipBox(cats[i], [{ color: CATEGORICAL[0], value: formatValue(actual[i], col), name: R.label(m) }, ...(t ? [{ color: INK.primary, value: formatValue(target[i], R.col(t)), name: R.label(t) }] : [])]); } },
      xAxis: valueAxis(col),
      yAxis: { ...categoryAxis(cats), inverse: true },
      series: [
        ...bands.map((d, i) => ({ type: "bar", stack: "bands", data: d, barWidth: 22, silent: true, itemStyle: { color: NEUTRAL[i] }, z: 1 })),
        { name: R.label(m), type: "bar", data: actual.map((v, i) => ({ value: v, row: item ? i : -1 })), barWidth: 8, barGap: "-100%", z: 3, itemStyle: { color: CATEGORICAL[0], borderRadius: [0, 3, 3, 0] } },
        ...(t ? [{ name: R.label(t), type: "scatter", data: target.map((v) => v), symbol: "rect", symbolSize: [3, 22], z: 4, itemStyle: { color: INK.primary } }] : []),
      ],
    },
  };
}

// ------------------------------------------------------------------ dot plots
function dots(type: ChartType, cfg: ChartConfig, R: Result, identity: unknown[]): Built {
  const L = layout(cfg);
  const horizontal = type === "dot_h";
  const cat = (horizontal ? cfg.shelves.rows : cfg.shelves.columns).find((p) => p.role === "dimension") ?? dims(cfg).find((p) => p !== L.colorDim);
  const ms = measures(cfg);
  if (!cat || !ms.length) return empty("Add a dimension and one or more measures.");
  const rows = R.r.rows;
  const cats: string[] = [];
  for (const r of rows) { const k = R.text(r, cat); if (!cats.includes(k)) cats.push(k); }
  const series: Obj[] = [];
  const point = (ci: number, v: number | null, ri: number) => ({ value: horizontal ? [v, ci] : [ci, v], row: ri });
  if (L.colorDim && L.colorDim !== cat) {
    const cm = colorMap(identity, rows.map((r) => R.get(r, L.colorDim!)), CATEGORICAL.length, weightsOf(R, rows, L.colorDim, ms[0]));
    for (const k of [...cm.kept, ...(cm.hasOther ? [OTHER_LABEL] : [])]) {
      const data = rows.map((r, ri) => ({ r, ri })).filter(({ r }) => (cm.folded(R.get(r, L.colorDim!)) ? OTHER_LABEL : String(R.get(r, L.colorDim!))) === k).map(({ r, ri }) => point(cats.indexOf(R.text(r, cat)), R.num(r, ms[0]), ri));
      series.push({ name: k === OTHER_LABEL ? OTHER_LABEL : formatValue(rows[data[0]?.row as number]?.[R.index.get(L.colorDim.id)!], R.col(L.colorDim)), type: "scatter", data, symbolSize: 11, itemStyle: { color: k === OTHER_LABEL ? OTHER : cm.color(k), borderColor: INK.surface, borderWidth: 2 } });
    }
  } else {
    ms.slice(0, 8).forEach((m, i) => series.push({ name: R.label(m), type: "scatter", data: rows.map((r, ri) => point(cats.indexOf(R.text(r, cat)), R.num(r, m), ri)), symbolSize: 11, itemStyle: { color: CATEGORICAL[i], borderColor: INK.surface, borderWidth: 2 } }));
  }
  const col = R.col(ms[0]);
  const showLegend = cfg.options.legend !== false && series.length > 1;
  const catAxis = { ...categoryAxis(cats), boundaryGap: true, splitLine: { show: true, lineStyle: { color: "#f3f2ee" } } };
  return {
    notes: [],
    option: {
      ...BASE,
      legend: legend(series.map((s) => ({ name: s.name as string, icon: "circle" })), showLegend),
      grid: { left: 8, right: 16, top: showLegend ? 36 : 12, bottom: 8, containLabel: true },
      tooltip: { ...TOOLTIP_BASE, trigger: "item", formatter: (p: { seriesName: string; color: string; data: { value: number[]; row: number } }) => { const r = rows[p.data.row]; return r ? tooltipBox(R.text(r, cat), [{ color: p.color, value: formatValue(horizontal ? p.data.value[0] : p.data.value[1], col), name: p.seriesName }]) : ""; } },
      xAxis: horizontal ? { ...valueAxis(col), scale: true } : catAxis,
      yAxis: horizontal ? { ...catAxis, inverse: true } : { ...valueAxis(col), scale: true },
      series,
    },
  };
}

// ------------------------------------------------------------------ waterfalls
function waterfall(cfg: ChartConfig, R: Result, identity: unknown[], stacked: boolean): Built {
  const L = layout(cfg);
  const cat = L.cats.find((p) => p !== L.colorDim) ?? dims(cfg).find((p) => p !== L.colorDim);
  const m = L.values[0] ?? measures(cfg)[0];
  if (!cat || !m) return empty("Add the steps (a dimension) and a measure.");
  const rows = R.r.rows;
  const col = R.col(m);
  const cats: string[] = [];
  for (const r of rows) { const k = R.text(r, cat); if (!cats.includes(k)) cats.push(k); }
  const showTotal = cfg.options.showTotal !== false;
  const labels = [...cats, ...(showTotal ? ["Total"] : [])];
  if (!stacked || !L.colorDim) {
    const step = cats.map((c) => rows.filter((r) => R.text(r, cat) === c).reduce((a, r) => a + (R.num(r, m) ?? 0), 0));
    const base: (number | null)[] = [], up: Obj[] = [], down: Obj[] = [];
    let run = 0;
    step.forEach((v, i) => {
      const next = run + v;
      base.push(Math.min(run, next));
      up.push({ value: v >= 0 ? v : null, row: rows.findIndex((r) => R.text(r, cat) === cats[i]) });
      down.push({ value: v < 0 ? -v : null, row: rows.findIndex((r) => R.text(r, cat) === cats[i]) });
      run = next;
    });
    if (showTotal) { base.push(0); up.push({ value: null, row: -1 }); down.push({ value: null, row: -1 }); }
    const total = showTotal ? [...cats.map(() => null), run] : [];
    const running = step.reduce<number[]>((acc, v) => [...acc, (acc[acc.length - 1] ?? 0) + v], []);
    return {
      notes: [],
      option: {
        ...BASE,
        grid: { left: 8, right: 16, top: 12, bottom: 8, containLabel: true },
        tooltip: { ...TOOLTIP_BASE, trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: "rgba(0,0,0,0.04)" } }, formatter: (ps: { dataIndex: number }[]) => { const i = ps[0]?.dataIndex ?? 0; return i >= cats.length ? tooltipBox("Total", [{ value: formatValue(run, col), name: R.label(m) }]) : tooltipBox(cats[i], [{ color: step[i] >= 0 ? DIVERGING.positive : DIVERGING.negative, value: `${step[i] >= 0 ? "+" : ""}${formatValue(step[i], col)}`, name: "Change" }, { value: formatValue(running[i], col), name: "Running total" }]); } },
        xAxis: categoryAxis(labels),
        yAxis: valueAxis(col),
        series: [
          { type: "bar", stack: "w", data: base, itemStyle: { color: "transparent" }, silent: true, barMaxWidth: 32 },
          { name: "Increase", type: "bar", stack: "w", data: up, barMaxWidth: 32, itemStyle: { color: DIVERGING.positive, borderRadius: 3 } },
          { name: "Decrease", type: "bar", stack: "w", data: down, barMaxWidth: 32, itemStyle: { color: DIVERGING.negative, borderRadius: 3 } },
          ...(showTotal ? [{ name: "Total", type: "bar", stack: "w", data: total, barMaxWidth: 32, itemStyle: { color: INK.secondary, borderRadius: 3 } }] : []),
        ],
      },
    };
  }
  // Stacked waterfall: each step split by Color; parts stack on the running total
  const colorDim = L.colorDim;
  const cm = colorMap(identity, rows.map((r) => R.get(r, colorDim)), CATEGORICAL.length, weightsOf(R, rows, colorDim, m));
  const keys = [...cm.kept, ...(cm.hasOther ? [OTHER_LABEL] : [])];
  const part = (c: string, k: string) => rows.filter((r) => R.text(r, cat) === c && (cm.folded(R.get(r, colorDim)) ? OTHER_LABEL : String(R.get(r, colorDim))) === k).reduce((a, r) => a + (R.num(r, m) ?? 0), 0);
  const stepTotal = cats.map((c) => keys.reduce((a, k) => a + part(c, k), 0));
  const base: (number | null)[] = [];
  let run = 0;
  stepTotal.forEach((v) => { base.push(v >= 0 ? run : run + v); run += v; });
  if (showTotal) base.push(0);
  const series: Obj[] = [{ type: "bar", stack: "w", data: base, itemStyle: { color: "transparent" }, silent: true, barMaxWidth: 32 }];
  keys.forEach((k) => {
    const data = cats.map((c) => Math.abs(part(c, k)) || null) as (number | null)[];
    if (showTotal) data.push(Math.abs(cats.reduce((a, c) => a + part(c, k), 0)) || null);
    series.push({ name: k === OTHER_LABEL ? OTHER_LABEL : formatValue(rows.find((r) => String(R.get(r, colorDim)) === k)?.[R.index.get(colorDim.id)!], R.col(colorDim)), type: "bar", stack: "w", data, barMaxWidth: 32, itemStyle: { color: k === OTHER_LABEL ? OTHER : cm.color(k), borderColor: INK.surface, borderWidth: 1 } });
  });
  return {
    notes: stepTotal.some((v) => v < 0) ? ["Steps that go down are drawn below the running total."] : [],
    option: {
      ...BASE,
      legend: legend(series.slice(1).map((s) => ({ name: s.name as string, icon: "roundRect" })), cfg.options.legend !== false),
      grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
      tooltip: { ...TOOLTIP_BASE, trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: "rgba(0,0,0,0.04)" } }, formatter: (ps: { seriesName: string; color: string; value: number | null; dataIndex: number }[]) => tooltipBox(labels[ps[0]?.dataIndex ?? 0], ps.filter((p) => p.seriesName && p.value).map((p) => ({ color: p.color, value: formatValue(p.value, col), name: p.seriesName }))) },
      xAxis: categoryAxis(labels),
      yAxis: valueAxis(col),
      series,
    },
  };
}

// ------------------------------------------------------------------ funnel and Origami
function stages(cfg: ChartConfig, R: Result) {
  const L = layout(cfg);
  const d = L.colorDim ?? L.cats[0] ?? dims(cfg)[0];
  const m = L.size ?? L.values[0] ?? measures(cfg)[0];
  if (!d || !m) return null;
  const rows = R.r.rows;
  let items = rows.map((r, ri) => ({ raw: R.get(r, d), name: R.text(r, d), value: R.num(r, m) ?? 0, row: ri }));
  if (cfg.options.order !== "data" && !cfg.sort) items = [...items].sort((a, b) => a.value - b.value);
  return { d, m, items, col: R.col(m) };
}

function funnel(cfg: ChartConfig, R: Result): Built {
  const s = stages(cfg, R);
  if (!s) return empty("Add the stages (a dimension) and a measure.");
  const items = cfg.options.order !== "data" && !cfg.sort ? [...s.items].reverse() : s.items;
  const first = items[0]?.value || 1;
  const n = items.length;
  const ramp = SEQUENTIAL.slice(2).reverse();
  return {
    notes: [],
    option: {
      ...BASE,
      tooltip: { ...TOOLTIP_BASE, trigger: "item", formatter: (p: { dataIndex: number }) => { const i = p.dataIndex; const it = items[i]; const prev = i > 0 ? items[i - 1].value : null; return tooltipBox(it.name, [{ value: formatValue(it.value, s.col), name: R.label(s.m) }], [`${((it.value / first) * 100).toFixed(1)}% of the first stage`, ...(prev ? [`${((it.value / prev) * 100).toFixed(1)}% of the previous stage`] : [])]); } },
      series: [{
        type: "funnel", sort: "none", left: 8, width: "58%", top: 16, bottom: 16, gap: 2, minSize: "8%",
        data: items.map((it, i) => ({ name: it.name, value: it.value, row: it.row, itemStyle: { color: ramp[Math.min(ramp.length - 1, Math.floor((i / Math.max(1, n - 1)) * (ramp.length - 1)))], borderColor: INK.surface, borderWidth: 0 } })),
        // Labels sit beside the stages (narrow stages have no room inside)
        label: { show: true, position: "right", fontFamily: FONT, color: INK.secondary, formatter: (p: { name: string; value: number }) => `${p.name}  ${formatValue(p.value, s.col, true)}  (${Math.round((p.value / first) * 100)}%)` },
        labelLine: { show: true, length: 12, lineStyle: { color: INK.axis } },
        itemStyle: { borderRadius: 2 },
      }],
    },
  };
}

function origami(cfg: ChartConfig, R: Result, identity: unknown[]): Built {
  const s = stages(cfg, R);
  if (!s) return empty("Origami: put a dimension on Color and a measure on Size.");
  const cm = colorMap(identity, s.items.map((i) => i.raw), CATEGORICAL.length, new Map(s.items.map((i) => [String(i.raw), Math.abs(i.value)])));
  const items = s.items.map((it) => ({ ...it, color: cm.folded(it.raw) ? OTHER : cm.color(it.raw) }));
  const vmax = Math.max(1, ...items.map((i) => i.value));
  const total = items.reduce((a, b) => a + b.value, 0);
  const n = items.length;
  const showTotal = cfg.options.showTotal !== false && additive(s.col);
  const pct = (v: number) => `${((v / (total || 1)) * 100).toFixed(1)}%`;
  return {
    notes: n > 8 ? ["Origami reads best with a few parts; consider a filter or top N."] : [],
    option: {
      ...BASE,
      title: showTotal ? { text: `${R.label(s.m)}: ${formatValue(total, s.col, true)}`, left: "center", top: 0, textStyle: { color: INK.primary, fontSize: 14, fontWeight: 500, fontFamily: FONT } } : undefined,
      legend: legend(items.map((i) => ({ name: i.name, icon: "roundRect" })), cfg.options.legend !== false && n > 1),
      tooltip: { ...TOOLTIP_BASE, trigger: "item", formatter: (p: { dataIndex: number }) => { const it = items[p.dataIndex]; return it ? tooltipBox(it.name, [{ color: it.color, value: formatValue(it.value, s.col), name: `${pct(it.value)} of total` }]) : ""; } },
      xAxis: { show: false, type: "value", min: 0, max: 1 },
      yAxis: { show: false, type: "value", min: 0, max: 1 },
      grid: { left: 8, right: 8, top: showTotal || n > 1 ? 56 : 16, bottom: 16 },
      series: [{
        type: "custom",
        data: items.map((it, i) => ({ value: [i, it.value], row: it.row })),
        renderItem: (params: { dataIndex: number; coordSys: { x: number; y: number; width: number; height: number } }) => {
          const { x, y, width, height } = params.coordSys;
          const i = params.dataIndex;
          const fold = Math.min(28, width * 0.04);
          const blockW = (width - fold * (n - 1)) / n;
          const labelSpace = 22;
          const maxH = height - labelSpace * 2;
          const cy = y + labelSpace + maxH / 2;
          const h = (v: number) => Math.max(3, (maxH * v) / vmax);
          const it = items[i];
          const x0 = x + i * (blockW + fold);
          const hh = h(it.value);
          const children: Obj[] = [
            { type: "rect", shape: { x: x0, y: cy - hh / 2, width: blockW, height: hh, r: 3 }, style: { fill: it.color } },
            { type: "text", style: { x: x0 + blockW / 2, y: cy - hh / 2 - 8, text: it.name, align: "center", verticalAlign: "bottom", fill: INK.secondary, font: `13px ${FONT}` } },
          ];
          const valueText = `${formatValue(it.value, s.col, true)}`;
          const big = Math.min(36, Math.max(12, hh * 0.28));
          if (hh >= 26 && blockW >= 56) children.push({ type: "text", style: { x: x0 + blockW / 2, y: cy, text: valueText, align: "center", verticalAlign: "middle", fill: inkOn(it.color === OTHER ? "#b9b7ae" : it.color), font: `${hh >= 80 ? 600 : 500} ${big}px ${FONT}` } });
          else children.push({ type: "text", style: { x: x0 + blockW / 2, y: cy + hh / 2 + 6, text: valueText, align: "center", verticalAlign: "top", fill: INK.primary, font: `12px ${FONT}` } });
          if (i < n - 1) {
            const nx = x0 + blockW + fold;
            const nh = h(items[i + 1].value);
            children.push({ type: "polygon", shape: { points: [[x0 + blockW, cy - hh / 2], [nx, cy - nh / 2], [nx, cy + nh / 2], [x0 + blockW, cy + hh / 2]] }, style: { fill: items[i + 1].color, opacity: 0.55 } });
          }
          return { type: "group", children };
        },
      }],
    },
  };
}

// ------------------------------------------------------------------ Sankey
function sankey(cfg: ChartConfig, R: Result, identity: unknown[]): Built {
  const levels = dims(cfg);
  const m = measures(cfg)[0];
  if (levels.length < 2 || !m) return empty("Sankey: add two or more dimensions (from → to, in shelf order) and a measure.");
  const rows = R.r.rows;
  const nodeName = (li: number, v: string) => `${li}\u0001${v}`;
  const shown = (name: string) => name.split("\u0001")[1] ?? name;
  const nodes = new Map<string, number>();
  const links = new Map<string, number>();
  rows.forEach((r) => {
    const v = R.num(r, m) ?? 0;
    if (v <= 0) return;
    levels.forEach((p, li) => {
      const a = nodeName(li, R.text(r, p));
      nodes.set(a, (nodes.get(a) ?? 0) + v);
      if (li < levels.length - 1) {
        const b = nodeName(li + 1, R.text(r, levels[li + 1]));
        links.set(`${a}\u0002${b}`, (links.get(`${a}\u0002${b}`) ?? 0) + v);
      }
    });
  });
  const firstLevel = [...nodes.keys()].filter((k) => k.startsWith("0\u0001")).map(shown);
  const cm = colorMap(identity, firstLevel, CATEGORICAL.length, new Map(firstLevel.map((n) => [n, nodes.get(nodeName(0, n)) ?? 0])));
  const color = (name: string) => (name.startsWith("0\u0001") ? (cm.folded(shown(name)) ? OTHER : cm.color(shown(name))) : "#898781");
  const col = R.col(m);
  return {
    notes: nodes.size > 60 ? ["Many flows: consider a filter or top N."] : [],
    option: {
      ...BASE,
      tooltip: { ...TOOLTIP_BASE, trigger: "item", formatter: (p: { dataType: string; data: { source?: string; target?: string; name?: string; value: number } }) => p.dataType === "edge" ? tooltipBox(`${shown(p.data.source!)} → ${shown(p.data.target!)}`, [{ value: formatValue(p.data.value, col), name: R.label(m) }]) : tooltipBox(shown(p.data.name ?? ""), [{ value: formatValue(nodes.get(p.data.name ?? "") ?? 0, col), name: R.label(m) }]) },
      series: [{
        type: "sankey", left: 8, right: 120, top: 16, bottom: 16, nodeWidth: 12, nodeGap: 10, draggable: false, emphasis: { focus: "adjacency" },
        data: [...nodes.keys()].map((n) => ({ name: n, itemStyle: { color: color(n), borderWidth: 0 } })),
        links: [...links.entries()].map(([k, v]) => { const [source, target] = k.split("\u0002"); return { source, target, value: v, lineStyle: { color: color(source), opacity: 0.28 } }; }),
        label: { color: INK.secondary, fontFamily: FONT, formatter: (p: { name: string }) => shown(p.name) },
        lineStyle: { curveness: 0.5 },
      }],
    },
  };
}

// ------------------------------------------------------------------ Geo Map with uploaded boundaries
const geoCache = new Map<number, Promise<{ name: string; name_property: string; geojson: { features: { properties: Record<string, unknown> }[] } }>>();

const normName = (s: string) => s.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9؀-ۿ]+/g, "");

async function geomap(cfg: ChartConfig, R: Result): Promise<Built> {
  const L = layout(cfg);
  const d = [...L.cats, ...(L.colorDim ? [L.colorDim] : []), ...L.detail][0];
  const m = L.colorMeasure ?? L.size ?? L.values[0];
  if (!cfg.options.boundary) return empty("Choose the map in Options (an admin or creator uploads boundary files under Data → Maps).");
  if (!d || !m) return empty("Add the area names (a dimension) and a measure.");
  const id = cfg.options.boundary;
  if (!geoCache.has(id)) geoCache.set(id, get(`/boundaries/${id}`));
  const b = await geoCache.get(id)!;
  const prop = b.name_property;
  const names = new Map<string, string>();
  for (const f of b.geojson.features) {
    const v = f.properties?.[prop];
    if (v !== undefined && v !== null) names.set(normName(String(v)), String(v));
  }
  const rows = R.r.rows;
  const matched = rows.map((r) => names.get(normName(String(R.get(r, d) ?? ""))) ?? null);
  const vals = rows.map((r) => R.num(r, m)).filter((v): v is number => v !== null);
  const col = R.col(m);
  const missing = rows.filter((_, i) => !matched[i]).map((r) => R.text(r, d));
  const mapName = `boundary-${id}`;
  return {
    notes: missing.length ? [`Not on the map: ${missing.slice(0, 5).join(", ")}${missing.length > 5 ? ` and ${missing.length - 5} more` : ""}.`] : [],
    maps: { [mapName]: b.geojson },
    option: {
      ...BASE,
      tooltip: { ...TOOLTIP_BASE, trigger: "item", formatter: (p: { name: string; data?: { row: number } }) => { const r = p.data ? rows[p.data.row] : undefined; return r ? tooltipBox(R.text(r, d), [{ value: formatValue(R.get(r, m), col), name: R.label(m) }]) : tooltipBox(p.name, [{ value: "No data", name: "" }]); } },
      visualMap: { min: Math.min(...vals, 0), max: Math.max(...vals, 1), orient: "horizontal", left: 0, bottom: 0, itemHeight: 140, itemWidth: 10, inRange: { color: SEQUENTIAL }, text: [formatValue(Math.max(...vals, 1), col, true), formatValue(Math.min(...vals, 0), col, true)], textStyle: { color: INK.muted }, calculable: false },
      series: [{
        type: "map", map: mapName, nameProperty: prop, roam: true,
        itemStyle: { areaColor: "#f0efec", borderColor: INK.surface, borderWidth: 1 }, emphasis: { itemStyle: { areaColor: "#e1e0d9" }, label: { show: false } },
        label: { show: cfg.options.labels !== "none", color: INK.secondary, fontFamily: FONT }, select: { disabled: true },
        data: rows.map((r, ri) => (matched[ri] ? { name: matched[ri], value: R.num(r, m), row: ri } : null)).filter(Boolean),
      }],
    },
  };
}

export const MORE_TYPES = new Set<ChartType>([
  "pyramid", "stacked_pyramid", "parallel", "radar", "timeline", "time_bar", "time_combo", "calendar", "gantt", "gauge",
  "polar_gauge", "flat_gauge", "bullet", "dot_h", "dot_v", "waterfall", "stacked_waterfall", "funnel", "origami", "sankey", "geomap",
]);

export async function buildMore(type: ChartType, cfg: ChartConfig, R: Result, identity: unknown[]): Promise<Built> {
  switch (type) {
    case "pyramid": return pyramid(cfg, R, identity, false);
    case "stacked_pyramid": return pyramid(cfg, R, identity, true);
    case "parallel": return parallel(cfg, R, identity);
    case "radar": return radar(cfg, R, identity);
    case "timeline": case "time_bar": case "time_combo": return timeSeries(type, cfg, R, identity);
    case "calendar": return calendar(cfg, R);
    case "gantt": return gantt(cfg, R, identity);
    case "gauge": case "polar_gauge": case "flat_gauge": return gauges(type, cfg, R);
    case "bullet": return bullet(cfg, R);
    case "dot_h": case "dot_v": return dots(type, cfg, R, identity);
    case "waterfall": return waterfall(cfg, R, identity, false);
    case "stacked_waterfall": return waterfall(cfg, R, identity, true);
    case "funnel": return funnel(cfg, R);
    case "origami": return origami(cfg, R, identity);
    case "sankey": return sankey(cfg, R, identity);
    case "geomap": return geomap(cfg, R);
    default: return empty("Unknown chart type");
  }
}

export type { EChartsCoreOption };
