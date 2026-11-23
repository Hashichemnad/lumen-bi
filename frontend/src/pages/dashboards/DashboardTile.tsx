import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ChevronLeft, Download, Eye, GripVertical, Image as ImageIcon, MoreVertical, Pencil, Settings, Trash2 } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import ChartView, { type Annotation, type ChartViewHandle } from "../../components/chart/ChartView";
import type { KpiResult } from "../../lib/governance";
import { KpiLine } from "./Collab";
import type { MarkEvent } from "../../components/chart/EChart";
import Markdown from "../../components/Markdown";
import { Alert } from "../../components/ui";
import { identityPill } from "../../lib/chart/build";
import { formatValue } from "../../lib/chart/format";
import { resolvedType } from "../../lib/chart/showme";
import type { ChartConfig } from "../../lib/chart/types";
import { download, get, post, saveFile } from "../../lib/api";
import { dimensionPills, drilledConfig, markSpecs, nextLevel, type Drill } from "../../lib/dashboard/filters";
import type { Dashboard, DashDataset, Tile } from "../../lib/dashboard/types";
import type { Field, QueryResult } from "../../lib/types";
import { Menu, MenuItem, MenuLine } from "../charts/Shelf";
import { t } from "../../lib/i18n";

export const ROW_HEIGHT = 40;
export const MARGIN = 12;
export const tileHeight = (h: number) => h * ROW_HEIGHT + (h - 1) * MARGIN;

type Spec = Record<string, unknown>;

export interface TileActions {
  /** Pin a note to a point of a chart (an annotation); missing when comments are not available */
  onNote?: (tileId: string, label: string) => void;
  onCross: (tileId: string, label: string, filters: Spec[]) => void;
  onDrill: (tileId: string, drill: Drill | null) => void;
  onViewData: (tileId: string, title: string, filters: Spec[]) => void;
  onEdit: (tile: Tile) => void;
  onRemove: (tile: Tile) => void;
}

function ChartTile({ dash, tile, dataset, filters, params, drill, isCrossSource, actions, height, canExport, annotations }: {
  annotations?: Annotation[];
  dash: Dashboard;
  tile: Tile;
  dataset?: DashDataset;
  filters: Spec[];
  params: Record<string, unknown>;
  drill?: Drill;
  isCrossSource: boolean;
  actions: TileActions;
  height: number;
  canExport: boolean;
}) {
  const chart = dash.charts[String(tile.chart_id)];
  const view = useRef<ChartViewHandle>(null);
  const [mark, setMark] = useState<MarkEvent | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [error, setError] = useState("");
  const cfg: ChartConfig | undefined = useMemo(() => (chart ? drilledConfig(chart.config, drill) : undefined), [chart, drill]);
  const fields = (dataset?.fields ?? []) as unknown as Field[];
  const type = cfg ? resolvedType(cfg, fields) : "table";
  const body = { item: tile.id, filters: [...filters, ...(drill?.filters ?? [])], params, drill: drill?.steps ?? [] };
  const data = useQuery({
    queryKey: ["dash-tile", dash.id, tile.id, JSON.stringify(body), chart?.config],
    queryFn: () => post<QueryResult>(`/dashboards/${dash.id}/query`, body),
    enabled: !!chart,
    placeholderData: (prev) => prev,
    retry: false,
  });
  const identity = cfg ? identityPill(cfg, type) : undefined;
  const identityValues = useQuery({
    queryKey: ["dash-values", dash.id, dataset?.id, identity?.field],
    queryFn: () => get<{ values: unknown[] }>(`/dashboards/${dash.id}/values?dataset=${dataset!.id}&field=${encodeURIComponent(identity!.field)}`),
    enabled: !!identity && !!dataset && !identity.grain && !identity.part && identity.field !== "*",
    staleTime: 5 * 60_000,
  });
  const identityList = useMemo(() => identityValues.data?.values ?? [], [identityValues.data]);

  if (!chart || !cfg) return <div className="p-4 text-sm text-slate-500">{t("This chart was deleted.")}</div>;
  const title = tile.title || cfg.options.title || chart.name;
  const firstDim = dimensionPills(cfg)[0];
  const next = firstDim && dataset ? nextLevel(firstDim, dataset) : null;
  const row = mark && data.data ? data.data.rows[mark.row] : null;
  const markInfo = row && data.data ? markSpecs(cfg, data.data.columns, row) : null;
  const firstDimIndex = data.data && firstDim ? data.data.columns.findIndex((c) => c.key === firstDim.id) : -1;

  const drillDown = () => {
    if (!next || !row || firstDimIndex < 0 || !firstDim) return;
    const value = row[firstDimIndex];
    const col = data.data!.columns[firstDimIndex];
    const base = { field: firstDim.field, ...(firstDim.grain ? { grain: firstDim.grain } : {}) };
    actions.onDrill(tile.id, {
      steps: [...(drill?.steps ?? []), { key: firstDim.id, field: next.field, grain: next.grain }],
      filters: [...(drill?.filters ?? []), value === null ? { ...base, op: "is_null" } : { ...base, op: "eq", value }],
      path: [...(drill?.path ?? []), value === null ? "(empty)" : formatValue(value, col)],
    });
    setMark(null);
  };
  const drillUp = () => {
    if (!drill) return;
    const n = drill.steps.length - 1;
    actions.onDrill(tile.id, n > 0 ? { steps: drill.steps.slice(0, n), filters: drill.filters.slice(0, n), path: drill.path.slice(0, n) } : null);
  };
  const exportCsv = async (rows: boolean, format: "csv" | "xlsx" = "csv") => {
    setMenu(null);
    try {
      await download(`/dashboards/${dash.id}/export`, { ...body, rows, format }, `${chart.name}.${format}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Export failed"));
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="tile-handle flex items-center gap-1.5 px-3 pb-1 pt-2.5">
        {drill && (
          <button onClick={drillUp} className="no-drag rounded p-0.5 text-slate-500 hover:bg-slate-100" title={t("Back up")} aria-label={t("Back up")}><ChevronLeft size={16} /></button>
        )}
        {tile.show_title !== false && <h3 className="min-w-0 truncate text-sm font-semibold text-slate-900" title={title}>{title}</h3>}
        {drill && <span className="truncate text-xs text-slate-500">· {drill.path.join(" › ")}</span>}
        {isCrossSource && <span className="rounded bg-brand-50 px-1.5 py-0.5 text-[10px] font-medium text-brand-700">{t("Filtering others")}</span>}
        <button onClick={(e) => setMenu({ x: e.clientX - 200, y: e.clientY + 8 })} className="no-drag print:hidden ms-auto rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label={t("Options for {title}", { title })}>
          <MoreVertical size={15} />
        </button>
      </div>
      <div className="min-h-0 flex-1 px-3 pb-3">
        {error && <Alert>{error}</Alert>}
        {data.error ? (
          <p className="p-3 text-sm text-red-700">{(data.error as Error).message}</p>
        ) : data.data ? (
          <ChartView ref={view} cfg={cfg} type={type} result={data.data} identity={identityList} height={Math.max(80, height)}
            stale={data.isFetching} onMark={(e) => setMark(e)} annotations={annotations} />
        ) : (
          <div data-state="loading" className="flex h-full items-center justify-center text-sm text-slate-400">{t("Loading…")}</div>
        )}
      </div>
      {mark && row && (
        <Menu anchor={{ x: mark.x, y: mark.y }} onClose={() => setMark(null)}>
          <div className="px-3 py-1.5 text-xs font-medium text-slate-500">{markInfo?.label || t("This mark")}</div>
          <MenuLine />
          {dash.config.options.cross_filter && markInfo && markInfo.specs.length > 0 && (
            <MenuItem onClick={() => { actions.onCross(tile.id, markInfo.label, markInfo.specs); setMark(null); }}>{t("Filter the other charts")}</MenuItem>
          )}
          {next && <MenuItem onClick={drillDown}>{t("Drill down to {label}", { label: next.label })}</MenuItem>}
          {(dash.config.options.view_data || dash.can_edit) && markInfo && (
            <MenuItem onClick={() => { actions.onViewData(tile.id, t("Data for {label}", { label: markInfo.label }), [...body.filters, ...markInfo.specs]); setMark(null); }}>{t("View data")}</MenuItem>
          )}
          {actions.onNote && firstDimIndex >= 0 && (
            <MenuItem onClick={() => { actions.onNote!(tile.id, formatValue(row[firstDimIndex], data.data!.columns[firstDimIndex])); setMark(null); }}>{t("Add a note here")}</MenuItem>
          )}
        </Menu>
      )}
      {menu && (
        <Menu anchor={menu} onClose={() => setMenu(null)}>
          {(dash.config.options.view_data || dash.can_edit) && (
            <MenuItem onClick={() => { actions.onViewData(tile.id, t("Data behind {title}", { title }), body.filters); setMenu(null); }}><Eye size={14} /> {t("View data")}</MenuItem>
          )}
          {canExport && <MenuItem onClick={() => exportCsv(false)}><Download size={14} /> {t("Export as CSV")}</MenuItem>}
          {canExport && <MenuItem onClick={() => exportCsv(false, "xlsx")}><Download size={14} /> {t("Export as Excel")}</MenuItem>}
          {!["table", "kpi", "rating"].includes(type) && (
            <MenuItem onClick={() => { const url = view.current?.png(); if (url) saveFile(url, `${title}.png`); setMenu(null); }}><ImageIcon size={14} /> {t("Save as image")}</MenuItem>
          )}
          {chart.can_edit && (
            <Link to={`/charts/${chart.id}`} className="flex items-center gap-2 px-3 py-1.5 hover:bg-slate-100"><span className="w-4" /><Pencil size={14} /> {t("Open in chart builder")}</Link>
          )}
        </Menu>
      )}
    </div>
  );
}

export default function DashboardTile(props: {
  dash: Dashboard;
  tile: Tile;
  dataset?: DashDataset;
  filters: Spec[];
  params: Record<string, unknown>;
  drill?: Drill;
  isCrossSource: boolean;
  actions: TileActions;
  editing: boolean;
  heightPx: number;
  canExport: boolean;
  kpi?: KpiResult;
  annotations?: Annotation[];
}) {
  const { tile, editing, actions, heightPx, kpi } = props;
  const chartHeight = heightPx - 44;
  return (
    <div data-tile={tile.id} data-section={tile.kind === "section" ? tile.id : undefined}
      className={clsx("group relative h-full overflow-hidden rounded-xl border bg-white shadow-sm", editing ? "border-dashed border-slate-300" : "border-slate-200")}>
      {editing && (
        <div className="absolute end-2 top-2 z-10 flex gap-1 rounded-md bg-white/90 p-0.5 opacity-0 shadow-sm ring-1 ring-slate-200 transition-opacity group-hover:opacity-100">
          <span className="tile-handle cursor-grab rounded p-1 text-slate-500 hover:bg-slate-100 active:cursor-grabbing" title={t("Drag to move")}><GripVertical size={14} /></span>
          <button onClick={() => actions.onEdit(tile)} className="no-drag rounded p-1 text-slate-500 hover:bg-slate-100" aria-label={t("Tile settings")}><Settings size={14} /></button>
          <button onClick={() => actions.onRemove(tile)} className="no-drag rounded p-1 text-slate-500 hover:bg-red-50 hover:text-red-600" aria-label={t("Remove tile")}><Trash2 size={14} /></button>
        </div>
      )}
      {tile.kind === "chart" && <ChartTile {...props} height={chartHeight} />}
      {tile.kind === "section" && (
        <div className="tile-handle flex h-full items-center gap-4 px-5">
          {tile.code && <span className="section-code shrink-0 rounded-lg bg-brand-50 px-2.5 py-1 font-display text-xs font-bold tracking-wider text-brand-700">{tile.code}</span>}
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-lg font-bold text-slate-900">{tile.title || t("Section")}</h2>
            {tile.text && <p className="truncate text-sm text-slate-500">{tile.text}</p>}
          </div>
          {kpi && <div className="no-drag ms-auto shrink-0"><KpiLine k={kpi} /></div>}
        </div>
      )}
      {tile.kind === "text" && (
        <div className="tile-handle h-full overflow-auto p-4">
          {tile.text ? <Markdown text={tile.text} /> : <p className="text-sm text-slate-400">{t("Empty text. Use the settings to write.")}</p>}
        </div>
      )}
      {tile.kind === "image" && (
        tile.url
          ? <img src={tile.url} alt={tile.alt ?? ""} referrerPolicy="no-referrer" className={clsx("h-full w-full", tile.fit === "cover" ? "object-cover" : "object-contain p-2")} draggable={false} />
          : <p className="p-4 text-sm text-slate-400">{t("No image address yet.")}</p>
      )}
      {tile.kind === "web" && (
        tile.url
          ? <iframe src={tile.url} title={tile.title || t("Web page")} className={clsx("h-full w-full", editing && "pointer-events-none")}
              sandbox="allow-scripts allow-same-origin allow-popups allow-forms" referrerPolicy="no-referrer" loading="lazy" />
          : <p className="p-4 text-sm text-slate-400">{t("No web address yet.")}</p>
      )}
    </div>
  );
}
