import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  AreaChart, ArrowDownWideNarrow, BarChart3, BarChartHorizontal, CalendarDays, ChartBarStacked, ChartColumnStacked, ChartGantt,
  ChartNoAxesGantt, CircleDot, CircleGauge, Ellipsis, Gauge, Grid3x3, Hash, Layers, LayoutGrid, LineChart, Map, MapPinned,
  Origami, PieChart, Radar, Rows3, ScatterChart, Sparkles, Spline, Star, Table2, Timer, Triangle, Waypoints, Workflow,
} from "lucide-react";
import type { ReactNode } from "react";

import { Input, Select } from "../../components/ui";
import { get } from "../../lib/api";
import { CHART_GROUPS, CHART_KINDS } from "../../lib/chart/showme";
import type { ChartConfig, ChartOptions, ChartType, RefLine } from "../../lib/chart/types";
import { t } from "../../lib/i18n";

const ICONS: Record<ChartType, ReactNode> = {
  table: <Table2 size={18} />, kpi: <Hash size={18} />, bar: <BarChart3 size={18} />, stacked: <Layers size={18} />,
  line: <LineChart size={18} />, area: <AreaChart size={18} />, combo: <BarChartHorizontal size={18} />,
  scatter: <ScatterChart size={18} />, heatmap: <Grid3x3 size={18} />, treemap: <LayoutGrid size={18} />,
  donut: <PieChart size={18} />, map: <Map size={18} />,
  rating: <Star size={18} />, pyramid: <Triangle size={18} />, stacked_pyramid: <ChartBarStacked size={18} />,
  parallel: <Spline size={18} />, radar: <Radar size={18} />, timeline: <Timer size={18} />, time_bar: <ChartColumnStacked size={18} />,
  time_combo: <ChartNoAxesGantt size={18} />, calendar: <CalendarDays size={18} />, gantt: <ChartGantt size={18} />,
  gauge: <Gauge size={18} />, polar_gauge: <CircleGauge size={18} />, flat_gauge: <Rows3 size={18} />, bullet: <Ellipsis size={18} />,
  dot_h: <CircleDot size={18} />, dot_v: <ScatterChart size={18} />, waterfall: <Waypoints size={18} />,
  stacked_waterfall: <Layers size={18} />, funnel: <ArrowDownWideNarrow size={18} />, origami: <Origami size={18} />,
  sankey: <Workflow size={18} />, geomap: <MapPinned size={18} />,
};

export function ShowMe({ cfg, resolved, available, onPick }: {
  cfg: ChartConfig; resolved: ChartType; available: Set<ChartType>; onPick: (t: ChartType | "auto") => void;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("Show me")}</h3>
        <button onClick={() => onPick("auto")}
          className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs", cfg.type === "auto" ? "bg-brand-50 text-brand-700" : "text-slate-500 hover:bg-slate-100")}>
          <Sparkles size={12} /> {t("Automatic")}
        </button>
      </div>
      <div className="space-y-2">
        {CHART_GROUPS.map((g) => (
          <div key={g}>
            <div className="mb-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-500">{t(g)}</div>
            <div className="grid grid-cols-4 gap-1">
              {CHART_KINDS.filter((k) => k.group === g).map((k) => {
                const ok = available.has(k.type);
                return (
                  <button key={k.type} disabled={!ok} onClick={() => onPick(k.type)} title={`${t(k.label)}: ${t(k.needs)}`} aria-label={t(k.label)}
                    className={clsx("flex aspect-square flex-col items-center justify-center gap-0.5 rounded-md border text-[10px]",
                      resolved === k.type ? "border-brand-600 bg-brand-50 text-brand-700" : "border-transparent text-slate-600 hover:bg-slate-100",
                      !ok && "cursor-not-allowed opacity-30")}>
                    {ICONS[k.type]}
                    <span className="line-clamp-2 leading-tight">{t(k.label)}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-500">{t(CHART_KINDS.find((k) => k.type === resolved)?.needs ?? "")}</p>
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

function BoundaryPicker({ value, onChange }: { value?: number; onChange: (v?: number) => void }) {
  const list = useQuery({ queryKey: ["boundaries"], queryFn: () => get<{ id: number; name: string; features: number }[]>("/boundaries") });
  return (
    <div>
      <span className="mb-1 block text-sm text-slate-600">{t("Map")}</span>
      <Select className="py-1.5" value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}>
        <option value="">{t("Choose a map")}</option>
        {list.data?.map((b) => <option key={b.id} value={b.id}>{t("{name} ({features} areas)", { name: b.name, features: b.features })}</option>)}
      </Select>
      {list.data && !list.data.length && <p className="mt-1 text-xs text-slate-500">{t("No maps yet. Creators upload them under Data → Maps.")}</p>}
    </div>
  );
}

export function OptionsPanel({ cfg, type, onChange }: { cfg: ChartConfig; type: ChartType; onChange: (o: Partial<ChartOptions>, limit?: number) => void }) {
  const o = cfg.options;
  const cartesian = ["bar", "stacked", "line", "area", "combo"].includes(type);
  const ref = o.refLine;
  const setRef = (r: Partial<RefLine> | null) => onChange({ refLine: r === null ? null : ({ ...(ref ?? { kind: "avg" }), ...r } as RefLine) });
  return (
    <div className="space-y-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("Options")}</h3>
      <div>
        <span className="mb-1 block text-sm text-slate-600">{t("Title")}</span>
        <Input className="py-1.5" value={o.title ?? ""} placeholder={t("Automatic")} onChange={(e) => onChange({ title: e.target.value })} />
      </div>
      {!["table", "kpi", "rating", "gauge", "polar_gauge", "flat_gauge"].includes(type) && (
        <Row label={t("Value labels")}>
          <div className="w-28">
            <Select className="py-1" value={o.labels ?? "none"} onChange={(e) => onChange({ labels: e.target.value as ChartOptions["labels"] })}>
              <option value="none">{t("None")}</option>
              {type === "line" || type === "area" ? <option value="ends">{t("At line ends")}</option> : null}
              <option value="all">{t("On marks")}</option>
            </Select>
          </div>
        </Row>
      )}
      {!["table", "kpi", "rating", "heatmap", "gauge", "polar_gauge", "flat_gauge", "funnel", "calendar"].includes(type) && (
        <Row label={t("Legend")}><input type="checkbox" checked={o.legend !== false} onChange={(e) => onChange({ legend: e.target.checked })} /></Row>
      )}
      {type === "table" && (
        <>
          <Row label={t("Grand totals")}><input type="checkbox" checked={!!o.totals} onChange={(e) => onChange({ totals: e.target.checked })} /></Row>
          <Row label={t("Shade values")}><input type="checkbox" checked={!!o.colorScale} onChange={(e) => onChange({ colorScale: e.target.checked })} /></Row>
        </>
      )}
      {type === "stacked" && (
        <Row label={t("100% stacked")}><input type="checkbox" checked={!!o.percentStack} onChange={(e) => onChange({ percentStack: e.target.checked })} /></Row>
      )}
      {(type === "line" || type === "area") && (
        <Row label={t("Smooth lines")}><input type="checkbox" checked={!!o.smooth} onChange={(e) => onChange({ smooth: e.target.checked })} /></Row>
      )}
      {(type === "line" || type === "area") && (
        <div className="space-y-1.5">
          <Row label={t("Forecast")}>
            <input type="checkbox" checked={!!o.forecast} onChange={(e) => onChange({ forecast: e.target.checked ? { periods: 6, interval: 0.95 } : null })} />
          </Row>
          {o.forecast && (
            <div className="grid grid-cols-2 gap-1.5">
              <label className="text-xs text-slate-500">
                {t("Periods ahead")}
                <Input className="mt-0.5 py-1" type="number" min={1} max={120} value={o.forecast.periods}
                  onChange={(e) => onChange({ forecast: { ...o.forecast!, periods: Math.max(1, Math.min(120, Number(e.target.value) || 1)) } })} />
              </label>
              <label className="text-xs text-slate-500">
                {t("Likely range")}
                <Select className="mt-0.5 py-1 ps-2 pe-7" value={o.forecast.interval}
                  onChange={(e) => onChange({ forecast: { ...o.forecast!, interval: Number(e.target.value) as 0.8 | 0.9 | 0.95 | 0.99 } })}>
                  {[0.8, 0.9, 0.95, 0.99].map((v) => <option key={v} value={v}>{Math.round(v * 100)}%</option>)}
                </Select>
              </label>
              <p className="col-span-2 text-xs text-slate-500">{t("Needs a date at a level such as month. Calculated on the server from past values; no AI.")}</p>
            </div>
          )}
        </div>
      )}
      {(cartesian || type === "scatter") && (
        <Row label={t("Trend line")}><input type="checkbox" checked={!!o.trend} onChange={(e) => onChange({ trend: e.target.checked })} /></Row>
      )}
      {cartesian && (
        <div className="space-y-1.5">
          <Row label={t("Reference line")}>
            <div className="w-28">
              <Select className="py-1" value={ref?.kind ?? ""} onChange={(e) => setRef(e.target.value ? { kind: e.target.value as RefLine["kind"] } : null)}>
                <option value="">{t("None")}</option>
                <option value="avg">{t("Average")}</option>
                <option value="median">{t("Median")}</option>
                <option value="min">{t("Minimum")}</option>
                <option value="max">{t("Maximum")}</option>
                <option value="constant">{t("Fixed value")}</option>
              </Select>
            </div>
          </Row>
          {ref?.kind === "constant" && (
            <div className="grid grid-cols-2 gap-1.5">
              <Input className="py-1" type="number" placeholder={t("Value")} value={ref.value ?? ""} onChange={(e) => setRef({ value: e.target.value === "" ? undefined : Number(e.target.value) })} />
              <Input className="py-1" placeholder={t("Label (Target)")} value={ref.label ?? ""} onChange={(e) => setRef({ label: e.target.value })} />
            </div>
          )}
        </div>
      )}
      {["gauge", "polar_gauge", "flat_gauge"].includes(type) && (
        <Row label={t("Maximum")}>
          <Input className="w-28 py-1" type="number" placeholder={t("Auto / target")} value={o.gaugeMax ?? ""} onChange={(e) => onChange({ gaugeMax: e.target.value === "" ? undefined : Number(e.target.value) })} />
        </Row>
      )}
      {type === "rating" && (
        <Row label={t("Full marks at")}>
          <Input className="w-28 py-1" type="number" value={o.ratingMax ?? 5} onChange={(e) => onChange({ ratingMax: Number(e.target.value) || 5 })} />
        </Row>
      )}
      {["waterfall", "stacked_waterfall", "origami"].includes(type) && (
        <Row label={t("Show the total")}><input type="checkbox" checked={o.showTotal !== false} onChange={(e) => onChange({ showTotal: e.target.checked })} /></Row>
      )}
      {["funnel", "origami"].includes(type) && (
        <Row label={t("Order")}>
          <div className="w-28">
            <Select className="py-1" value={o.order ?? "value"} onChange={(e) => onChange({ order: e.target.value as "value" | "data" })}>
              <option value="value">{t("By value")}</option>
              <option value="data">{t("As sorted")}</option>
            </Select>
          </div>
        </Row>
      )}
      {type === "geomap" && <BoundaryPicker value={o.boundary} onChange={(boundary) => onChange({ boundary })} />}
      <Row label={t("Most results")}>
        <div className="w-28">
          <Select className="py-1" value={cfg.limit ?? 5000} onChange={(e) => onChange({}, Number(e.target.value))}>
            {[100, 1000, 5000, 20000, 50000].map((n) => <option key={n} value={n}>{n.toLocaleString()}</option>)}
          </Select>
        </div>
      </Row>
    </div>
  );
}
