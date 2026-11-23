import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Alert, Badge, Button, Card, PageHeader, Table, formatDate } from "../../components/ui";
import { del, get, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { Connection, Dataset, Preview, SyncRun } from "../../lib/types";
import { RunBadge, StatusBadge, describeSchedule, formatNumber, sourceLabel } from "./common";
import CombineModal from "./CombineModal";
import DatasetForm from "./DatasetForm";
import ModelTab from "./ModelTab";
import QualityTab from "./QualityTab";
import LineageTab from "./LineageTab";
import SecurityTab from "./SecurityTab";
import { t as tr } from "../../lib/i18n";

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(value);
}

function duration(run: SyncRun): string {
  if (!run.started_at || !run.finished_at) return "";
  const s = (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000;
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.round(s / 60)} min`;
}

export default function DatasetPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { user, hasRole } = useAuth();
  const [tab, setTab] = useState<"preview" | "model" | "security" | "quality" | "lineage" | "history">("preview");
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");

  const ds = useQuery({
    queryKey: ["dataset", id],
    queryFn: () => get<Dataset>(`/datasets/${id}`),
    refetchInterval: (q) => (q.state.data?.active_run ? 2000 : false),
  });
  const version = ds.data?.current_version ?? 0;
  const preview = useQuery({
    queryKey: ["preview", id, version],
    queryFn: () => get<Preview>(`/datasets/${id}/preview?limit=100`),
    enabled: version > 0,
  });
  const runs = useQuery({
    queryKey: ["runs", id, ds.data?.active_run?.status, version],
    queryFn: () => get<SyncRun[]>(`/datasets/${id}/runs`),
  });
  const conn = useQuery({
    queryKey: ["connection", ds.data?.connection_id],
    queryFn: () => get<Connection>(`/connections/${ds.data!.connection_id}`),
    enabled: !!ds.data?.connection_id,
  });

  if (!ds.data) return ds.error ? <Alert>{(ds.error as Error).message}</Alert> : null;
  const d = ds.data;
  const canEdit = hasRole("admin") || d.owner_id === user?.id;
  const lastRun = runs.data?.[0];

  const syncNow = async () => {
    setError("");
    try {
      await post(`/datasets/${d.id}/sync`);
      await qc.invalidateQueries({ queryKey: ["dataset", id] });
      await qc.invalidateQueries({ queryKey: ["runs", id] });
    } catch (err) {
      setError(err instanceof Error ? err.message : tr("Could not start the sync"));
    }
  };
  const remove = async () => {
    if (!confirm(tr("Delete dataset \"{name}\" and its data copy?", { name: d.name }))) return;
    await del(`/datasets/${d.id}`);
    await qc.invalidateQueries({ queryKey: ["datasets"] });
    navigate("/data");
  };

  return (
    <>
      <Link to="/data" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"><ArrowLeft size={14} /> {tr("Data")}</Link>
      <PageHeader title={d.name} description={d.description || undefined}
        actions={<>
          <Button onClick={syncNow} disabled={!!d.active_run}>
            <RefreshCw size={16} className={d.active_run ? "animate-spin" : ""} /> {d.active_run ? tr("Syncing…") : tr("Sync now")}
          </Button>
          {canEdit && <Button variant="secondary" onClick={() => setEditing(true)} disabled={d.kind === "source" && !conn.data}>{tr("Settings")}</Button>}
          {canEdit && <Button variant="danger" onClick={remove}>{tr("Delete")}</Button>}
        </>} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {d.last_status === "failed" && lastRun?.status === "failed" && !d.active_run && (
        <div className="mb-4"><Alert>{tr("The last sync failed: {error}", { error: lastRun.error })}{d.current_version ? ` ${tr("The previous data is still in use.")}` : ""}</Alert></div>
      )}

      <div className="mb-6 grid gap-3 sm:grid-cols-4">
        {[
          [tr("Status"), <StatusBadge key="s" dataset={d} />],
          [tr("Rows"), d.current_version ? formatNumber(d.row_count) : "—"],
          [tr("Last synced"), formatDate(d.last_synced_at)],
          [tr("Refresh"), <span key="r">{describeSchedule(d.schedule)}{d.next_run_at && <span className="block text-xs text-slate-500">{tr("next {v0}", { v0: formatDate(d.next_run_at) })}</span>}</span>],
        ].map(([label, value]) => (
          <Card key={String(label)} className="p-4">
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
            <div className="mt-1 text-sm font-medium">{value}</div>
          </Card>
        ))}
      </div>
      {d.kind === "source" ? (
        <p className="mb-4 text-sm text-slate-600">
          {tr("From")} <Link className="text-brand-600 hover:underline" to={`/data/connections/${d.connection_id}`}>{d.connection_name}</Link>
          {" · "}{sourceLabel(d)}{" · "}{d.sync_mode === "incremental" ? `incremental by ${d.incremental_column}${d.primary_key.length ? `, key ${d.primary_key.join(" + ")}` : ""}` : tr("full refresh")}
        </p>
      ) : (
        <p className="mb-4 text-sm text-slate-600">
          {d.kind === "join" ? tr("Join of ") : tr("Union of ")}
          {d.parents.map((p, i) => <span key={p.id}>{i > 0 && (d.kind === "join" ? " + " : ", ")}
            <Link className="text-brand-600 hover:underline" to={`/data/datasets/${p.id}`}>{p.name}</Link></span>)}
          {" · rebuilt automatically when any of them syncs"}
        </p>
      )}
      {d.dependents.length > 0 && (
        <p className="-mt-2 mb-4 text-sm text-slate-600">{tr("Used by")} {d.dependents.map((x, i) => <span key={x.id}>{i > 0 && ", "}
          <Link className="text-brand-600 hover:underline" to={`/data/datasets/${x.id}`}>{x.name}</Link></span>)}</p>
      )}
      {d.source.sql && <pre className="mb-4 overflow-x-auto rounded-lg bg-slate-100 p-3 text-xs">{d.source.sql}</pre>}

      <div className="mb-3 flex gap-1 border-b border-slate-200">
        {(["preview", "model", "security", "quality", "lineage", "history"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={clsx("-mb-px border-b-2 px-3 py-2 text-sm font-medium capitalize",
              tab === t ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-700")}>
            {t === "history" ? tr("Sync history") : tr({ preview: "Preview", model: "Model", security: "Security", quality: "Quality", lineage: "Lineage" }[t])}
          </button>
        ))}
      </div>

      {tab === "preview" && (
        <Card>
          {!d.current_version ? (
            <p className="p-6 text-center text-sm text-slate-500">{d.active_run ? tr("Syncing… the preview appears when it finishes.") : tr("No data yet. Run a sync.")}</p>
          ) : (
            <>
              {preview.data?.restricted && (
                <p className="border-b border-slate-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">
                  {tr("Row-level security applies to you: only the rows you may see are shown.")}
                </p>
              )}
              <div className="max-h-[480px] overflow-auto">
                <table className="min-w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50">
                    <tr>{preview.data?.columns.map((c) => <th key={c} className="whitespace-nowrap px-3 py-2 text-start text-xs font-semibold text-slate-600">{c}</th>)}</tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {preview.data?.rows.map((r, i) => (
                      <tr key={i}>{r.map((v, j) => <td key={j} className="whitespace-nowrap px-3 py-1.5 tabular-nums">{cell(v)}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="border-t border-slate-200 px-4 py-2 text-xs text-slate-500">
                {tr("First")} {preview.data?.rows.length ?? 0} {tr("of")} {formatNumber(d.row_count)} {tr("rows · version")} {version}
              </p>
            </>
          )}
        </Card>
      )}

      {tab === "model" && <ModelTab datasetId={d.id} canEdit={canEdit} />}

      {tab === "security" && <SecurityTab datasetId={d.id} canEdit={canEdit} columns={(d.columns ?? []).map((c) => c.name)} dataset={d} />}
      {tab === "quality" && <QualityTab datasetId={d.id} columns={(d.columns ?? []).map((c) => c.name)} canEdit={canEdit} />}

      {tab === "lineage" && <LineageTab datasetId={d.id} />}
      {tab === "history" && (
        <Card>
          <Table head={[tr("Started"), tr("Trigger"), tr("Mode"), tr("Status"), tr("Rows"), tr("Time"), tr("Details")]}>
            {runs.data?.map((r) => (
              <tr key={r.id}>
                <td className="whitespace-nowrap px-4 py-2.5 text-slate-500">{formatDate(r.started_at ?? r.created_at)}</td>
                <td className="px-4 py-2.5 capitalize">{r.trigger}</td>
                <td className="px-4 py-2.5">{r.mode}</td>
                <td className="px-4 py-2.5"><RunBadge run={r} /></td>
                <td className="px-4 py-2.5 tabular-nums">
                  {r.status === "success" ? <>{formatNumber(r.rows_total)}{r.mode === "incremental" && <span className="text-xs text-slate-500">{tr("(+{v0} read)", { v0: formatNumber(r.rows_read) })}</span>}</> : "—"}
                </td>
                <td className="px-4 py-2.5 text-slate-500">{duration(r)}</td>
                <td className="px-4 py-2.5 text-xs">
                  {r.error && <span className="text-red-700">{r.error}</span>}
                  {r.details.note && <span className="text-slate-500">{r.details.note}</span>}
                  {r.details.schema_changes && (
                    <span className="flex flex-wrap gap-1">
                      {r.details.schema_changes.added?.map((c) => <Badge key={c} tone="green">+ {c}</Badge>)}
                      {r.details.schema_changes.removed?.map((c) => <Badge key={c} tone="red">− {c}</Badge>)}
                      {r.details.schema_changes.retyped?.map((c) => <Badge key={c.name} tone="amber">{c.name}: {c.from} → {c.to}</Badge>)}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      )}

      {editing && d.kind === "source" && conn.data && <DatasetForm connection={conn.data} existing={d} onClose={() => setEditing(false)} />}
      {editing && d.kind !== "source" && <CombineModal existing={d} onClose={() => setEditing(false)} />}
    </>
  );
}
