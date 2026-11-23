/** Dataset → Lineage: where the data comes from and everything built on it; impact of changing a field. */
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Bell, Database, LayoutDashboard, Mail, PieChart, Plug, ShieldCheck } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { Card, Select } from "../../components/ui";
import { get } from "../../lib/api";
import { t } from "../../lib/i18n";
import type { Field } from "../../lib/types";

interface Ref { id: number; name: string; kind: string; last_status: string }
interface Up extends Ref { connection: { id: number; name: string; type: string } | null; parents: Up[] }
interface Impact {
  datasets: Ref[];
  charts: { id: number; name: string; dataset_id: number; dashboards: string[] }[];
  dashboards: { id: number; name: string }[];
  subscriptions: { id: number; name: string; dashboard_id: number }[];
  alerts: { id: number; name: string; dashboard_id: number }[];
  checks: { id: number; kind: string }[];
  access_table_for: Ref[];
  calculated_fields: string[];
}
interface Lineage {
  dataset: Ref & { connection: Up["connection"]; row_count: number };
  upstream: Up[];
  downstream: Impact;
}

function Node({ icon, children, to, tone = "slate" }: { icon: ReactNode; children: ReactNode; to?: string; tone?: "slate" | "brand" | "red" }) {
  const cls = { slate: "border-slate-200 bg-white", brand: "border-brand-600 bg-brand-50 font-semibold text-brand-700", red: "border-red-200 bg-red-50 text-red-800" }[tone];
  const body = <span className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${cls}`}>{icon}<span className="min-w-0 truncate">{children}</span></span>;
  return to ? <Link to={to} className="block hover:opacity-80">{body}</Link> : body;
}

function Column({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-w-0 flex-1 space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      {children}
    </div>
  );
}

function UpList({ items }: { items: Up[] }) {
  return (
    <ul className="space-y-2">
      {items.map((u) => (
        <li key={u.id} className="space-y-1">
          <Node icon={<Database size={15} />} to={`/data/datasets/${u.id}`} tone={u.last_status === "failed" ? "red" : "slate"}>{u.name}</Node>
          {u.connection && <div className="ms-4"><Node icon={<Plug size={14} />} to={`/data/connections/${u.connection.id}`}>{u.connection.name}</Node></div>}
          {u.parents.length > 0 && <div className="ms-4"><UpList items={u.parents} /></div>}
        </li>
      ))}
    </ul>
  );
}

const SHOWN = 6;

function ImpactList({ d }: { d: Impact }) {
  const [allCharts, setAllCharts] = useState(false);
  const empty = !d.datasets.length && !d.charts.length && !d.dashboards.length && !d.access_table_for.length && !d.calculated_fields.length;
  if (empty) return <p className="text-sm text-slate-500">{t("Nothing uses it yet.")}</p>;
  return (
    <div className="space-y-2">
      {d.calculated_fields.map((f) => <Node key={f} icon={<span className="w-4 text-center text-xs font-bold">ƒ</span>}>{f}</Node>)}
      {d.datasets.map((x) => <Node key={`d${x.id}`} icon={<Database size={15} />} to={`/data/datasets/${x.id}`}>{x.name}</Node>)}
      {d.access_table_for.map((x) => <Node key={`a${x.id}`} icon={<ShieldCheck size={15} />} to={`/data/datasets/${x.id}`}>{t("Access table for {name}", { name: x.name })}</Node>)}
      {d.dashboards.map((x) => <Node key={`b${x.id}`} icon={<LayoutDashboard size={15} />} to={`/dashboards/${x.id}`}>{x.name}</Node>)}
      {(allCharts ? d.charts : d.charts.slice(0, SHOWN)).map((c) => (
        <Node key={`c${c.id}`} icon={<PieChart size={15} />} to={`/charts/${c.id}`}>{c.name}</Node>
      ))}
      {d.charts.length > SHOWN && (
        <button type="button" className="text-xs font-medium text-brand-700 hover:underline" onClick={() => setAllCharts((v) => !v)}>
          {allCharts ? t("Show fewer") : t("Show all {n} charts", { n: d.charts.length })}
        </button>
      )}
      {(d.subscriptions.length > 0 || d.alerts.length > 0) && (
        <p className="flex flex-wrap gap-3 pt-1 text-xs text-slate-500">
          {d.subscriptions.length > 0 && <span className="inline-flex items-center gap-1"><Mail size={13} /> {t("{n} email subscriptions", { n: d.subscriptions.length })}</span>}
          {d.alerts.length > 0 && <span className="inline-flex items-center gap-1"><Bell size={13} /> {t("{n} alerts", { n: d.alerts.length })}</span>}
          {d.checks.length > 0 && <span>{t("{n} data checks", { n: d.checks.length })}</span>}
        </p>
      )}
    </div>
  );
}

export default function LineageTab({ datasetId }: { datasetId: number }) {
  const q = useQuery({ queryKey: ["lineage", datasetId], queryFn: () => get<Lineage>(`/lineage/datasets/${datasetId}`) });
  const fields = useQuery({ queryKey: ["fields", datasetId], queryFn: () => get<Field[]>(`/datasets/${datasetId}/fields`) });
  const [field, setField] = useState("");
  const impact = useQuery({
    queryKey: ["impact", datasetId, field],
    queryFn: () => get<Impact>(`/lineage/datasets/${datasetId}/impact?field=${encodeURIComponent(field)}`),
    enabled: !!field,
  });
  if (!q.data) return null;
  const l = q.data;
  return (
    <div className="space-y-6">
      <Card className="p-5">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
          <Column title={t("Comes from")}>
            {l.dataset.connection && <Node icon={<Plug size={15} />} to={`/data/connections/${l.dataset.connection.id}`}>{l.dataset.connection.name}</Node>}
            {l.upstream.length > 0 && <UpList items={l.upstream} />}
            {!l.dataset.connection && !l.upstream.length && <p className="text-sm text-slate-500">—</p>}
          </Column>
          <ArrowRight size={18} className="flip-rtl hidden shrink-0 self-center text-slate-400 lg:block" />
          <Column title={t("This dataset")}>
            <Node icon={<Database size={15} />} tone="brand">{l.dataset.name}</Node>
            <p className="text-xs text-slate-500">{l.dataset.row_count.toLocaleString()} {t("rows")}</p>
          </Column>
          <ArrowRight size={18} className="flip-rtl hidden shrink-0 self-center text-slate-400 lg:block" />
          <Column title={t("Used by")}><ImpactList d={l.downstream} /></Column>
        </div>
      </Card>
      <Card className="p-5">
        <h3 className="font-semibold">{t("Before you change a field")}</h3>
        <p className="mt-1 text-sm text-slate-500">{t("See which calculated fields, charts and dashboards use it, so renaming or removing it breaks nothing by surprise.")}</p>
        <div className="mt-3 max-w-xs">
          <Select value={field} onChange={(e) => setField(e.target.value)} aria-label={t("Field")}>
            <option value="">{t("Choose a field")}</option>
            {(fields.data ?? []).map((f) => <option key={f.id} value={f.name}>{f.label || f.name}</option>)}
          </Select>
        </div>
        {impact.data && <div className="mt-4"><ImpactList d={impact.data} /></div>}
      </Card>
    </div>
  );
}
