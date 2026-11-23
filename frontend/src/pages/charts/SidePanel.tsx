import clsx from "clsx";
import {
  AreaChart, BarChart3, BarChartHorizontal, Grid3x3, Hash, LineChart, Map, PieChart, ScatterChart, Sparkles, Table2,
  LayoutGrid, Layers,
} from "lucide-react";
import type { ReactNode } from "react";

import { Input, Select } from "../../components/ui";
import { CHART_KINDS } from "../../lib/chart/showme";
import type { ChartConfig, ChartOptions, ChartType, RefLine } from "../../lib/chart/types";

const ICONS: Record<ChartType, ReactNode> = {
  table: <Table2 size={18} />, kpi: <Hash size={18} />, bar: <BarChart3 size={18} />, stacked: <Layers size={18} />,
  line: <LineChart size={18} />, area: <AreaChart size={18} />, combo: <BarChartHorizontal size={18} />,
  scatter: <ScatterChart size={18} />, heatmap: <Grid3x3 size={18} />, treemap: <LayoutGrid size={18} />,
  donut: <PieChart size={18} />, map: <Map size={18} />,
};

export function ShowMe({ cfg, resolved, available, onPick }: {
  cfg: ChartConfig; resolved: ChartType; available: Set<ChartType>; onPick: (t: ChartType | "auto") => void;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Show me</h3>
        <button onClick={() => onPick("auto")}
          className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs", cfg.type === "auto" ? "bg-brand-50 text-brand-700" : "text-slate-500 hover:bg-slate-100")}>
          <Sparkles size={12} /> Automatic
        </button>
      </div>
      <div className="grid grid-cols-4 gap-1">
        {CHART_KINDS.map((k) => {
          const ok = available.has(k.type);
          return (
            <button key={k.type} disabled={!ok} onClick={() => onPick(k.type)} title={`${k.label}: ${k.needs}`} aria-label={k.label}
              className={clsx("flex aspect-square flex-col items-center justify-center gap-0.5 rounded-md border text-[10px]",
                resolved === k.type ? "border-brand-600 bg-brand-50 text-brand-700" : "border-transparent text-slate-600 hover:bg-slate-100",
                !ok && "cursor-not-allowed opacity-30")}>
              {ICONS[k.type]}
              <span className="leading-tight">{k.label}</span>
            </button>
          );
        })}
      </div>
      <p className="mt-2 text-xs text-slate-500">{CHART_KINDS.find((k) => k.type === resolved)?.needs}</p>
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex items-center justify-between gap-2 text-sm">
      <span className="text-slate-600">{label}</span>
      {children}
    </label>
  );
}

export function OptionsPanel({ cfg, type, onChange }: { cfg: ChartConfig; type: ChartType; onChange: (o: Partial<ChartOptions>, limit?: number) => void }) {
  const o = cfg.options;
  const cartesian = ["bar", "stacked", "line", "area", "combo"].includes(type);
  const ref = o.refLine;
  const setRef = (r: Partial<RefLine> | null) => onChange({ refLine: r === null ? null : ({ ...(ref ?? { kind: "avg" }), ...r } as RefLine) });
  return (
    <div className="space-y-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Options</h3>
      <div>
        <span className="mb-1 block text-sm text-slate-600">Title</span>
        <Input className="py-1.5" value={o.title ?? ""} placeholder="Automatic" onChange={(e) => onChange({ title: e.target.value })} />
      </div>
      {type !== "table" && type !== "kpi" && (
        <Row label="Value labels">
          <div className="w-28">
            <Select className="py-1" value={o.labels ?? "none"} onChange={(e) => onChange({ labels: e.target.value as ChartOptions["labels"] })}>
              <option value="none">None</option>
              {type === "line" || type === "area" ? <option value="ends">At line ends</option> : null}
              <option value="all">On marks</option>
            </Select>
          </div>
        </Row>
      )}
      {type !== "table" && type !== "kpi" && type !== "heatmap" && (
        <Row label="Legend"><input type="checkbox" checked={o.legend !== false} onChange={(e) => onChange({ legend: e.target.checked })} /></Row>
      )}
      {type === "table" && (
        <>
          <Row label="Grand totals"><input type="checkbox" checked={!!o.totals} onChange={(e) => onChange({ totals: e.target.checked })} /></Row>
          <Row label="Shade values"><input type="checkbox" checked={!!o.colorScale} onChange={(e) => onChange({ colorScale: e.target.checked })} /></Row>
        </>
      )}
      {type === "stacked" && (
        <Row label="100% stacked"><input type="checkbox" checked={!!o.percentStack} onChange={(e) => onChange({ percentStack: e.target.checked })} /></Row>
      )}
      {(type === "line" || type === "area") && (
        <Row label="Smooth lines"><input type="checkbox" checked={!!o.smooth} onChange={(e) => onChange({ smooth: e.target.checked })} /></Row>
      )}
      {(cartesian || type === "scatter") && (
        <Row label="Trend line"><input type="checkbox" checked={!!o.trend} onChange={(e) => onChange({ trend: e.target.checked })} /></Row>
      )}
      {cartesian && (
        <div className="space-y-1.5">
          <Row label="Reference line">
            <div className="w-28">
              <Select className="py-1" value={ref?.kind ?? ""} onChange={(e) => setRef(e.target.value ? { kind: e.target.value as RefLine["kind"] } : null)}>
                <option value="">None</option>
                <option value="avg">Average</option>
                <option value="median">Median</option>
                <option value="min">Minimum</option>
                <option value="max">Maximum</option>
                <option value="constant">Fixed value</option>
              </Select>
            </div>
          </Row>
          {ref?.kind === "constant" && (
            <div className="grid grid-cols-2 gap-1.5">
              <Input className="py-1" type="number" placeholder="Value" value={ref.value ?? ""} onChange={(e) => setRef({ value: e.target.value === "" ? undefined : Number(e.target.value) })} />
              <Input className="py-1" placeholder="Label (Target)" value={ref.label ?? ""} onChange={(e) => setRef({ label: e.target.value })} />
            </div>
          )}
        </div>
      )}
      <Row label="Most results">
        <div className="w-28">
          <Select className="py-1" value={cfg.limit ?? 5000} onChange={(e) => onChange({}, Number(e.target.value))}>
            {[100, 1000, 5000, 20000, 50000].map((n) => <option key={n} value={n}>{n.toLocaleString()}</option>)}
          </Select>
        </div>
      </Row>
    </div>
  );
}
