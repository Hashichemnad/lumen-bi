import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Circle } from "lucide-react";
import { Link } from "react-router-dom";

import { Card, PageHeader } from "../components/ui";
import { get } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { DashboardSummary } from "../lib/dashboard/types";
import { DashboardCard } from "./dashboards/DashboardList";

const ROADMAP = [
  { name: "Sign-in, users, roles and groups", done: true },
  { name: "Microsoft and UAE PASS sign-in", done: true },
  { name: "Data connections: files, Oracle, PostgreSQL, scheduled sync", done: true },
  { name: "Salesforce and REST API connections", done: true },
  { name: "Data modelling and calculated fields", done: true },
  { name: "Query engine with row-level security", done: true },
  { name: "Chart builder", done: true },
  { name: "Interactive dashboards", done: true },
  { name: "More chart types, KPI sections, PDF and Excel exports", done: true },
  { name: "New design, themes and Arabic", done: false },
  { name: "Email reports, alerts, embedding, KPI goals", done: false },
  { name: "AI and connecting Claude (MCP)", done: false },
  { name: "Admin tools and hardening", done: false },
];

export default function Home() {
  const { user } = useAuth();
  const dashboards = useQuery({ queryKey: ["dashboards"], queryFn: () => get<DashboardSummary[]>("/dashboards") });
  const favorites = (dashboards.data ?? []).filter((d) => d.favorite);
  const recent = (dashboards.data ?? []).filter((d) => !d.favorite).slice(0, 6 - Math.min(favorites.length, 6));
  const shown = [...favorites, ...recent].slice(0, 6);
  return (
    <>
      <PageHeader title={`Welcome, ${user?.name || user?.email}`} description="Your favourite and latest dashboards." />
      {shown.length > 0 ? (
        <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{shown.map((d) => <DashboardCard key={d.id} d={d} />)}</div>
      ) : dashboards.data ? (
        <Card className="mb-6 p-6 text-sm text-slate-500">No dashboards yet. <Link className="text-brand-600 hover:underline" to="/dashboards">Go to dashboards</Link></Card>
      ) : null}
      <Card className="p-5">
        <h2 className="font-semibold">Build progress</h2>
        <ul className="mt-3 space-y-2">
          {ROADMAP.map((item) => (
            <li key={item.name} className="flex items-center gap-2 text-sm">
              {item.done ? <CheckCircle2 size={18} className="text-emerald-600" /> : <Circle size={18} className="text-slate-300" />}
              <span className={item.done ? "" : "text-slate-500"}>{item.name}</span>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
