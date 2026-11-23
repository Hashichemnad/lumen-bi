import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeftRight, Copy, Download, Eraser, Eye, Filter, Image, Redo2, Save, Undo2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import ChartView, { type ChartViewHandle } from "../../components/chart/ChartView";
import type { MarkEvent } from "../../components/chart/EChart";
import { Alert, Button, Input } from "../../components/ui";
import { identityPill } from "../../lib/chart/build";
import { formatValue } from "../../lib/chart/format";
import { buildQuery, dimensionsOf, markFilters, measuresOf, underlyingQuery } from "../../lib/chart/query";
import { available, resolvedType } from "../../lib/chart/showme";
import type { Catalog, ChartConfig, ChartOptions, ChartType, FilterPill, Pill, SavedChart, ShelfName } from "../../lib/chart/types";
import { emptyConfig, newId, SHELVES } from "../../lib/chart/types";
import { download, get, post, put, saveFile } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { useWorkspace } from "../../lib/workspace";
import type { Field, QueryResult } from "../../lib/types";
import FieldPanel from "./FieldPanel";
import FilterModal from "./FilterModal";
import { drillDown, drillUp, fieldMap, fieldToPill, autoShelf, filterSummary, findPill, pillText, SHELF_LABEL, updatePill, withoutDrillFilters } from "./pills";
import { Menu, MenuItem, MenuLine, Shelf, type DragPayload, type PillActions } from "./Shelf";
import { OptionsPanel, ShowMe } from "./SidePanel";
import ViewDataModal from "./ViewDataModal";
import { t } from "../../lib/i18n";

const SINGLE: ShelfName[] = ["color", "size"];

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function autoTitle(cfg: ChartConfig, fields: Map<string, Field>): string {
  const m = measuresOf(cfg).map((p) => pillText(p, fields));
  const d = dimensionsOf(cfg).map((p) => pillText(p, fields));
  if (!m.length && !d.length) return "New chart";
  if (!m.length) return d.join(", ");
  return d.length ? `${m.join(", ")} by ${d.join(", ")}` : m.join(", ");
}

export default function ChartBuilder() {
  const { id } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { hasRole } = useAuth();
  const { target } = useWorkspace();
  const [exportMenu, setExportMenu] = useState<{ x: number; y: number } | null>(null);
  const isNew = id === "new";

  const saved = useQuery({ queryKey: ["chart", id], queryFn: () => get<SavedChart>(`/charts/${id}`), enabled: !isNew });
  const datasetId = isNew ? Number(search.get("dataset")) : saved.data?.dataset_id;
  const catalog = useQuery({
    queryKey: ["catalog", datasetId],
    queryFn: () => get<Catalog>(`/catalog/datasets/${datasetId}`),
    enabled: !!datasetId,
  });

  // ---------------------------------------------------------------- config with undo/redo
  const [cfg, setCfgRaw] = useState<ChartConfig>(emptyConfig);
  const [name, setName] = useState("");
  const [dirty, setDirty] = useState(false);
  const past = useRef<ChartConfig[]>([]);
  const future = useRef<ChartConfig[]>([]);
  const loaded = useRef<string | null>(null);
  useEffect(() => {
    if (saved.data && loaded.current !== `${saved.data.id}`) {
      loaded.current = `${saved.data.id}`;
      setCfgRaw({ ...emptyConfig(), ...saved.data.config, shelves: { ...emptyConfig().shelves, ...saved.data.config.shelves } });
      setName(saved.data.name);
      past.current = [];
      future.current = [];
      setDirty(false);
    }
  }, [saved.data]);
  const setCfg = useCallback((next: ChartConfig | ((c: ChartConfig) => ChartConfig)) => {
    setCfgRaw((current) => {
      const value = typeof next === "function" ? next(current) : next;
      if (value === current) return current;
      past.current = [...past.current.slice(-50), current];
      future.current = [];
      return value;
    });
    setDirty(true);
  }, []);
  const undo = () => {
    const prev = past.current.pop();
    if (!prev) return;
    future.current.push(cfg);
    setCfgRaw(prev);
    setDirty(true);
  };
  const redo = () => {
    const next = future.current.pop();
    if (!next) return;
    past.current.push(cfg);
    setCfgRaw(next);
    setDirty(true);
  };
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => dirty && e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const fields = useMemo(() => fieldMap(catalog.data), [catalog.data]);
  const fieldList = catalog.data?.fields ?? [];
  const type: ChartType = resolvedType(cfg, fieldList);
  const avail = available(cfg, fieldList);

  // ---------------------------------------------------------------- data
  const debounced = useDebounced(cfg, 250);
  const debouncedType = resolvedType(debounced, fieldList);
  const spec = useMemo(() => buildQuery(debounced, debouncedType), [debounced, debouncedType]);
  const hasFields = dimensionsOf(debounced).length + measuresOf(debounced).length > 0;
  // Each result keeps the settings that produced it, so a previous result shown while the next one loads is
  // always drawn with its own settings
  const result = useQuery({
    queryKey: ["chart-data", datasetId, JSON.stringify(spec)],
    queryFn: async () => ({
      result: await post<QueryResult>("/query", { dataset: datasetId, ...spec }),
      cfg: debounced,
      type: debouncedType,
      spec: JSON.stringify(spec),
    }),
    enabled: !!datasetId && !!catalog.data && hasFields,
    placeholderData: (prev) => prev,
    retry: false,
  });
  const data = { ...result, data: result.data?.result };
  // Same query (only the chart type or options changed): draw it with the current settings at once
  const shown = result.data && result.data.spec === JSON.stringify(spec) ? { ...result.data, cfg: debounced, type: debouncedType } : result.data;
  const identity = shown ? identityPill(shown.cfg, shown.type) : undefined;
  const identityValues = useQuery({
    queryKey: ["values", datasetId, identity?.field, "all"],
    queryFn: () => get<{ values: unknown[] }>(`/datasets/${datasetId}/values?field=${encodeURIComponent(identity!.field)}&order=value&limit=1000`),
    enabled: !!identity && !identity.grain && !identity.part && identity.field !== "*",
    staleTime: 5 * 60_000,
  });
  const identityList = useMemo(() => identityValues.data?.values ?? [], [identityValues.data]);

  // ---------------------------------------------------------------- editing
  const [filterEdit, setFilterEdit] = useState<{ field: string; existing?: FilterPill } | null>(null);
  const [mark, setMark] = useState<MarkEvent | null>(null);
  const [viewData, setViewData] = useState<{ spec: Record<string, unknown>; title: string } | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const view = useRef<ChartViewHandle>(null);

  const addPill = (pill: Pill, shelf: ShelfName, index?: number) =>
    setCfg((c) => {
      const list = SINGLE.includes(shelf) ? [] : [...c.shelves[shelf]];
      list.splice(index ?? list.length, 0, pill);
      return { ...c, shelves: { ...c.shelves, [shelf]: list } };
    });
  const addField = (f: Field | "*", role?: "dimension" | "measure") => {
    const pill = fieldToPill(f, role);
    addPill(pill, autoShelf(cfg, pill));
  };
  const onDrop = (shelf: ShelfName | "filters", payload: DragPayload, index: number) => {
    if (shelf === "filters") {
      const field = payload.from === "field" ? payload.field : findPill(cfg, payload.id)?.pill.field;
      if (field && field !== "*") setFilterEdit({ field });
      return;
    }
    if (payload.from === "field") {
      const f = payload.field === "*" ? "*" : fields.get(payload.field);
      if (f) addPill(fieldToPill(f, payload.field === "*" ? "measure" : payload.role), shelf, index);
      return;
    }
    setCfg((c) => {
      const found = findPill(c, payload.id);
      if (!found) return c;
      const shelves = { ...c.shelves };
      for (const s of SHELVES) shelves[s] = c.shelves[s].filter((p) => p.id !== payload.id);
      const target = SINGLE.includes(shelf) ? [] : [...shelves[shelf]];
      const at = found.shelf === shelf && found.index < index ? index - 1 : index;
      target.splice(Math.min(at, target.length), 0, found.pill);
      shelves[shelf] = target;
      return { ...c, shelves };
    });
  };
  const actions: PillActions = {
    change: (pid, next) => setCfg((c) => updatePill(c, pid, () => next)),
    drill: (pid, dir) =>
      setCfg((c) => {
        const found = findPill(c, pid);
        if (!found) return c;
        const step = dir === "down" ? drillDown(found.pill, catalog.data) : drillUp(found.pill, catalog.data);
        if (!step) return c;
        const next = updatePill(c, pid, () => step.pill);
        return dir === "up" ? { ...next, filters: withoutDrillFilters(next.filters, step.pill) } : next;
      }),
    sort: (pid, dir) => setCfg((c) => ({ ...c, sort: dir ? { pill: pid, dir } : null })),
    filter: (field) => setFilterEdit({ field, existing: cfg.filters.find((f) => f.field === field && !f.id.startsWith("drill")) }),
  };
  const saveFilter = (f: FilterPill) =>
    setCfg((c) => ({ ...c, filters: c.filters.some((x) => x.id === f.id) ? c.filters.map((x) => (x.id === f.id ? f : x)) : [...c.filters, f] }));
  const setOptions = (o: Partial<ChartOptions>, limit?: number) => setCfg((c) => ({ ...c, options: { ...c.options, ...o }, ...(limit ? { limit } : {}) }));
  const swap = () => setCfg((c) => ({ ...c, shelves: { ...c.shelves, columns: c.shelves.rows, rows: c.shelves.columns } }));
  const sortByColumn = (key: string) =>
    setCfg((c) => ({ ...c, sort: c.sort?.pill === key ? (c.sort.dir === "asc" ? { pill: key, dir: "desc" } : null) : { pill: key, dir: "asc" } }));

  // ---------------------------------------------------------------- marks: keep only, exclude, drill, view data
  const markRow = mark && data.data ? data.data.rows[mark.row] : null;
  const markDims = markRow && data.data ? data.data.columns.map((c, i) => [c, markRow[i]] as const).filter(([c]) => c.role === "dimension") : [];
  const markTitle = markDims.map(([c, v]) => (v === null ? "(empty)" : formatValue(v, c))).join(" · ");
  const pillOf = (key: string) => dimensionsOf(shown?.cfg ?? cfg).find((p) => p.id === key);
  const keepOnly = (exclude: boolean) => {
    setCfg((c) => {
      const extra: FilterPill[] = [];
      const dims = exclude ? markDims.slice(0, 1) : markDims;
      for (const [col, v] of dims) {
        const p = pillOf(col.key);
        if (p) extra.push({ id: newId("f"), field: p.field, kind: "values", values: [v], exclude, grain: p.grain, part: p.part });
      }
      return { ...c, filters: [...c.filters, ...extra] };
    });
    setMark(null);
  };
  const firstCat = markDims.length ? pillOf(markDims[0][0].key) : undefined;
  const markDrill = firstCat ? drillDown(firstCat, catalog.data) : null;
  const drillMark = () => {
    if (!firstCat || !markDrill) return;
    const value = markDims[0][1];
    setCfg((c) => {
      const next = updatePill(c, firstCat.id, () => markDrill.pill);
      return { ...next, filters: [...next.filters, { id: newId("drill"), field: firstCat.field, kind: "values", values: [value], grain: firstCat.grain, part: firstCat.part }] };
    });
    setMark(null);
  };

  // ---------------------------------------------------------------- saving and exporting
  const title = cfg.options.title || autoTitle(cfg, fields);
  const save = async (asCopy = false) => {
    setError("");
    setSaving(true);
    const body = { name: name.trim() || title, dataset_id: datasetId, config: cfg, query: buildQuery(cfg, type), description: saved.data?.description ?? "" };
    try {
      let chart: SavedChart;
      if (asCopy && !isNew) {
        chart = await post<SavedChart>(`/charts/${id}/copy`);
        chart = await put<SavedChart>(`/charts/${chart.id}`, { ...body, name: `${body.name} (copy)` });
      } else if (isNew) chart = await post<SavedChart>("/charts", { ...body, workspace_id: target });
      else chart = await put<SavedChart>(`/charts/${id}`, body);
      qc.setQueryData(["chart", String(chart.id)], chart);
      await qc.invalidateQueries({ queryKey: ["charts"] });
      setDirty(false);
      setName(chart.name);
      if (isNew || asCopy) {
        loaded.current = String(chart.id);
        navigate(`/charts/${chart.id}`, { replace: isNew });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save the chart"));
    } finally {
      setSaving(false);
    }
  };
  const exportCsv = async (format: "csv" | "xlsx" = "csv") => {
    try {
      await download("/query/export", { dataset: datasetId, ...buildQuery(cfg, type), limit: undefined, format }, `chart.${format}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Export failed"));
    }
  };
  const exportPng = () => {
    const url = view.current?.png();
    if (url) saveFile(url, `${(name || title).replace(/[^\w\- ]+/g, "").trim() || "chart"}.png`);
  };

  if (!datasetId && !isNew && saved.error) return <div className="p-6"><Alert>{(saved.error as Error).message}</Alert></div>;
  if (isNew && !datasetId) return <div className="p-6"><Alert>{t("Choose a dataset first.")} <Link className="underline" to="/charts">{t("Back to charts")}</Link></Alert></div>;
  if (catalog.error) return <div className="p-6"><Alert>{(catalog.error as Error).message}</Alert></div>;
  if (!catalog.data) return <div className="p-6 text-sm text-slate-500">{t("Loading…")}</div>;

  const shelfProps = { fields, catalog: catalog.data, sort: cfg.sort, actions };
  const queryError = data.error ? (data.error as Error).message : "";

  return (
    <div className="grid h-full grid-cols-[230px_200px_minmax(0,1fr)_210px] bg-slate-50">
      <FieldPanel catalog={catalog.data} params={cfg.params}
        onParam={(n, v) => setCfg((c) => ({ ...c, params: { ...c.params, [n]: v } }))}
        onAdd={addField} onRemovePill={(pid) => actions.change(pid, null)} />

      {/* Filters and marks */}
      <div className="min-h-0 space-y-4 overflow-y-auto border-e border-slate-200 p-3">
        <div onDragOver={(e) => e.preventDefault()} onDrop={(e) => {
          e.preventDefault();
          try {
            const p = JSON.parse(e.dataTransfer.getData("application/x-lumen")) as DragPayload;
            onDrop("filters", p, 0);
          } catch { /* not ours */ }
        }}>
          <h3 className="mb-1.5 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500"><Filter size={12} /> {t("Filters")}</h3>
          <div className="min-h-12 space-y-1 rounded-lg border border-dashed border-slate-300 bg-white p-1.5">
            {cfg.filters.map((f) => (
              <div key={f.id} className="flex items-center gap-1 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-900">
                <button className="min-w-0 flex-1 truncate text-start" title={filterSummary(f, fields)}
                  onClick={() => setFilterEdit({ field: f.field, existing: f })}>{filterSummary(f, fields)}</button>
                <button onClick={() => setCfg((c) => ({ ...c, filters: c.filters.filter((x) => x.id !== f.id) }))} aria-label={t("Remove filter")}><X size={12} /></button>
              </div>
            ))}
            {!cfg.filters.length && <p className="px-1 py-1.5 text-xs text-slate-400">{t("Drop fields here to filter")}</p>}
          </div>
        </div>
        <div>
          <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">{t("Marks")}</h3>
          <div className="space-y-1.5">
            {(["color", "size", "label", "tooltip", "detail"] as ShelfName[]).map((s) => (
              <Shelf key={s} name={s} label={t(SHELF_LABEL[s])} pills={cfg.shelves[s]} vertical {...shelfProps}
                onDrop={(p, i) => onDrop(s, p, i)} hint={s === "color" ? t("Colour by a field") : undefined} />
            ))}
          </div>
        </div>
      </div>

      {/* Main */}
      <div className="flex min-h-0 min-w-0 flex-col">
        <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-4 py-2">
          <Input value={name} onChange={(e) => { setName(e.target.value); setDirty(true); }} placeholder={title} className="min-w-0 max-w-sm flex-1 py-1.5 font-display font-semibold" aria-label={t("Chart name")} />
          {dirty && <span className="text-xs text-slate-400">{t("Unsaved")}</span>}
          <div className="ms-auto flex flex-wrap items-center justify-end gap-1">
            <button className="rounded p-1.5 text-slate-500 hover:bg-slate-100 disabled:opacity-30" onClick={undo} disabled={!past.current.length} title={t("Undo")} aria-label={t("Undo")}><Undo2 size={16} /></button>
            <button className="rounded p-1.5 text-slate-500 hover:bg-slate-100 disabled:opacity-30" onClick={redo} disabled={!future.current.length} title={t("Redo")} aria-label={t("Redo")}><Redo2 size={16} /></button>
            <button className="rounded p-1.5 text-slate-500 hover:bg-slate-100" onClick={swap} title={t("Swap rows and columns")} aria-label={t("Swap rows and columns")}><ArrowLeftRight size={16} /></button>
            <button className="rounded p-1.5 text-slate-500 hover:bg-slate-100" onClick={() => setCfg({ ...emptyConfig(), params: cfg.params })} title={t("Clear")} aria-label={t("Clear")}><Eraser size={16} /></button>
            <span className="mx-1 h-5 w-px bg-slate-200" />
            <Button size="sm" variant="secondary" disabled={!data.data} onClick={() => setViewData({ spec: underlyingQuery(cfg), title: "Data behind this chart" })}><Eye size={14} /> {t("View data")}</Button>
            <Button size="sm" variant="secondary" disabled={!data.data} onClick={(e) => setExportMenu({ x: e.clientX - 160, y: e.clientY + 12 })} aria-haspopup="menu"><Download size={14} /> {t("Export")}</Button>
            {exportMenu && (
              <Menu anchor={exportMenu} onClose={() => setExportMenu(null)}>
                {hasRole("explorer") && <MenuItem onClick={() => { setExportMenu(null); exportCsv("csv"); }}><Download size={14} /> {t("CSV")}</MenuItem>}
                {hasRole("explorer") && <MenuItem onClick={() => { setExportMenu(null); exportCsv("xlsx"); }}><Download size={14} /> {t("Excel")}</MenuItem>}
                {!["table", "kpi", "rating"].includes(type) && <MenuItem onClick={() => { setExportMenu(null); exportPng(); }}><Image size={14} /> {t("PNG")}</MenuItem>}
              </Menu>
            )}
            {!isNew && <Button size="sm" variant="secondary" onClick={() => save(true)} disabled={saving}><Copy size={14} /> {t("Save copy")}</Button>}
            <Button size="sm" onClick={() => save()} disabled={saving || (!dirty && !isNew)}><Save size={14} /> {saving ? t("Saving…") : t("Save")}</Button>
          </div>
        </div>
        <div className="space-y-1.5 border-b border-slate-200 bg-white px-4 py-2">
          <Shelf name="columns" label={t("Columns")} pills={cfg.shelves.columns} {...shelfProps} onDrop={(p, i) => onDrop("columns", p, i)} />
          <Shelf name="rows" label={t("Rows")} pills={cfg.shelves.rows} {...shelfProps} onDrop={(p, i) => onDrop("rows", p, i)} />
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {error && <div className="mb-3"><Alert>{error}</Alert></div>}
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <h2 className="mb-3 font-semibold text-slate-900">{title}</h2>
            {!hasFields ? (
              <div className="flex h-[420px] flex-col items-center justify-center gap-2 text-center text-sm text-slate-500">
                <p className="font-medium text-slate-700">{t("Drag fields to Columns and Rows to start")}</p>
                <p>{t("Dimensions (blue) split the data; measures (green) are added up.")}</p>
              </div>
            ) : queryError ? (
              <div className="flex h-[420px] items-center justify-center"><Alert>{queryError}</Alert></div>
            ) : data.data ? (
              <ChartView ref={view} cfg={shown!.cfg} type={shown!.type} result={data.data} identity={identityList}
                stale={data.isFetching} height={460} onMark={setMark} onSort={sortByColumn} />
            ) : (
              <div className="flex h-[420px] items-center justify-center text-sm text-slate-500">{t("Loading…")}</div>
            )}
            {data.data && (
              <p className={clsx("mt-3 text-end text-[11px] text-slate-400")}>
                {data.data.restricted && "Only rows you may see · "}{data.data.cached ? t("from cache") : `${data.data.ms} ms`}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Show me and options */}
      <div className="min-h-0 space-y-5 overflow-y-auto border-s border-slate-200 bg-white p-3">
        <ShowMe cfg={cfg} resolved={type} available={avail} onPick={(t) => setCfg((c) => ({ ...c, type: t }))} />
        <OptionsPanel cfg={cfg} type={type} onChange={setOptions} />
      </div>

      {mark && markRow && (
        <Menu anchor={{ x: mark.x, y: mark.y }} onClose={() => setMark(null)}>
          <div className="px-3 py-1.5 text-xs font-medium text-slate-500">{markTitle || t("This mark")}</div>
          <MenuLine />
          {markDims.length > 0 && <MenuItem onClick={() => keepOnly(false)}>{t("Keep only")}</MenuItem>}
          {markDims.length > 0 && <MenuItem onClick={() => keepOnly(true)}>{t("Exclude {v0}", { v0: markDims.length > 1 ? formatValue(markDims[0][1], markDims[0][0]) : "" })}</MenuItem>}
          {markDrill && <MenuItem onClick={drillMark}>{t("Drill down to {label}", { label: markDrill.label })}</MenuItem>}
          <MenuItem onClick={() => { setViewData({ spec: underlyingQuery(cfg, markFilters(shown!.cfg, data.data!.columns, markRow)), title: markTitle ? `Data for ${markTitle}` : "Data" }); setMark(null); }}>{t("View data")}</MenuItem>
        </Menu>
      )}
      {filterEdit && (
        <FilterModal catalog={catalog.data} field={filterEdit.field} existing={filterEdit.existing} onSave={saveFilter} onClose={() => setFilterEdit(null)} />
      )}
      {viewData && (
        <ViewDataModal datasetId={datasetId!} spec={viewData.spec} title={viewData.title} onClose={() => setViewData(null)} canExport={hasRole("explorer")} />
      )}
    </div>
  );
}
