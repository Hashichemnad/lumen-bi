import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Plus, Search, Star, Users } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Alert, Button, Card, Input, PageHeader, formatDate } from "../../components/ui";
import { del, get, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { emptyDashboard, type Dashboard, type DashboardSummary } from "../../lib/dashboard/types";
import { t } from "../../lib/i18n";
import { useWorkspace } from "../../lib/workspace";

type Show = "all" | "favorites" | "mine" | "shared";

export function FavoriteStar({ d }: { d: DashboardSummary }) {
  const qc = useQueryClient();
  const toggle = async (e: React.MouseEvent) => {
    e.preventDefault();
    if (d.favorite) await del(`/dashboards/${d.id}/favorite`);
    else await put(`/dashboards/${d.id}/favorite`, {});
    await qc.invalidateQueries({ queryKey: ["dashboards"] });
  };
  return (
    <button onClick={toggle} className="rounded p-1 text-slate-400 hover:text-amber-500" aria-label={d.favorite ? t("Remove from favourites") : t("Add to favourites")}>
      <Star size={16} className={d.favorite ? "fill-amber-400 text-amber-500" : ""} />
    </button>
  );
}

/** A small picture of the dashboard's first tab: its tiles where they sit on the grid. */
export function LayoutThumb({ layout }: { layout: DashboardSummary["layout"] }) {
  const rows = Math.max(12, ...layout.map((b) => b.y + b.h));
  const h = Math.min(rows, 40);
  return (
    <svg viewBox={`0 0 120 ${h * 2.4}`} preserveAspectRatio="xMidYMin meet" className="block h-24 w-full" aria-hidden="true">
      {layout.filter((b) => b.y < h).map((b, i) => (
        <rect key={i} x={b.x * 10 + 1} y={b.y * 2.4 + 1} width={b.w * 10 - 2} height={Math.max(1, b.h * 2.4 - 2)} rx="1.5"
          className={b.kind === "chart" ? "fill-brand-100" : b.kind === "section" ? "fill-slate-300" : "fill-slate-200"} />
      ))}
      {layout.filter((b) => b.kind === "chart" && b.y < h).slice(0, 6).map((b, i) => (
        <rect key={`m${i}`} x={b.x * 10 + 4} y={b.y * 2.4 + b.h * 2.4 - 5} width={Math.max(2, b.w * 10 - 8) * 0.6} height="2" rx="1" className="fill-brand-400" />
      ))}
    </svg>
  );
}

export function DashboardCard({ d }: { d: DashboardSummary }) {
  return (
    <Link to={`/dashboards/${d.id}`} className="group block">
      <Card className="flex h-full flex-col gap-3 p-4 transition-shadow group-hover:shadow-md">
        <div className="overflow-hidden rounded-lg bg-slate-50 p-2"><LayoutThumb layout={d.layout ?? []} /></div>
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="line-clamp-2 font-semibold leading-snug text-slate-900">{d.name}</div>
            {d.description && <div className="mt-0.5 line-clamp-1 text-sm text-slate-500">{d.description}</div>}
          </div>
          <FavoriteStar d={d} />
        </div>
        <div className="mt-auto flex items-center gap-1.5 text-xs text-slate-500">
          <span className="truncate">{d.workspace_name || d.owner_name}</span>·<span className="whitespace-nowrap">{formatDate(d.updated_at)}</span>
          {d.shared && <span className="ms-auto inline-flex items-center gap-1" title={t("Shared")}><Users size={12} /><span className="sr-only">{t("Shared")}</span></span>}
        </div>
      </Card>
    </Link>
  );
}

export default function DashboardList() {
  const { user, hasRole } = useAuth();
  const navigate = useNavigate();
  const { shows, target, current } = useWorkspace();
  const list = useQuery({ queryKey: ["dashboards"], queryFn: () => get<DashboardSummary[]>("/dashboards") });
  const [show, setShow] = useState<Show>("all");
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const create = async () => {
    setError("");
    try {
      const d = await post<Dashboard>("/dashboards", { name: t("Untitled dashboard"), config: emptyDashboard(), workspace_id: target });
      navigate(`/dashboards/${d.id}?edit=1`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not create the dashboard"));
    }
  };
  const items = (list.data ?? []).filter((d) => {
    if (!shows(d.workspace_id)) return false;
    if (q && !`${d.name} ${d.description}`.toLowerCase().includes(q.toLowerCase())) return false;
    if (show === "favorites") return d.favorite;
    if (show === "mine") return d.owner_id === user?.id;
    if (show === "shared") return d.owner_id !== user?.id;
    return true;
  });
  return (
    <>
      <PageHeader title={current ? current.name : t("Dashboards")} description={current?.description || t("Charts, filters and notes together, shared with the people who need them.")}
        actions={hasRole("explorer") ? <Button onClick={create}><Plus size={16} /> {t("New dashboard")}</Button> : undefined} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {(["all", "favorites", "mine", "shared"] as Show[]).map((s) => (
          <button key={s} onClick={() => setShow(s)}
            aria-pressed={show === s}
            className={clsx("min-h-9 rounded-full px-3.5 text-sm", show === s ? "bg-brand-600 font-medium text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50")}>
            {t({ all: "All", favorites: "Favourites", mine: "Mine", shared: "Shared with me" }[s])}
          </button>
        ))}
        <div className="relative ms-auto w-full sm:w-64">
          <Search size={14} className="absolute start-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("Search dashboards")} aria-label={t("Search dashboards")} className="py-1.5 ps-8" />
        </div>
      </div>
      {list.data && !items.length ? (
        <Card className="p-10 text-center text-sm text-slate-500">
          {list.data.length ? t("No dashboards match.") : hasRole("explorer") ? t("No dashboards yet. Create the first one.") : t("No dashboards have been shared with you yet.")}
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{items.map((d) => <DashboardCard key={d.id} d={d} />)}</div>
      )}
    </>
  );
}
