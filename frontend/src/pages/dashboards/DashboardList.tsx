import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { LayoutDashboard, Plus, Search, Star, Users } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Alert, Button, Card, Input, PageHeader, formatDate } from "../../components/ui";
import { del, get, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { emptyDashboard, type Dashboard, type DashboardSummary } from "../../lib/dashboard/types";

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
    <button onClick={toggle} className="rounded p-1 text-slate-300 hover:text-amber-500" aria-label={d.favorite ? "Remove from favourites" : "Add to favourites"}>
      <Star size={16} className={d.favorite ? "fill-amber-400 text-amber-500" : ""} />
    </button>
  );
}

export function DashboardCard({ d }: { d: DashboardSummary }) {
  return (
    <Link to={`/dashboards/${d.id}`} className="group block">
      <Card className="h-full p-4 transition-shadow group-hover:shadow-md">
        <div className="flex items-start gap-2">
          <LayoutDashboard size={18} className="mt-0.5 shrink-0 text-brand-600" />
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium text-slate-900">{d.name}</div>
            {d.description && <div className="mt-0.5 line-clamp-2 text-sm text-slate-500">{d.description}</div>}
          </div>
          <FavoriteStar d={d} />
        </div>
        <div className="mt-3 flex items-center gap-2 text-xs text-slate-500">
          <span>{d.owner_name}</span>·<span>{formatDate(d.updated_at)}</span>
          {d.shared && <span className="ms-auto inline-flex items-center gap-1"><Users size={12} /> Shared</span>}
        </div>
      </Card>
    </Link>
  );
}

export default function DashboardList() {
  const { user, hasRole } = useAuth();
  const navigate = useNavigate();
  const list = useQuery({ queryKey: ["dashboards"], queryFn: () => get<DashboardSummary[]>("/dashboards") });
  const [show, setShow] = useState<Show>("all");
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const create = async () => {
    setError("");
    try {
      const d = await post<Dashboard>("/dashboards", { name: "Untitled dashboard", config: emptyDashboard() });
      navigate(`/dashboards/${d.id}?edit=1`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the dashboard");
    }
  };
  const items = (list.data ?? []).filter((d) => {
    if (q && !`${d.name} ${d.description}`.toLowerCase().includes(q.toLowerCase())) return false;
    if (show === "favorites") return d.favorite;
    if (show === "mine") return d.owner_id === user?.id;
    if (show === "shared") return d.owner_id !== user?.id;
    return true;
  });
  return (
    <>
      <PageHeader title="Dashboards" description="Charts, filters and notes together, shared with the people who need them."
        actions={hasRole("explorer") ? <Button onClick={create}><Plus size={16} /> New dashboard</Button> : undefined} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {(["all", "favorites", "mine", "shared"] as Show[]).map((s) => (
          <button key={s} onClick={() => setShow(s)}
            className={clsx("rounded-full px-3 py-1 text-sm", show === s ? "bg-brand-600 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50")}>
            {{ all: "All", favorites: "Favourites", mine: "Mine", shared: "Shared with me" }[s]}
          </button>
        ))}
        <div className="relative ms-auto w-64">
          <Search size={14} className="absolute start-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search dashboards" className="py-1.5 ps-8" />
        </div>
      </div>
      {list.data && !items.length ? (
        <Card className="p-10 text-center text-sm text-slate-500">
          {list.data.length ? "No dashboards match." : hasRole("explorer") ? "No dashboards yet. Create the first one." : "No dashboards have been shared with you yet."}
        </Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{items.map((d) => <DashboardCard key={d.id} d={d} />)}</div>
      )}
    </>
  );
}
