import clsx from "clsx";
import { ArrowDown, ArrowUp } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";

import { buildChart, type Built } from "../../lib/chart/build";
import { formatValue } from "../../lib/chart/format";
import { CATEGORICAL, INK, inkOn, sequential } from "../../lib/chart/palette";
import type { ChartConfig, ChartType, Pill } from "../../lib/chart/types";
import type { QueryColumn, QueryResult } from "../../lib/types";
import EChart, { type EChartHandle, type MarkEvent } from "./EChart";

export interface ChartViewHandle {
  png: () => string | null;
}

interface Props {
  cfg: ChartConfig;
  type: ChartType;
  result: QueryResult;
  identity: unknown[];
  height?: number;
  stale?: boolean;
  onMark?: (e: MarkEvent) => void;
  onSort?: (pillId: string) => void;
}

const MAX_TABLE_ROWS = 2000;

// ------------------------------------------------------------------ table and pivot table
function shadeFor(values: number[]) {
  const lo = Math.min(...values), hi = Math.max(...values);
  return (v: unknown) => {
    if (typeof v !== "number" || !Number.isFinite(v) || hi === lo) return undefined;
    const bg = sequential(((v - lo) / (hi - lo)) * 0.85);
    return { background: bg, color: inkOn(bg) };
  };
}

function orderedColumns(cfg: ChartConfig, result: QueryResult): number[] {
  const byKey = new Map(result.columns.map((c, i) => [c.key, i]));
  const s = cfg.shelves;
  const dims = [...s.rows, ...s.columns, ...s.detail, ...s.color, ...s.label, ...s.tooltip, ...s.size].filter((p) => p.role === "dimension");
  const meas = [...s.columns, ...s.rows, ...s.color, ...s.size, ...s.label, ...s.tooltip, ...s.detail].filter((p) => p.role === "measure");
  const seen = new Set<number>();
  const out: number[] = [];
  for (const p of [...dims, ...meas]) {
    const i = byKey.get(p.id);
    if (i !== undefined && !seen.has(i)) {
      seen.add(i);
      out.push(i);
    }
  }
  result.columns.forEach((_, i) => !seen.has(i) && out.push(i)); // rows mode
  return out;
}

function FlatTable({ cfg, result, onMark, onSort }: Pick<Props, "cfg" | "result" | "onMark" | "onSort">) {
  const order = orderedColumns(cfg, result);
  const cols = order.map((i) => result.columns[i]);
  const shades = cfg.options.colorScale
    ? order.map((i, j) => (cols[j].role === "measure" ? shadeFor(result.rows.map((r) => r[i]).filter((v): v is number => typeof v === "number")) : undefined))
    : [];
  const rows = result.rows.slice(0, MAX_TABLE_ROWS);
  const isNum = (c: QueryColumn) => c.role === "measure" || ["integer", "number"].includes(c.type);
  return (
    <div className="h-full overflow-auto">
      <table className="min-w-full text-sm">
        <thead className="sticky top-0 z-10 bg-white">
          <tr className="border-b border-slate-200">
            {cols.map((c) => {
              const sorted = cfg.sort?.pill === c.key ? cfg.sort.dir : null;
              return (
                <th key={c.key} className={clsx("whitespace-nowrap px-3 py-2 text-xs font-semibold text-slate-600", isNum(c) ? "text-right" : "text-left")}>
                  <button className="inline-flex items-center gap-1 hover:text-slate-900 disabled:cursor-default" disabled={!onSort} onClick={() => onSort?.(c.key)}>
                    {c.label}
                    {sorted === "asc" && <ArrowUp size={12} />}
                    {sorted === "desc" && <ArrowDown size={12} />}
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r, ri) => (
            <tr key={ri} className={clsx(onMark && "cursor-pointer hover:bg-slate-50")} onClick={(e) => onMark?.({ row: ri, x: e.clientX, y: e.clientY })}>
              {order.map((i, j) => {
                const c = cols[j];
                const style = shades[j]?.(r[i]);
                return (
                  <td key={i} style={style} className={clsx("whitespace-nowrap px-3 py-1.5", isNum(c) && "text-right tabular-nums", r[i] === null && "text-slate-400")}>
                    {r[i] === null ? (c.role === "dimension" ? "(empty)" : "") : formatValue(r[i], c)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
        {result.totals && (
          <tfoot className="sticky bottom-0 bg-slate-50">
            <tr className="border-t border-slate-300 font-semibold">
              {order.map((i, j) => (
                <td key={i} className={clsx("whitespace-nowrap px-3 py-2", isNum(cols[j]) && "text-right tabular-nums")}>
                  {j === 0 && cols[j].role === "dimension" ? "Total" : result.totals![i] === null ? "" : formatValue(result.totals![i], cols[j])}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
      {result.rows.length > MAX_TABLE_ROWS && <p className="p-2 text-xs text-slate-500">Showing the first {MAX_TABLE_ROWS.toLocaleString()} rows. Export to see all.</p>}
    </div>
  );
}

function PivotTable({ cfg, result, onMark }: Pick<Props, "cfg" | "result" | "onMark">) {
  const idx = new Map(result.columns.map((c, i) => [c.key, i]));
  const s = cfg.shelves;
  const colDims = s.columns.filter((p) => p.role === "dimension" && idx.has(p.id));
  const rowDims = [...s.rows, ...s.detail, ...s.color, ...s.label, ...s.tooltip].filter((p) => p.role === "dimension" && !colDims.includes(p));
  const measures = [...s.rows, ...s.columns, ...s.color, ...s.size, ...s.label, ...s.tooltip, ...s.detail].filter((p) => p.role === "measure");
  const col = (p: Pill): QueryColumn => result.columns[idx.get(p.id) ?? -1] ?? { key: p.id, label: p.field, field: p.field, role: p.role, type: "string", format: { type: "text" }, agg: null, grain: null, part: null, calc: null };
  const text = (r: unknown[], p: Pill) => (r[idx.get(p.id)!] === null ? "(empty)" : formatValue(r[idx.get(p.id)!], col(p)));
  const colKeys: string[] = [];
  const colParts = new Map<string, string[]>();
  const rowKeys: string[] = [];
  const rowParts = new Map<string, string[]>();
  const cells = new Map<string, { row: number; values: unknown[] }>();
  result.rows.forEach((r, ri) => {
    const ck = colDims.map((p) => text(r, p)).join("\u0001");
    const rk = rowDims.map((p) => text(r, p)).join("\u0001");
    if (!colParts.has(ck)) { colParts.set(ck, colDims.map((p) => text(r, p))); colKeys.push(ck); }
    if (!rowParts.has(rk)) { rowParts.set(rk, rowDims.map((p) => text(r, p))); rowKeys.push(rk); }
    cells.set(`${rk}\u0002${ck}`, { row: ri, values: measures.map((m) => r[idx.get(m.id)!]) });
  });
  const shades = cfg.options.colorScale
    ? measures.map((m) => shadeFor(result.rows.map((r) => r[idx.get(m.id)!]).filter((v): v is number => typeof v === "number")))
    : [];
  const levels = colDims.length;
  return (
    <div className="h-full overflow-auto">
      <table className="min-w-full border-separate border-spacing-0 text-sm">
        <thead className="sticky top-0 z-10 bg-white">
          {Array.from({ length: levels }, (_, li) => (
            <tr key={li}>
              {rowDims.map((p, i) => <th key={p.id} className="px-3 py-1.5 text-left text-xs font-semibold text-slate-600">{li === levels - 1 ? col(p).label : i === 0 ? col(colDims[li]).label : ""}</th>)}
              {!rowDims.length && <th className="px-3 py-1.5 text-left text-xs font-semibold text-slate-600">{col(colDims[li]).label}</th>}
              {colKeys.map((ck) => (
                <th key={ck} colSpan={Math.max(1, measures.length)} className="whitespace-nowrap border-b border-slate-200 px-3 py-1.5 text-center text-xs font-semibold text-slate-700">{colParts.get(ck)![li]}</th>
              ))}
            </tr>
          ))}
          {measures.length > 1 && (
            <tr>
              {(rowDims.length ? rowDims : [null]).map((_, i) => <th key={i} />)}
              {colKeys.flatMap((ck) => measures.map((m) => <th key={`${ck}-${m.id}`} className="whitespace-nowrap border-b border-slate-200 px-3 py-1.5 text-right text-xs font-medium text-slate-500">{col(m).label}</th>))}
            </tr>
          )}
        </thead>
        <tbody>
          {rowKeys.slice(0, MAX_TABLE_ROWS).map((rk) => (
            <tr key={rk} className="hover:bg-slate-50">
              {(rowDims.length ? rowParts.get(rk)! : [measures.length === 1 ? col(measures[0]).label : ""]).map((t, i) => <td key={i} className="whitespace-nowrap border-b border-slate-100 px-3 py-1.5 font-medium text-slate-700">{t}</td>)}
              {colKeys.flatMap((ck) => {
                const cell = cells.get(`${rk}\u0002${ck}`);
                return measures.map((m, mi) => {
                  const v = cell?.values[mi];
                  return (
                    <td key={`${ck}-${m.id}`} style={shades[mi]?.(v)} onClick={(e) => cell && onMark?.({ row: cell.row, x: e.clientX, y: e.clientY })}
                      className={clsx("whitespace-nowrap border-b border-slate-100 px-3 py-1.5 text-right tabular-nums", cell && onMark && "cursor-pointer")}>
                      {v === null || v === undefined ? "" : formatValue(v, col(m))}
                    </td>
                  );
                });
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------ number tiles
function Sparkline({ values }: { values: (number | null)[] }) {
  const option = useMemo(() => ({
    grid: { left: 0, right: 4, top: 4, bottom: 4 },
    xAxis: { type: "category", show: false, data: values.map((_, i) => i) },
    yAxis: { type: "value", show: false, scale: true },
    series: [{
      type: "line",
      data: values.map((v, i) => (i === values.length - 1 ? { value: v, symbol: "circle", symbolSize: 8, itemStyle: { color: CATEGORICAL[0], borderColor: "#fff", borderWidth: 2 } } : v)),
      symbol: "none",
      lineStyle: { width: 2, color: INK.axis },
      silent: true,
    }],
    animation: false,
  }), [values]);
  return <EChart option={option} height={48} />;
}

function NumberTiles({ cfg, result }: Pick<Props, "cfg" | "result">) {
  const measures = result.columns.map((c, i) => [c, i] as const).filter(([c]) => c.role === "measure");
  const dateDim = result.columns.findIndex((c) => c.role === "dimension");
  return (
    <div className="grid h-full content-center gap-4" style={{ gridTemplateColumns: `repeat(${Math.min(measures.length, 4)}, minmax(0, 1fr))` }}>
      {measures.map(([c, i]) => {
        const value = result.totals?.[i] ?? result.rows[0]?.[i] ?? null;
        const trend = dateDim >= 0 && result.rows.length > 2 ? result.rows.map((r) => (typeof r[i] === "number" ? (r[i] as number) : null)) : null;
        return (
          <div key={c.key} className="px-2">
            <div className="text-sm text-slate-600">{c.label}</div>
            <div className="mt-1 text-4xl font-semibold text-slate-900">{value === null ? "—" : formatValue(value, c, Math.abs(Number(value)) >= 100000)}</div>
            {value !== null && Math.abs(Number(value)) >= 100000 && <div className="text-xs text-slate-500">{formatValue(value, c)}</div>}
            {trend && <div className="mt-2"><Sparkline values={trend.slice(-24)} /></div>}
          </div>
        );
      })}
      {!measures.length && <p className="text-sm text-slate-500">Add a measure.</p>}
      {cfg.shelves.columns.length + cfg.shelves.rows.length === 0 && null}
    </div>
  );
}

// ------------------------------------------------------------------ the view
const ChartView = forwardRef<ChartViewHandle, Props>(function ChartView({ cfg, type, result, identity, height = 420, stale, onMark, onSort }, ref) {
  const [built, setBuilt] = useState<Built | null>(null);
  const [error, setError] = useState("");
  const chart = useRef<EChartHandle>(null);
  useImperativeHandle(ref, () => ({ png: () => chart.current?.png() ?? null }));
  const usesECharts = !["table", "kpi"].includes(type);

  useEffect(() => {
    if (!usesECharts) return;
    let cancelled = false;
    buildChart(type, cfg, result, identity)
      .then((b) => !cancelled && (setBuilt(b), setError("")))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Could not draw the chart"));
    return () => {
      cancelled = true;
    };
  }, [type, cfg, result, identity, usesECharts]);

  const empty = result.rows.length === 0;
  const pivot = type === "table" && cfg.shelves.columns.some((p) => p.role === "dimension") && result.columns.some((c) => c.role === "measure");
  return (
    <div className={clsx("flex h-full flex-col transition-opacity", stale && "opacity-50")}>
      {empty ? (
        <div className="flex items-center justify-center text-sm text-slate-500" style={{ height }}>No data for these fields and filters.</div>
      ) : type === "table" ? (
        <div style={{ height }}>{pivot ? <PivotTable cfg={cfg} result={result} onMark={onMark} /> : <FlatTable cfg={cfg} result={result} onMark={onMark} onSort={onSort} />}</div>
      ) : type === "kpi" ? (
        <div style={{ height }}><NumberTiles cfg={cfg} result={result} /></div>
      ) : error ? (
        <p className="p-6 text-sm text-red-700">{error}</p>
      ) : built && Object.keys(built.option).length ? (
        <EChart ref={chart} option={built.option} maps={built.maps} height={height} onMark={onMark} />
      ) : (
        <div className="flex items-center justify-center text-sm text-slate-500" style={{ height }}>{built?.notes[0] ?? "Drawing…"}</div>
      )}
      {!empty && (built?.notes.length || result.truncated) && usesECharts && built && Object.keys(built.option).length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-slate-500">
          {result.truncated && <li>Showing the first {result.row_count.toLocaleString()} results. Add a filter to see the rest.</li>}
          {built.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
      {!usesECharts && result.truncated && <p className="mt-2 text-xs text-slate-500">Showing the first {result.row_count.toLocaleString()} results.</p>}
    </div>
  );
});

export default ChartView;
