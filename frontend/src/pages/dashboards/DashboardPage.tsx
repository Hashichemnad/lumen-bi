import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  AlertTriangle, Check, Copy, Download, Filter, FolderInput, Link2, Monitor, MoreHorizontal, Pencil, Play, Plus, Settings2, Share2,
  Smartphone, Star, Trash2, Tv, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactGridLayout, { useContainerWidth, type Layout } from "react-grid-layout";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import MoveModal from "../../components/MoveModal";
import { Alert, Button, Input } from "../../components/ui";
import { formatMoment } from "../../lib/dates";
import { t as tr } from "../../lib/i18n";
import { newId } from "../../lib/chart/types";
import { del, download, get, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { crossFor, decodeState, describeValue, encodeState, filterSpecs, isActive, type CrossFilter, type Drill } from "../../lib/dashboard/filters";
import type { Box, DashConfig, Dashboard, DashFilter, FilterValue, Tab, Tile } from "../../lib/dashboard/types";
import ViewDataModal from "../charts/ViewDataModal";
import { AddTileModal, FilterModal, ShareModal, TileModal } from "./DashboardModals";
import DashboardTile, { MARGIN, ROW_HEIGHT, tileHeight, type TileActions } from "./DashboardTile";
import FilterBar from "./FilterBar";
import Stage, { groupsOf } from "./Stage";

type Spec = Record<string, unknown>;
const PHONE_COLS = 4;
const PHONE_WIDTH = 640;

/** The first free spot for a new tile, reading left to right, top to bottom. */
function freeSpot(items: Tile[], w: number, h: number): { x: number; y: number } {
  const overlaps = (x: number, y: number) =>
    items.some((t) => x < t.desktop.x + t.desktop.w && x + w > t.desktop.x && y < t.desktop.y + t.desktop.h && y + h > t.desktop.y);
  const bottom = Math.max(0, ...items.map((i) => i.desktop.y + i.desktop.h));
  for (let y = 0; y <= bottom; y++) for (let x = 0; x + w <= 12; x++) if (!overlaps(x, y)) return { x, y };
  return { x: 0, y: bottom };
}

/** Print view (PDF/PNG export): tiles in normal page flow, each section starting on a new page. */
function PrintGrid({ tab, render }: { tab: Tab; render: (tile: Tile, heightPx: number) => React.ReactNode }) {
  const groups = groupsOf(tab);
  return (
    <>
      {groups.map((g, gi) => {
        const y0 = Math.min(...g.map((t) => t.desktop.y));
        return (
          <div key={g[0].id} className="grid gap-3 pb-3" style={{ gridTemplateColumns: "repeat(12, minmax(0, 1fr))", gridAutoRows: `${ROW_HEIGHT}px`, breakBefore: gi > 0 && g[0].kind === "section" ? "page" : "auto" }}>
            {g.map((t) => (
              <div key={t.id} style={{ gridColumn: `${t.desktop.x + 1} / span ${t.desktop.w}`, gridRow: `${t.desktop.y - y0 + 1} / span ${t.desktop.h}`, breakInside: "avoid" }}>
                {render(t, tileHeight(t.desktop.h))}
              </div>
            ))}
          </div>
        );
      })}
    </>
  );
}

/** Phone layout made from the desktop one: tiles stacked in reading order, full width. */
function stacked(items: Tile[]): Record<string, Box> {
  let y = 0;
  const out: Record<string, Box> = {};
  for (const t of [...items].sort((a, b) => a.desktop.y - b.desktop.y || a.desktop.x - b.desktop.x)) {
    out[t.id] = { x: 0, y, w: PHONE_COLS, h: t.desktop.h };
    y += t.desktop.h;
  }
  return out;
}

function Grid({ tab, editing, device, onLayout, render }: {
  tab: Tab;
  editing: boolean;
  device: "desktop" | "phone";
  onLayout: (boxes: Record<string, Box>, device: "desktop" | "phone") => void;
  render: (tile: Tile, heightPx: number) => React.ReactNode;
}) {
  const { width, containerRef, mounted } = useContainerWidth();
  const phone = device === "phone";
  const cols = phone ? PHONE_COLS : 12;
  // The grid places tiles from the left; in right-to-left pages the layout is mirrored
  const rtl = document.documentElement.dir === "rtl";
  const flip = (x: number, w: number) => (rtl ? cols - x - w : x);
  const auto = useMemo(() => stacked(tab.items), [tab.items]);
  const layout: Layout = tab.items.map((t) => {
    const box = phone ? (t.phone ?? auto[t.id]) : t.desktop;
    return { i: t.id, ...box, x: flip(box.x, box.w), minW: phone ? 2 : 2, minH: 2 };
  });
  const change = (l: Layout) => {
    if (!editing) return;
    const boxes: Record<string, Box> = {};
    for (const it of l) boxes[it.i] = { x: flip(it.x, it.w), y: it.y, w: it.w, h: it.h };
    // Only real moves count (the grid also reports the layout when it first appears)
    const moved = layout.some((it) => {
      const b = boxes[it.i];
      return !b || b.x !== it.x || b.y !== it.y || b.w !== it.w || b.h !== it.h;
    });
    if (moved) onLayout(boxes, device);
  };
  return (
    <div ref={containerRef} dir="ltr" className={clsx(phone && editing && "mx-auto max-w-[400px] rounded-2xl border-8 border-slate-800 bg-slate-50")}>
      {mounted && (
        <ReactGridLayout layout={layout} width={width} onLayoutChange={change}
          gridConfig={{ cols, rowHeight: ROW_HEIGHT, margin: [MARGIN, MARGIN], containerPadding: [0, 0] }}
          dragConfig={{ enabled: editing, handle: ".tile-handle", cancel: ".no-drag" }}
          resizeConfig={{ enabled: editing, handles: ["se"] }}>
          {tab.items.map((t) => {
            const h = (phone ? (t.phone ?? auto[t.id]) : t.desktop).h;
            return <div key={t.id} dir={rtl ? "rtl" : "ltr"}>{render(t, tileHeight(h))}</div>;
          })}
        </ReactGridLayout>
      )}
    </div>
  );
}

const PRINT = new URLSearchParams(window.location.search).get("print") === "1";
if (PRINT) (window as unknown as { __lumenPrint: boolean }).__lumenPrint = true;

/** In print view, tell the renderer when every tile has finished loading and drawing. */
function usePrintReady(active: boolean) {
  useEffect(() => {
    if (!active) return;
    let quiet = 0;
    const timer = window.setInterval(() => {
      const busy = document.querySelectorAll('[data-state="loading"]').length;
      quiet = busy ? 0 : quiet + 1;
      if (quiet >= 3) {
        window.clearInterval(timer);
        window.setTimeout(() => ((window as unknown as { __lumenReady: boolean }).__lumenReady = true), 500);
      }
    }, 300);
    return () => window.clearInterval(timer);
  }, [active]);
}

export default function DashboardPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { hasRole } = useAuth();
  const [search, setSearch] = useSearchParams();
  const detail = useQuery({ queryKey: ["dashboard", id], queryFn: () => get<Dashboard>(`/dashboards/${id}`) });
  const dash = detail.data;

  // ---------------------------------------------------------------- editing (changes save automatically)
  const [editing, setEditing] = useState(search.get("edit") === "1");
  const [config, setConfig] = useState<DashConfig | null>(null);
  const [name, setName] = useState("");
  const [saveState, setSaveState] = useState<"saved" | "saving" | "error">("saved");
  const [saveError, setSaveError] = useState("");
  const pending = useRef<number | null>(null);
  const nameRef = useRef(name);
  nameRef.current = name;
  useEffect(() => {
    if (dash && !dash.can_edit && editing) setEditing(false);
  }, [dash, editing]);
  useEffect(() => {
    if (dash && !config) {
      setConfig(dash.config);
      setName(dash.name);
    }
  }, [dash, config]);
  const save = useCallback(async (next: DashConfig) => {
    setSaveState("saving");
    try {
      const out = await put<Dashboard>(`/dashboards/${id}`, { name: nameRef.current.trim() || "Untitled dashboard", description: dash?.description ?? "", config: next });
      // Keep the local layout (it may have moved on); take the server's charts and datasets
      qc.setQueryData<Dashboard>(["dashboard", id], (old) => (old ? { ...out, config: out.config } : out));
      setSaveState("saved");
      setSaveError("");
    } catch (err) {
      setSaveState("error");
      setSaveError(err instanceof Error ? err.message : tr("Could not save"));
    }
  }, [id, dash?.description, qc]);
  const update = useCallback((change: (c: DashConfig) => DashConfig, delay = 600) => {
    setConfig((c) => {
      if (!c) return c;
      const next = change(c);
      if (pending.current) window.clearTimeout(pending.current);
      pending.current = window.setTimeout(() => {
        pending.current = null;
        save(next);
      }, delay);
      return next;
    });
  }, [save]);

  // ---------------------------------------------------------------- what people choose while viewing
  // eslint-disable-next-line react-hooks/exhaustive-deps -- read once, when the page opens
  const initial = useMemo(() => decodeState(search.get("s")), []);
  const [values, setValues] = useState<Record<string, FilterValue>>(initial?.f ?? {});
  const [params, setParams] = useState<Record<string, unknown>>(initial?.p ?? {});
  const [tabId, setTabId] = useState<string | undefined>(initial?.t);
  const [cross, setCross] = useState<CrossFilter | null>(null);
  const [drills, setDrills] = useState<Record<string, Drill>>({});
  const [viewData, setViewData] = useState<{ tile: string; title: string; filters: Spec[] } | null>(null);
  const [modal, setModal] = useState<null | { kind: "add" } | { kind: "tile"; tile: Tile } | { kind: "filter"; filter?: DashFilter } | { kind: "share" } | { kind: "move" }>(null);
  const stage: "tv" | "present" | null = search.get("tv") === "1" ? "tv" : search.get("present") === "1" ? "present" : null;
  const openStage = (m: "tv" | "present" | null) => {
    const next = new URLSearchParams(search);
    next.delete("tv");
    next.delete("present");
    if (m) next.set(m, "1");
    setSearch(next);
  };
  const [more, setMore] = useState(false);
  const [device, setDevice] = useState<"desktop" | "phone">("desktop");
  const [copied, setCopied] = useState(false);
  const [narrow, setNarrow] = useState(window.innerWidth < PHONE_WIDTH);
  const scroller = useRef<HTMLDivElement>(null);
  const [exporting, setExporting] = useState<"" | "pdf" | "png">("");
  const [exportError, setExportError] = useState("");
  const [activeSection, setActiveSection] = useState<string | null>(null);
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < PHONE_WIDTH);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const cfg = config ?? dash?.config;
  const filterValue = useCallback((f: DashFilter) => (f.id in values ? values[f.id] : f.value ?? null), [values]);
  const tab = cfg?.tabs.find((t) => t.id === tabId) ?? cfg?.tabs[0];

  // Links keep the filters: the current choices live in the address
  useEffect(() => {
    if (!cfg) return;
    const f = Object.fromEntries(Object.entries(values).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(cfg.filters.find((x) => x.id === k)?.value ?? null)));
    const next = new URLSearchParams(search);
    if (Object.keys(f).length || Object.keys(params).length || (tabId && tabId !== cfg.tabs[0]?.id)) next.set("s", encodeState({ f, p: params, t: tabId }));
    else next.delete("s");
    if (editing) next.set("edit", "1");
    else next.delete("edit");
    if (next.toString() !== search.toString()) setSearch(next, { replace: true });
  }, [values, params, tabId, editing, cfg]); // eslint-disable-line react-hooks/exhaustive-deps -- search is written here

  usePrintReady(PRINT && !!dash && !!cfg);
  const sectionTiles = (tab?.items ?? [])
    .filter((t) => t.kind === "section")
    .sort((a, b) => a.desktop.y - b.desktop.y || a.desktop.x - b.desktop.x);
  const goToSection = (sid: string, smooth = true) => {
    const box = scroller.current;
    const el = box?.querySelector(`[data-section="${sid}"]`);
    if (!box || !el) return;
    const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 12;
    box.scrollTo({ top, behavior: smooth ? "smooth" : "auto" });
    setActiveSection(sid);
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#section-${sid}`);
  };
  // Highlight the section in view while scrolling
  const onScroll = () => {
    const box = scroller.current;
    if (!box || !sectionTiles.length) return;
    const limit = box.getBoundingClientRect().top + 140;
    let current: string | null = sectionTiles[0].id;
    for (const el of box.querySelectorAll<HTMLElement>("[data-section]")) {
      if (el.getBoundingClientRect().top <= limit) current = el.dataset.section ?? current;
    }
    // Reading order on the page can differ from the list order on phones; take the nearest one above
    if (current !== activeSection) setActiveSection(current);
  };
  // A link to a section opens there
  const hashSection = window.location.hash.startsWith("#section-") ? window.location.hash.slice(9) : null;
  useEffect(() => {
    if (!hashSection || !dash) return;
    const t = setTimeout(() => goToSection(hashSection, false), 700);
    return () => clearTimeout(t);
  }, [dash?.id, tab?.id]); // eslint-disable-line react-hooks/exhaustive-deps -- once the tab is drawn

  if (detail.error) return <div className="p-6"><Alert>{(detail.error as Error).message}</Alert></div>;
  if (!dash || !cfg || !tab) return <div className="p-6 text-sm text-slate-500">{tr("Loading…")}</div>;

  const datasetOf = (t: Tile) => {
    const chart = t.chart_id ? dash.charts[String(t.chart_id)] : undefined;
    return chart ? dash.datasets[String(chart.dataset_id)] : undefined;
  };
  const filtersFor = (t: Tile): Spec[] => {
    const ds = datasetOf(t);
    if (!ds) return [];
    return [...cfg.filters.flatMap((f) => filterSpecs(f, filterValue(f), ds.id)), ...crossFor(cross, t.id, ds)];
  };
  const paramsFor = (t: Tile): Record<string, unknown> => {
    const ds = datasetOf(t);
    if (!ds) return {};
    return Object.fromEntries(Object.entries(params).filter(([k]) => k.startsWith(`${ds.id}:`)).map(([k, v]) => [k.slice(String(ds.id).length + 1), v]));
  };

  const actions: TileActions = {
    onCross: (tileId, label, filters) => setCross({ source: tileId, label, filters }),
    onDrill: (tileId, d) => setDrills((all) => {
      const next = { ...all };
      if (d) next[tileId] = d;
      else delete next[tileId];
      return next;
    }),
    onViewData: (tileId, title, filters) => setViewData({ tile: tileId, title, filters }),
    onEdit: (tile) => setModal({ kind: "tile", tile }),
    onRemove: (tile) => update((c) => ({ ...c, tabs: c.tabs.map((t) => ({ ...t, items: t.items.filter((i) => i.id !== tile.id) })) }), 0),
  };
  const onLayout = (boxes: Record<string, Box>, dev: "desktop" | "phone") => {
    update((c) => ({
      ...c,
      tabs: c.tabs.map((t) => (t.id !== tab.id ? t : { ...t, items: t.items.map((i) => (boxes[i.id] ? (dev === "desktop" ? { ...i, desktop: boxes[i.id] } : { ...i, phone: boxes[i.id] }) : i)) })),
      options: dev === "phone" ? { ...c.options, phone_layout: true } : c.options,
    }));
  };
  const addTile = (t: Omit<Tile, "desktop" | "id">, size: { w: number; h: number }) => {
    const spot = freeSpot(tab.items, size.w, size.h);
    update((c) => ({ ...c, tabs: c.tabs.map((x) => (x.id !== tab.id ? x : { ...x, items: [...x.items, { ...t, id: newId("tile"), desktop: { ...spot, ...size }, phone: null } as Tile] })) }), 0);
  };
  const saveFilter = (f: DashFilter) => {
    update((c) => ({ ...c, filters: c.filters.some((x) => x.id === f.id) ? c.filters.map((x) => (x.id === f.id ? f : x)) : [...c.filters, f] }), 0);
    setValues((v) => ({ ...v, [f.id]: f.value }));
  };
  const toggleFavorite = async () => {
    if (dash.favorite) await del(`/dashboards/${id}/favorite`);
    else await put(`/dashboards/${id}/favorite`, {});
    await qc.invalidateQueries({ queryKey: ["dashboard", id] });
    await qc.invalidateQueries({ queryKey: ["dashboards"] });
  };
  const copyLink = async () => {
    await navigator.clipboard?.writeText(window.location.href.replace(/([?&])edit=1&?/, "$1").replace(/[?&]#/, "#").replace(/[?&]$/, "")).catch(() => undefined);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  const exportAs = async (format: "pdf" | "png") => {
    setExporting(format);
    setExportError("");
    try {
      await download(`/dashboards/${id}/render`, { format, state: search.get("s") }, `${dash.name}.${format}`);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : tr("Export failed"));
    } finally {
      setExporting("");
    }
  };
  const copyDashboard = async () => {
    const made = await post<Dashboard>(`/dashboards/${id}/copy`);
    await qc.invalidateQueries({ queryKey: ["dashboards"] });
    navigate(`/dashboards/${made.id}?edit=1`);
  };
  const remove = async () => {
    if (!confirm(tr("Delete the dashboard \"{name}\"? Its charts are kept.", { name: dash.name }))) return;
    await del(`/dashboards/${id}`);
    await qc.invalidateQueries({ queryKey: ["dashboards"] });
    navigate("/dashboards");
  };
  const addTab = () => {
    const t = { id: newId("tab"), name: `Tab ${cfg.tabs.length + 1}`, items: [] };
    update((c) => ({ ...c, tabs: [...c.tabs, t] }), 0);
    setTabId(t.id);
  };
  const renameTab = (t: Tab) => {
    const n = prompt(tr("Tab name"), t.name);
    if (n && n.trim()) update((c) => ({ ...c, tabs: c.tabs.map((x) => (x.id === t.id ? { ...x, name: n.trim() } : x)) }), 0);
  };
  const removeTab = (t: Tab) => {
    if (cfg.tabs.length < 2 || !confirm(tr("Delete the tab \"{name}\" and its tiles?", { name: t.name }))) return;
    update((c) => ({ ...c, tabs: c.tabs.filter((x) => x.id !== t.id) }), 0);
    setTabId(undefined);
  };
  const showDevice = editing ? device : narrow ? "phone" : "desktop";

  const synced = Object.values(dash.datasets).map((d) => d.last_synced_at).filter(Boolean) as string[];
  const refreshedAt = synced.length ? synced.sort().slice(-1)[0] : null;
  const failed = Object.values(dash.datasets).some((d) => d.last_status === "failed");
  const renderTile = (t: Tile, heightPx: number) => (
    <DashboardTile dash={dash} tile={t} dataset={datasetOf(t)} filters={filtersFor(t)} params={paramsFor(t)}
      drill={drills[t.id]} isCrossSource={cross?.source === t.id} actions={actions} editing={false} heightPx={heightPx}
      canExport={hasRole("explorer")} />
  );
  if (stage && !PRINT) {
    return <Stage dash={dash} cfg={cfg} mode={stage} render={renderTile} onExit={() => openStage(null)} refreshedAt={refreshedAt} />;
  }

  if (PRINT) {
    const active = cfg.filters.filter((f) => isActive(f, filterValue(f)));
    return (
      <div className="bg-white p-4">
        <div className="mb-3 flex items-end justify-between border-b border-slate-200 pb-2">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">{dash.name}</h1>
            {dash.description && <p className="text-sm text-slate-600">{dash.description}</p>}
          </div>
          <div className="text-end text-xs text-slate-500">
            {cfg.tabs.length > 1 && <div>{tab.name}</div>}
            <div>{tr("Exported {v0}", { v0: new Date().toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) })}</div>
          </div>
        </div>
        {active.length > 0 && (
          <p className="mb-3 text-xs text-slate-600">{tr("Filters: {v0}", { v0: active.map((f) => `${f.label}: ${describeValue(f, filterValue(f))}`).join(" · ") })}</p>
        )}
        <PrintGrid tab={tab} render={(t, heightPx) => (
          <DashboardTile dash={dash} tile={t} dataset={datasetOf(t)} filters={filtersFor(t)} params={paramsFor(t)}
            isCrossSource={false} actions={actions} editing={false} heightPx={heightPx} canExport={false} />
        )} />
      </div>
    );
  }

  return (
    <div ref={scroller} onScroll={onScroll} className="h-full overflow-y-auto bg-slate-50">
      <div className="mx-auto max-w-[1600px] p-4 lg:p-6">
        {/* header */}
        <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <nav aria-label={tr("Breadcrumb")} className="mb-1 flex items-center gap-1.5 text-xs text-slate-500">
              <Link to="/workspaces" className="hover:text-slate-800">{dash.workspace_name || tr("Personal")}</Link>
              <span aria-hidden="true">/</span>
              <Link to="/dashboards" className="hover:text-slate-800">{tr("Dashboards")}</Link>
            </nav>
            <div className="flex items-center gap-2">
              {editing ? (
                <Input value={name} onChange={(e) => { setName(e.target.value); update((c) => c, 800); }} className="max-w-md py-1.5 text-lg font-semibold" aria-label={tr("Dashboard name")} />
              ) : (
                <h1 className="truncate text-2xl font-bold tracking-tight text-slate-900">{dash.name}</h1>
              )}
              {!editing && (
                <button onClick={toggleFavorite} className="rounded p-1 text-slate-400 hover:text-amber-500" aria-label={dash.favorite ? tr("Remove from favourites") : tr("Add to favourites")}>
                  <Star size={18} className={dash.favorite ? "fill-amber-400 text-amber-500" : ""} />
                </button>
              )}
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
              {editing ? (
                <span className={clsx(saveState === "error" ? "text-red-600" : "text-slate-500")}>
                  {saveState === "saving" ? tr("Saving…") : saveState === "error" ? saveError : tr("All changes saved")}
                </span>
              ) : failed ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 font-semibold text-red-700"><AlertTriangle size={12} /> {tr("A data sync failed")}</span>
              ) : refreshedAt ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-700"><Check size={12} strokeWidth={3} /> {tr("Data refreshed {when}", { when: formatMoment(refreshedAt) })}</span>
              ) : null}
              {!editing && dash.description && <span className="text-slate-500">{dash.description}</span>}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {editing ? (
              <>
                <div className="flex rounded-lg border border-slate-300 bg-white p-0.5">
                  <button onClick={() => setDevice("desktop")} aria-pressed={device === "desktop"} className={clsx("rounded-md px-2 py-1.5", device === "desktop" ? "bg-slate-100" : "text-slate-500")} title={tr("Desktop layout")} aria-label={tr("Desktop layout")}><Monitor size={15} /></button>
                  <button onClick={() => setDevice("phone")} aria-pressed={device === "phone"} className={clsx("rounded-md px-2 py-1.5", device === "phone" ? "bg-slate-100" : "text-slate-500")} title={tr("Phone layout")} aria-label={tr("Phone layout")}><Smartphone size={15} /></button>
                </div>
                <Button variant="secondary" onClick={() => setModal({ kind: "filter" })}><Filter size={15} /> {tr("Add filter")}</Button>
                <Button variant="secondary" onClick={() => setModal({ kind: "add" })}><Plus size={15} /> {tr("Add tile")}</Button>
                <OptionsMenu config={cfg} onChange={(o) => update((c) => ({ ...c, options: { ...c.options, ...o } }), 0)} />
                <Button onClick={() => { if (pending.current) { window.clearTimeout(pending.current); pending.current = null; save(cfg); } setEditing(false); }}>{tr("Done")}</Button>
              </>
            ) : (
              <>
                <Button variant="secondary" onClick={() => openStage("present")}><Play size={15} /> {tr("Present")}</Button>
                <Button variant="secondary" onClick={() => openStage("tv")}><Tv size={15} /> {tr("TV mode")}</Button>
                <ExportMenu busy={exporting} onExport={exportAs} />
                {dash.can_manage && <Button variant="secondary" onClick={() => setModal({ kind: "share" })}><Share2 size={15} /> {tr("Share")}</Button>}
                <div className="relative">
                  <Button variant="secondary" onClick={() => setMore((m) => !m)} aria-label={tr("More")} aria-expanded={more}><MoreHorizontal size={16} /></Button>
                  {more && (
                    <>
                      <button className="fixed inset-0 z-30 cursor-default" aria-hidden="true" tabIndex={-1} onClick={() => setMore(false)} />
                      <div role="menu" className="absolute end-0 z-40 mt-1 w-56 rounded-lg border border-slate-200 bg-white py-1 text-sm shadow-xl">
                        <button role="menuitem" onClick={() => { copyLink(); setMore(false); }} className="flex w-full items-center gap-2 px-3 py-2 text-start hover:bg-slate-100"><Link2 size={14} /> {copied ? tr("Copied") : tr("Copy link")}</button>
                        {hasRole("explorer") && <button role="menuitem" onClick={() => { setMore(false); copyDashboard(); }} className="flex w-full items-center gap-2 px-3 py-2 text-start hover:bg-slate-100"><Copy size={14} /> {tr("Make a copy")}</button>}
                        {dash.can_manage && <button role="menuitem" onClick={() => { setMore(false); setModal({ kind: "move" }); }} className="flex w-full items-center gap-2 px-3 py-2 text-start hover:bg-slate-100"><FolderInput size={14} /> {tr("Move to a workspace")}</button>}
                        {dash.can_manage && <button role="menuitem" onClick={() => { setMore(false); remove(); }} className="flex w-full items-center gap-2 px-3 py-2 text-start text-red-700 hover:bg-red-50"><Trash2 size={14} /> {tr("Delete")}</button>}
                      </div>
                    </>
                  )}
                </div>
                {dash.can_edit && <Button onClick={() => setEditing(true)}><Pencil size={15} /> {tr("Edit")}</Button>}
              </>
            )}
          </div>
        </header>
        {exportError && <div className="mb-3"><Alert>{exportError}</Alert></div>}

        {/* tabs */}
        {(cfg.tabs.length > 1 || editing) && (
          <div className="mb-4 flex flex-wrap items-center gap-1 border-b border-slate-200">
            {cfg.tabs.map((t) => (
              <div key={t.id} className={clsx("-mb-px flex items-center border-b-2", t.id === tab.id ? "border-brand-600" : "border-transparent")}>
                <button onClick={() => setTabId(t.id)} onDoubleClick={() => editing && renameTab(t)}
                  className={clsx("px-3 py-2 text-sm font-medium", t.id === tab.id ? "text-brand-700" : "text-slate-500 hover:text-slate-700")}>{t.name}</button>
                {editing && t.id === tab.id && (
                  <>
                    <button onClick={() => renameTab(t)} className="rounded p-0.5 text-slate-400 hover:text-slate-700" aria-label={tr("Rename tab")}><Pencil size={12} /></button>
                    {cfg.tabs.length > 1 && <button onClick={() => removeTab(t)} className="rounded p-0.5 text-slate-400 hover:text-red-600" aria-label={tr("Delete tab")}><X size={12} /></button>}
                  </>
                )}
              </div>
            ))}
            {editing && <button onClick={addTab} className="ms-1 inline-flex items-center gap-1 rounded px-2 py-1 text-sm text-slate-500 hover:bg-slate-100"><Plus size={14} /> {tr("Tab")}</button>}
          </div>
        )}

        <FilterBar dash={dash} filters={cfg.filters} values={Object.fromEntries(cfg.filters.map((f) => [f.id, filterValue(f)]))}
          onChange={(fid, v) => setValues((all) => ({ ...all, [fid]: v }))}
          params={params} onParam={(k, v) => setParams((p) => ({ ...p, [k]: v }))}
          cross={cross} onClearCross={() => setCross(null)}
          onReset={() => { setValues({}); setParams({}); setCross(null); setDrills({}); }}
          editing={editing} onEditFilter={(f) => setModal({ kind: "filter", filter: f })}
          onRemoveFilter={(f) => update((c) => ({ ...c, filters: c.filters.filter((x) => x.id !== f.id) }), 0)} />

        {sectionTiles.length > 0 && showDevice === "phone" && !editing && (
          <SectionNav sections={sectionTiles} active={activeSection ?? sectionTiles[0].id} onGo={goToSection} phone />
        )}
        <div className="flex gap-6">
        <div className="min-w-0 flex-1">
        {!tab.items.length ? (
          <div className="rounded-xl border-2 border-dashed border-slate-300 p-12 text-center text-sm text-slate-500">
            {editing ? (
              <>
                <p className="font-medium text-slate-700">{tr("This tab is empty")}</p>
                <p className="mt-1">{tr("Add charts, text, images or web pages.")}</p>
                <Button className="mt-4" onClick={() => setModal({ kind: "add" })}><Plus size={16} /> {tr("Add tile")}</Button>
              </>
            ) : dash.can_edit ? (
              <p>{tr("Nothing here yet.")} <button className="text-brand-600 hover:underline" onClick={() => setEditing(true)}>{tr("Edit")}</button> {tr("to add charts.")}</p>
            ) : <p>{tr("Nothing here yet.")}</p>}
          </div>
        ) : (
          <Grid key={`${tab.id}-${showDevice}`} tab={tab} editing={editing} device={showDevice} onLayout={onLayout}
            render={(t, heightPx) => (
              <DashboardTile dash={dash} tile={t} dataset={datasetOf(t)} filters={filtersFor(t)} params={paramsFor(t)}
                drill={drills[t.id]} isCrossSource={cross?.source === t.id} actions={actions} editing={editing} heightPx={heightPx}
                canExport={hasRole("explorer")} />
            )} />
        )}
        </div>
        {sectionTiles.length > 0 && showDevice === "desktop" && (
          <SectionNav sections={sectionTiles} active={activeSection ?? sectionTiles[0].id} onGo={goToSection} phone={false} />
        )}
        </div>
        {editing && device === "phone" && <p className="mt-3 text-center text-xs text-slate-500">{tr("Arrange tiles for phones. Until you change it, phones show the tiles stacked in reading order.")}</p>}
      </div>

      {modal?.kind === "add" && <AddTileModal onAdd={addTile} onClose={() => setModal(null)} />}
      {modal?.kind === "tile" && (
        <TileModal tile={modal.tile} onClose={() => setModal(null)}
          onSave={(nt) => update((c) => ({ ...c, tabs: c.tabs.map((t) => ({ ...t, items: t.items.map((i) => (i.id === nt.id ? nt : i)) })) }), 0)} />
      )}
      {modal?.kind === "filter" && <FilterModal dash={{ ...dash, config: cfg }} existing={modal.filter} onSave={saveFilter} onClose={() => setModal(null)} />}
      {modal?.kind === "share" && <ShareModal dash={dash} onClose={() => setModal(null)} />}
      {modal?.kind === "move" && (
        <MoveModal kind="dashboards" id={dash.id} current={dash.workspace_id} onClose={() => setModal(null)}
          onMoved={async () => { await qc.invalidateQueries({ queryKey: ["dashboard", dash.id] }); await qc.invalidateQueries({ queryKey: ["dashboards"] }); }} />
      )}
      {viewData && (
        <DashboardViewData dashId={id} tile={viewData.tile} title={viewData.title} filters={viewData.filters}
          params={paramsFor(tab.items.find((t) => t.id === viewData.tile) ?? tab.items[0])} onClose={() => setViewData(null)} canExport={hasRole("explorer")} />
      )}
    </div>
  );
}

/** Sections of the current tab, in reading order, with the one in view highlighted. */
function SectionNav({ sections, active, onGo, phone }: { sections: Tile[]; active: string | null; onGo: (id: string) => void; phone: boolean }) {
  if (phone) {
    return (
      <div className="sticky top-0 z-20 -mx-4 mb-3 bg-slate-50/95 px-4 py-2 backdrop-blur">
        <select value={active ?? ""} onChange={(e) => e.target.value && onGo(e.target.value)} aria-label={tr("Go to section")}
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
          {sections.map((t) => <option key={t.id} value={t.id}>{t.code ? `${t.code} · ` : ""}{t.title || tr("Section")}</option>)}
        </select>
      </div>
    );
  }
  return (
    <nav aria-labelledby="sections-h" className="sticky top-4 hidden w-64 shrink-0 self-start rounded-xl border border-slate-200 bg-white p-3 shadow-sm lg:block">
      <h2 id="sections-h" className="mb-2 px-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{tr("Sections")}</h2>
      <ul className="max-h-[calc(100vh-9rem)] space-y-0.5 overflow-y-auto">
        {sections.map((t) => (
          <li key={t.id}>
            <button onClick={() => onGo(t.id)} aria-current={active === t.id ? "true" : undefined}
              className={clsx("flex min-h-11 w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-start text-sm transition-colors",
                active === t.id ? "bg-brand-50 text-slate-900" : "text-slate-700 hover:bg-slate-100")}>
              <span aria-hidden="true" className={clsx("mt-1.5 size-2 shrink-0 rounded-full", active === t.id ? "bg-brand-600" : "bg-slate-300")} />
              <span className="min-w-0">
                {t.code && <span className="block text-[11px] font-bold tracking-wide text-slate-500">{t.code}</span>}
                <span className={clsx("line-clamp-2 leading-snug", active === t.id && "font-semibold")}>{t.title || tr("Section")}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      <div className="my-2 h-px bg-slate-200" />
      <button onClick={() => sections[0] && onGo(sections[0].id)} className="flex min-h-10 w-full items-center gap-2 rounded-lg px-2.5 text-sm font-semibold text-brand-700 hover:bg-slate-100">
        {tr("Back to top")}
      </button>
    </nav>
  );
}

function ExportMenu({ busy, onExport }: { busy: "" | "pdf" | "png"; onExport: (f: "pdf" | "png") => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <Button size="sm" variant="secondary" onClick={() => setOpen(!open)} disabled={!!busy} aria-expanded={open}>
        <Download size={14} /> {busy ? tr("Preparing…") : tr("Export")}
      </Button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute end-0 z-40 mt-1 w-56 rounded-lg border border-slate-200 bg-white py-1 text-sm shadow-lg">
            <button className="block w-full px-3 py-2 text-start hover:bg-slate-50" onClick={() => { setOpen(false); onExport("pdf"); }}>
              {tr("PDF")} <span className="block text-xs text-slate-500">{tr("A4 landscape, a page per section")}</span>
            </button>
            <button className="block w-full px-3 py-2 text-start hover:bg-slate-50" onClick={() => { setOpen(false); onExport("png"); }}>
              {tr("Image (PNG)")} <span className="block text-xs text-slate-500">{tr("The whole tab as one picture")}</span>
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function OptionsMenu({ config, onChange }: { config: DashConfig; onChange: (o: Partial<DashConfig["options"]>) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <Button size="sm" variant="secondary" onClick={() => setOpen(!open)} aria-expanded={open}><Settings2 size={14} /> {tr("Options")}</Button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute end-0 z-40 mt-1 w-72 space-y-2 rounded-lg border border-slate-200 bg-white p-3 text-sm shadow-lg">
            <label className="flex items-start gap-2"><input type="checkbox" className="mt-0.5" checked={config.options.cross_filter} onChange={(e) => onChange({ cross_filter: e.target.checked })} />
              <span>{tr("Clicking a chart can filter the others")}</span></label>
            <label className="flex items-start gap-2"><input type="checkbox" className="mt-0.5" checked={config.options.view_data} onChange={(e) => onChange({ view_data: e.target.checked })} />
              <span>{tr("People can view the rows behind charts (their own rows only)")}</span></label>
            <label className="flex items-start gap-2"><input type="checkbox" className="mt-0.5" checked={config.options.phone_layout} onChange={(e) => onChange({ phone_layout: e.target.checked })} />
              <span>{tr("Use my own phone layout (otherwise tiles stack)")}</span></label>
          </div>
        </>
      )}
    </div>
  );
}

/** View data for a dashboard tile: the rows behind it, through the dashboard. */
function DashboardViewData({ dashId, tile, title, filters, params, onClose, canExport }: {
  dashId: number; tile: string; title: string; filters: Spec[]; params: Record<string, unknown>; onClose: () => void; canExport: boolean;
}) {
  return (
    <ViewDataModal datasetId={0} title={title} onClose={onClose} canExport={canExport}
      spec={{ item: tile, filters, params, rows: true, limit: 500 }}
      endpoint={`/dashboards/${dashId}/query`} exportEndpoint={`/dashboards/${dashId}/export`} />
  );
}
