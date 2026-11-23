/** Admin → Usage: who uses Lumen, which dashboards are opened, and which are not. */
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { lazy, Suspense, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { Card, PageHeader } from "../../components/ui";
import { get } from "../../lib/api";
import { formatDay, formatMoment } from "../../lib/dates";
import { t } from "../../lib/i18n";

const TrendChart = lazy(() => import("./UsageChart"));

interface Report {
  days: number;
  totals: Record<"active_users" | "people" | "sign_ins" | "dashboard_views" | "chart_views" | "exports" | "ai" | "mcp", number>;
  daily: { date: string; active_users: number; dashboard_views: number }[];
  top_dashboards: { id: number; name: string; views: number; viewers: number; last_viewed_at: string | null }[];
  unused_dashboards: { id: number; name: string; updated_at: string; owner: string }[];
  top_users: { id: number; name: string; email: string; role: string; events: number }[];
  roles: Record<string, number>;
  never_signed_in: number;
  view_sources: Record<string, number>;
}

function Stat({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-bold tabular-nums text-slate-900">{value.toLocaleString()}</p>
      {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
    </Card>
  );
}

export default function Usage() {
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["usage", days], queryFn: () => get<Report>(`/admin/usage?days=${days}`) });
  const r = q.data;
  const labels = useMemo(() => (r?.daily ?? []).map((d) => formatDay(new Date(d.date + "T12:00:00"), { day: "numeric", month: "short" })), [r]);
  return (
    <>
      <PageHeader title={t("Usage")} description={t("Who uses Lumen and what they open. Reports sent by email are not counted as views.")}
        actions={
          <div role="group" aria-label={t("Period")} className="flex gap-1 rounded-lg bg-slate-100 p-1">
            {[7, 30, 90].map((d) => (
              <button key={d} aria-pressed={days === d} onClick={() => setDays(d)}
                className={clsx("rounded-md px-3 py-1.5 text-sm", days === d ? "bg-white font-semibold text-slate-900 shadow-sm" : "text-slate-600 hover:text-slate-900")}>
                {t("{n} days", { n: d })}
              </button>
            ))}
          </div>
        } />
      {r && (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3 xl:grid-cols-6">
            <Stat label={t("Active people")} value={r.totals.active_users} sub={t("of {n} accounts", { n: r.totals.people })} />
            <Stat label={t("Sign-ins")} value={r.totals.sign_ins} />
            <Stat label={t("Dashboard views")} value={r.totals.dashboard_views} sub={r.view_sources.embed ? t("{n} embedded", { n: r.view_sources.embed }) : undefined} />
            <Stat label={t("Exports")} value={r.totals.exports} />
            <Stat label={t("AI requests")} value={r.totals.ai} />
            <Stat label={t("Claude (MCP) requests")} value={r.totals.mcp} />
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card className="p-5">
              <h2 className="text-sm font-semibold">{t("Active people per day")}</h2>
              <Suspense fallback={<div className="h-52" />}><TrendChart labels={labels} values={r.daily.map((d) => d.active_users)} name={t("Active people")} /></Suspense>
            </Card>
            <Card className="p-5">
              <h2 className="text-sm font-semibold">{t("Dashboard views per day")}</h2>
              <Suspense fallback={<div className="h-52" />}><TrendChart labels={labels} values={r.daily.map((d) => d.dashboard_views)} name={t("Dashboard views")} /></Suspense>
            </Card>
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card className="p-5">
              <h2 className="text-sm font-semibold">{t("Most viewed dashboards")}</h2>
              <table className="mt-2 w-full text-sm">
                <thead><tr className="text-xs text-slate-500">
                  <th className="border-b border-slate-200 py-2 text-start font-medium">{t("Dashboard")}</th>
                  <th className="border-b border-slate-200 py-2 text-end font-medium">{t("Views")}</th>
                  <th className="border-b border-slate-200 py-2 text-end font-medium">{t("People")}</th>
                  <th className="border-b border-slate-200 py-2 text-end font-medium">{t("Last viewed")}</th>
                </tr></thead>
                <tbody>
                  {r.top_dashboards.map((d) => (
                    <tr key={d.id}>
                      <td className="border-b border-slate-100 py-2"><Link to={`/dashboards/${d.id}`} className="font-medium hover:text-brand-700">{d.name}</Link></td>
                      <td className="border-b border-slate-100 py-2 text-end tabular-nums">{d.views.toLocaleString()}</td>
                      <td className="border-b border-slate-100 py-2 text-end tabular-nums">{d.viewers}</td>
                      <td className="border-b border-slate-100 py-2 text-end text-slate-500">{d.last_viewed_at ? formatMoment(d.last_viewed_at) : "—"}</td>
                    </tr>
                  ))}
                  {!r.top_dashboards.length && <tr><td colSpan={4} className="py-4 text-center text-slate-500">{t("No views in this period.")}</td></tr>}
                </tbody>
              </table>
            </Card>
            <Card className="p-5">
              <h2 className="text-sm font-semibold">{t("Not opened in this period")}</h2>
              <p className="text-xs text-slate-500">{t("Candidates to tidy up or to remind people about.")}</p>
              <ul className="mt-2 divide-y divide-slate-100 text-sm">
                {r.unused_dashboards.map((d) => (
                  <li key={d.id} className="flex justify-between gap-3 py-2">
                    <Link to={`/dashboards/${d.id}`} className="truncate font-medium hover:text-brand-700">{d.name}</Link>
                    <span className="shrink-0 text-xs text-slate-500">{d.owner} · {t("changed {when}", { when: formatMoment(d.updated_at) })}</span>
                  </li>
                ))}
                {!r.unused_dashboards.length && <li className="py-4 text-center text-slate-500">{t("Every dashboard was opened.")}</li>}
              </ul>
            </Card>
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card className="p-5">
              <h2 className="text-sm font-semibold">{t("Most active people")}</h2>
              <ul className="mt-2 divide-y divide-slate-100 text-sm">
                {r.top_users.map((u) => (
                  <li key={u.id} className="flex items-center justify-between gap-3 py-2">
                    <span className="min-w-0"><span className="block truncate font-medium">{u.name}</span><span className="block truncate text-xs text-slate-500">{u.email}</span></span>
                    <span className="shrink-0 text-xs text-slate-500"><span className="capitalize">{t(u.role)}</span> · {t("{n} views", { n: u.events })}</span>
                  </li>
                ))}
              </ul>
            </Card>
            <Card className="p-5">
              <h2 className="text-sm font-semibold">{t("Accounts by role")}</h2>
              <ul className="mt-3 space-y-2 text-sm">
                {(["admin", "creator", "explorer", "viewer"] as const).map((role) => {
                  const n = r.roles[role] ?? 0;
                  const pct = r.totals.people ? (n / r.totals.people) * 100 : 0;
                  return (
                    <li key={role} className="grid grid-cols-[90px_1fr_40px] items-center gap-3">
                      <span className="capitalize text-slate-600">{t(role)}</span>
                      <span className="h-2 overflow-hidden rounded-full bg-slate-100"><span className="block h-full rounded-full bg-brand-600" style={{ width: `${pct}%` }} /></span>
                      <span className="text-end tabular-nums">{n}</span>
                    </li>
                  );
                })}
              </ul>
              {r.never_signed_in > 0 && <p className="mt-4 text-xs text-slate-500">{t("{n} people have never signed in.", { n: r.never_signed_in })}</p>}
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
