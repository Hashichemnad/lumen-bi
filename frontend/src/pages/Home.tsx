import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, Building2, Check, Clock, Database, LayoutDashboard, PieChart, Plus, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Card } from "../components/ui";
import { get, post } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { SavedChart } from "../lib/chart/types";
import { emptyDashboard, type Dashboard, type DashboardSummary } from "../lib/dashboard/types";
import { formatDay, formatMoment } from "../lib/dates";
import { t } from "../lib/i18n";
import { useWorkspace } from "../lib/workspace";
import { DashboardCard } from "./dashboards/DashboardList";

interface DatasetLite {
  id: number;
  name: string;
  last_status: string;
  last_synced_at: string | null;
  next_run_at: string | null;
  schedule: string | null;
  row_count: number;
}

type Health = "fresh" | "stale" | "failed" | "empty";

export function datasetHealth(d: DatasetLite): Health {
  if (d.last_status === "failed") return "failed";
  if (!d.last_synced_at) return "empty";
  // A scheduled sync more than an hour late means the data is not as fresh as people expect
  if (d.schedule && d.next_run_at && Date.parse(d.next_run_at) < Date.now() - 3600_000) return "stale";
  return "fresh";
}

export function HealthChip({ health }: { health: Health }) {
  const cfg = {
    fresh: { cls: "bg-emerald-50 text-emerald-700", icon: <Check size={12} strokeWidth={3} />, label: "Fresh" },
    stale: { cls: "bg-amber-50 text-amber-800", icon: <Clock size={12} strokeWidth={2.6} />, label: "Late" },
    failed: { cls: "bg-red-50 text-red-700", icon: <AlertTriangle size={12} strokeWidth={2.6} />, label: "Sync failed" },
    empty: { cls: "bg-slate-100 text-slate-600", icon: <Clock size={12} />, label: "No data yet" },
  }[health];
  return (
    <span className={clsx("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold", cfg.cls)}>
      {cfg.icon}{t(cfg.label)}
    </span>
  );
}

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? t("Good morning") : h < 17 ? t("Good afternoon") : t("Good evening");
}

interface Found {
  kind: "dashboard" | "chart" | "dataset";
  id: number;
  name: string;
  sub: string;
  to: string;
}

function SearchBox({ items }: { items: Found[] }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        ref.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const hits = words.length ? items.filter((i) => words.every((w) => `${i.name} ${i.sub}`.toLowerCase().includes(w))).slice(0, 8) : [];
  const icon = { dashboard: <LayoutDashboard size={16} />, chart: <PieChart size={16} />, dataset: <Database size={16} /> };
  return (
    <div className="relative flex-1 basis-96">
      <label htmlFor="home-search" className="sr-only">{t("Search")}</label>
      <div className="flex h-11 items-center gap-2.5 rounded-lg border border-slate-200 bg-white px-3.5 text-slate-500 focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-100">
        <Search size={18} />
        <input id="home-search" ref={ref} type="search" value={q} autoComplete="off" role="combobox" aria-expanded={open && hits.length > 0} aria-controls="home-search-list"
          placeholder={t("Search dashboards, charts and data")}
          onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(0); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            if (e.key === "Enter" && hits[active]) navigate(hits[active].to);
            if (e.key === "Escape") setOpen(false);
          }}
          className="min-w-0 flex-1 bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-400" />
        <kbd className="hidden rounded border border-slate-200 px-1.5 py-0.5 font-sans text-[11px] sm:block">Ctrl K</kbd>
      </div>
      {open && words.length > 0 && (
        <ul id="home-search-list" role="listbox" className="absolute inset-x-0 top-12 z-30 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-xl">
          {hits.map((h, i) => (
            <li key={`${h.kind}-${h.id}`} role="option" aria-selected={i === active}>
              <Link to={h.to} className={clsx("flex items-center gap-3 px-3 py-2 text-sm", i === active ? "bg-brand-50" : "hover:bg-slate-50")}>
                <span className="text-brand-600">{icon[h.kind]}</span>
                <span className="min-w-0 flex-1 truncate font-medium text-slate-900">{h.name}</span>
                <span className="truncate text-xs text-slate-500">{h.sub}</span>
              </Link>
            </li>
          ))}
          {!hits.length && <li className="px-3 py-3 text-sm text-slate-500">{t("Nothing found.")}</li>}
        </ul>
      )}
    </div>
  );
}

export default function Home() {
  const { user, hasRole } = useAuth();
  const navigate = useNavigate();
  const { shows, list: spaces, target, current } = useWorkspace();
  const dashboards = useQuery({ queryKey: ["dashboards"], queryFn: () => get<DashboardSummary[]>("/dashboards") });
  const charts = useQuery({ queryKey: ["charts"], queryFn: () => get<SavedChart[]>("/charts"), enabled: hasRole("explorer") });
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => get<DatasetLite[]>("/datasets"), enabled: hasRole("creator") });
  const [show, setShow] = useState<"all" | "dashboard" | "chart">("all");

  const dash = (dashboards.data ?? []).filter((d) => shows(d.workspace_id));
  const favorites = dash.filter((d) => d.favorite);
  const picks = (favorites.length ? favorites : dash).slice(0, 4);
  const chartList = (charts.data ?? []).filter((c) => shows(c.workspace_id));
  const recent = useMemo(() => {
    const rows = [
      ...dash.map((d) => ({ kind: "dashboard" as const, id: d.id, name: d.name, ws: d.workspace_name, owner: d.owner_name, at: d.updated_at, to: `/dashboards/${d.id}` })),
      ...chartList.map((c) => ({ kind: "chart" as const, id: c.id, name: c.name, ws: c.workspace_name, owner: c.owner_name, at: c.updated_at, to: `/charts/${c.id}` })),
    ];
    return rows.filter((r) => show === "all" || r.kind === show).sort((a, b) => b.at.localeCompare(a.at)).slice(0, 8);
  }, [dash, chartList, show]);
  const searchItems: Found[] = [
    ...(dashboards.data ?? []).map((d) => ({ kind: "dashboard" as const, id: d.id, name: d.name, sub: d.workspace_name || t("Dashboard"), to: `/dashboards/${d.id}` })),
    ...(charts.data ?? []).map((c) => ({ kind: "chart" as const, id: c.id, name: c.name, sub: c.dataset_name, to: `/charts/${c.id}` })),
    ...(datasets.data ?? []).map((d) => ({ kind: "dataset" as const, id: d.id, name: d.name, sub: t("Dataset"), to: `/data/datasets/${d.id}` })),
  ];
  const newDashboard = async () => {
    const d = await post<Dashboard>("/dashboards", { name: t("Untitled dashboard"), config: emptyDashboard(), workspace_id: target });
    navigate(`/dashboards/${d.id}?edit=1`);
  };
  const health = (datasets.data ?? []).map((d) => ({ d, h: datasetHealth(d) }))
    .sort((a, b) => ["failed", "stale", "empty", "fresh"].indexOf(a.h) - ["failed", "stale", "empty", "fresh"].indexOf(b.h))
    .slice(0, 5);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <SearchBox items={searchItems} />
      </div>

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[28px] font-bold leading-tight tracking-tight">{greeting()}{user?.name ? `, ${user.name.split(" ")[0]}` : ""}</h1>
          <p className="mt-1 text-sm text-slate-500">
            {formatDay(new Date(), { dateStyle: "full" })}{current ? ` · ${current.name}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {hasRole("explorer") && (
            <button onClick={newDashboard} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white hover:bg-brand-700">
              <Plus size={16} strokeWidth={2.4} /> {t("New dashboard")}
            </button>
          )}
          {hasRole("explorer") && <Link to="/charts?new=1" className="inline-flex min-h-10 items-center rounded-lg border border-slate-200 bg-white px-3.5 text-sm font-medium text-slate-800 hover:bg-slate-50">{t("New chart")}</Link>}
          {hasRole("creator") && <Link to="/data" className="inline-flex min-h-10 items-center rounded-lg border border-slate-200 bg-white px-3.5 text-sm font-medium text-slate-800 hover:bg-slate-50">{t("Connect data")}</Link>}
        </div>
      </div>

      <section aria-labelledby="fav-h" className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 id="fav-h" className="text-base font-semibold">{favorites.length ? t("Favourites") : t("Dashboards")}</h2>
          <Link to="/dashboards" className="text-sm font-medium text-brand-700 hover:underline">{t("All dashboards")}</Link>
        </div>
        {picks.length ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{picks.map((d) => <DashboardCard key={d.id} d={d} />)}</div>
        ) : dashboards.data ? (
          <Card className="p-8 text-center text-sm text-slate-500">
            {hasRole("explorer") ? t("No dashboards here yet. Create the first one.") : t("No dashboards have been shared with you yet.")}
          </Card>
        ) : null}
      </section>

      <div className="flex flex-wrap items-start gap-6">
        <section aria-labelledby="recent-h" className="min-w-0 flex-[999_1_560px]">
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3 px-5 pb-2 pt-4">
              <h2 id="recent-h" className="text-base font-semibold">{t("Recent")}</h2>
              {hasRole("explorer") && (
                <div role="group" aria-label={t("Show")} className="flex gap-1 rounded-lg bg-slate-100 p-1">
                  {(["all", "dashboard", "chart"] as const).map((k) => (
                    <button key={k} aria-pressed={show === k} onClick={() => setShow(k)}
                      className={clsx("rounded-md px-2.5 py-1 text-xs", show === k ? "bg-white font-semibold text-slate-900 shadow-sm" : "text-slate-600 hover:text-slate-900")}>
                      {t({ all: "All", dashboard: "Dashboards", chart: "Charts" }[k])}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-slate-500">
                    <th scope="col" className="border-b border-slate-200 px-5 py-2 text-start font-medium">{t("Name")}</th>
                    <th scope="col" className="border-b border-slate-200 px-3 py-2 text-start font-medium">{t("Type")}</th>
                    <th scope="col" className="hidden border-b border-slate-200 px-3 py-2 text-start font-medium sm:table-cell">{t("Workspace")}</th>
                    <th scope="col" className="hidden border-b border-slate-200 px-3 py-2 text-start font-medium md:table-cell">{t("Owner")}</th>
                    <th scope="col" className="border-b border-slate-200 px-5 py-2 text-end font-medium">{t("Changed")}</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((r) => (
                    <tr key={`${r.kind}-${r.id}`} className="hover:bg-slate-50">
                      <td className="border-b border-slate-100 px-5 py-3"><Link to={r.to} className="font-medium text-slate-900 hover:text-brand-700">{r.name}</Link></td>
                      <td className="border-b border-slate-100 px-3 py-3"><span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{t(r.kind === "chart" ? t("Chart") : t("Dashboard"))}</span></td>
                      <td className="hidden border-b border-slate-100 px-3 py-3 text-slate-500 sm:table-cell">{r.ws || t("Personal")}</td>
                      <td className="hidden border-b border-slate-100 px-3 py-3 text-slate-500 md:table-cell">{r.owner}</td>
                      <td className="whitespace-nowrap border-b border-slate-100 px-5 py-3 text-end text-slate-500">{formatMoment(r.at)}</td>
                    </tr>
                  ))}
                  {!recent.length && <tr><td colSpan={5} className="px-5 py-6 text-center text-slate-500">{t("Nothing yet.")}</td></tr>}
                </tbody>
              </table>
            </div>
          </Card>
        </section>

        <div className="flex min-w-0 flex-[1_1_320px] flex-col gap-6">
          {hasRole("creator") && (
            <section aria-labelledby="health-h">
              <Card className="space-y-3 p-5">
                <div className="flex items-center justify-between">
                  <h2 id="health-h" className="text-base font-semibold">{t("Data health")}</h2>
                  <Link to="/data" className="text-xs font-medium text-brand-700 hover:underline">{t("Open Data")}</Link>
                </div>
                {health.map(({ d, h }) => (
                  <Link key={d.id} to={`/data/datasets/${d.id}`} className="flex items-center justify-between gap-3 rounded-lg hover:bg-slate-50">
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-slate-900">{d.name}</span>
                      <span className="block truncate text-xs text-slate-500">
                        {d.last_synced_at ? t("Synced {when}", { when: formatMoment(d.last_synced_at) }) : t("Not synced yet")}
                      </span>
                    </span>
                    <HealthChip health={h} />
                  </Link>
                ))}
                {datasets.data && !datasets.data.length && <p className="text-sm text-slate-500">{t("No datasets yet.")}</p>}
              </Card>
            </section>
          )}
          <section aria-labelledby="ws-h">
            <Card className="space-y-2 p-5">
              <div className="flex items-center justify-between">
                <h2 id="ws-h" className="text-base font-semibold">{t("Your workspaces")}</h2>
                <Link to="/workspaces" className="text-xs font-medium text-brand-700 hover:underline">{t("All")}</Link>
              </div>
              {spaces.slice(0, 5).map((w) => (
                <Link key={w.id} to="/workspaces" className="flex items-center gap-3 rounded-lg py-1 hover:bg-slate-50">
                  <span aria-hidden="true" className="flex size-8 items-center justify-center rounded-lg bg-slate-100 text-brand-600"><Building2 size={16} /></span>
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-900">{w.name}</span>
                  <span className="text-xs text-slate-500">{t({ viewer: "Viewer", editor: "Editor", manager: "Manager" }[w.my_role])}</span>
                </Link>
              ))}
              {!spaces.length && <p className="text-sm text-slate-500">{t("You are not a member of any workspace yet.")}</p>}
            </Card>
          </section>
        </div>
      </div>
    </div>
  );
}
