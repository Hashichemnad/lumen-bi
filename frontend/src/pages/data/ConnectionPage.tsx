import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Plus, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Alert, Button, Card, PageHeader, Table, formatDate } from "../../components/ui";
import { del, get, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { Connection, DataObject, Dataset } from "../../lib/types";
import { CONNECTION_LABELS, StatusBadge, formatBytes, formatNumber, sourceLabel } from "./common";
import { RestConnectionModal, SalesforceConnectionModal } from "./ApiConnectionForms";
import { DatabaseConnectionModal, UploadModal } from "./ConnectionForms";
import DatasetForm from "./DatasetForm";
import { t } from "../../lib/i18n";

export default function ConnectionPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { user, hasRole } = useAuth();
  const conn = useQuery({ queryKey: ["connection", id], queryFn: () => get<Connection>(`/connections/${id}`) });
  const datasets = useQuery({
    queryKey: ["datasets", "by-connection", id],
    queryFn: () => get<Dataset[]>(`/datasets?connection_id=${id}`),
  });
  const objects = useQuery({ queryKey: ["objects", id], queryFn: () => get<DataObject[]>(`/connections/${id}/objects`), retry: false });
  const [editing, setEditing] = useState(false);
  const [newDataset, setNewDataset] = useState<string | null>(null);
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null);

  if (!conn.data) return conn.error ? <Alert>{(conn.error as Error).message}</Alert> : null;
  const c = conn.data;
  const canEdit = hasRole("admin") || c.owner_id === user?.id;
  const cfg = c.config as Record<string, string | number | string[]>;
  const used = new Set(datasets.data?.map((d) => d.source.object).filter(Boolean));

  const remove = async () => {
    if (!confirm(t("Delete \"{name}\" and its {dataset_count} dataset(s)? Their data copies are deleted too.", { name: c.name, dataset_count: c.dataset_count }))) return;
    await del(`/connections/${c.id}`);
    await qc.invalidateQueries({ queryKey: ["connections"] });
    await qc.invalidateQueries({ queryKey: ["datasets"] });
    navigate("/data");
  };

  return (
    <>
      <Link to="/data" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"><ArrowLeft size={14} /> {t("Data")}</Link>
      <PageHeader title={c.name} description={t("{v0} · added by {v1}", { v0: t(CONNECTION_LABELS[c.type] ?? c.type), v1: c.owner_name || "—" })}
        actions={canEdit ? <>
          {c.type !== "file" && (
            <Button variant="secondary" onClick={async () => setTest(await post(`/connections/${c.id}/test`))}>{t("Test connection")}</Button>
          )}
          {c.type === "file"
            ? <Button variant="secondary" onClick={() => setEditing(true)}>{t("Replace file")}</Button>
            : <Button variant="secondary" onClick={() => setEditing(true)}>{t("Edit")}</Button>}
          <Button variant="danger" onClick={remove}>{t("Delete")}</Button>
        </> : undefined} />

      {test && (
        <div className={`mb-4 flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${test.ok ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-red-200 bg-red-50 text-red-800"}`}>
          {test.ok ? <CheckCircle2 size={18} /> : <XCircle size={18} />} {test.message}
        </div>
      )}

      <Card className="mb-6 p-5">
        <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
          {c.type === "file" ? <>
            <div className="flex justify-between"><dt className="text-slate-500">{t("File")}</dt><dd>{String(cfg.filename)}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Size")}</dt><dd>{formatBytes(Number(cfg.size ?? 0))}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Uploaded")}</dt><dd>{formatDate(String(cfg.uploaded_at ?? ""))}</dd></div>
          </> : c.type === "salesforce" ? <>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Org")}</dt><dd>{String(cfg.domain_url)}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Sign-in")}</dt><dd>{cfg.auth === "jwt" ? `JWT bearer as ${String(cfg.username)}` : t("Client credentials")}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("API version")}</dt><dd>{String(cfg.api_version || "newest")}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Bulk API from")}</dt><dd>{t("{v0} rows", { v0: Number(cfg.bulk_threshold).toLocaleString() })}</dd></div>
          </> : c.type === "rest" ? <>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Base URL")}</dt><dd>{String(cfg.base_url)}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Sign-in")}</dt><dd>{String(cfg.auth)}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Pages")}</dt><dd>{String(cfg.pagination)}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Incremental")}</dt><dd>{cfg.since_param ? `by ${String(cfg.since_param)}` : t("not set up")}</dd></div>
          </> : <>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Host")}</dt><dd>{String(cfg.host)}:{String(cfg.port)}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{c.type === "oracle" ? (cfg.sid ? "SID" : t("Service")) : t("Database")}</dt>
              <dd>{String(c.type === "oracle" ? cfg.sid || cfg.service_name : cfg.database)}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("User")}</dt><dd>{String(cfg.user)}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Schemas")}</dt>
              <dd>{Array.isArray(cfg.schemas) && cfg.schemas.length ? cfg.schemas.join(", ") : t("user's own")}</dd></div>
          </>}
        </dl>
      </Card>

      <h2 className="mb-3 text-lg font-semibold">{t("Datasets from this connection")}</h2>
      <Card className="mb-8">
        <Table head={[t("Dataset"), t("Source"), t("Rows"), t("Status")]}>
          {datasets.data?.map((d) => (
            <tr key={d.id} className="cursor-pointer hover:bg-slate-50" onClick={() => navigate(`/data/datasets/${d.id}`)}>
              <td className="px-4 py-3 font-medium">{d.name}</td>
              <td className="px-4 py-3 text-slate-600">{sourceLabel(d)}</td>
              <td className="px-4 py-3 tabular-nums">{d.current_version ? formatNumber(d.row_count) : "—"}</td>
              <td className="px-4 py-3"><StatusBadge dataset={d} /></td>
            </tr>
          ))}
        </Table>
        {datasets.data?.length === 0 && <p className="p-6 text-center text-sm text-slate-500">{t("None yet. Pick a table below.")}</p>}
      </Card>

      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">{{ file: "Sheets", salesforce: "Objects", rest: "Endpoints" }[c.type] ?? t("Tables and views")}</h2>
        {["oracle", "postgres", "salesforce"].includes(c.type) && (
          <Button variant="secondary" size="sm" onClick={() => setNewDataset("")}><Plus size={14} /> {t("Dataset from")} {c.type === "salesforce" ? "SOQL" : "SQL"} {t("query")}</Button>
        )}
      </div>
      <Card>
        {objects.error && <div className="p-4"><Alert>{(objects.error as Error).message}</Alert></div>}
        {objects.isLoading && <p className="p-4 text-sm text-slate-500">{t("Loading…")}</p>}
        <ul className="divide-y divide-slate-100">
          {objects.data?.map((o) => (
            <li key={o.name} className="flex items-center justify-between px-4 py-2.5 text-sm">
              <span><span className="font-medium">{o.label && o.label !== o.name ? `${o.label} ` : ""}</span>
                <span className={o.label && o.label !== o.name ? "font-mono text-xs text-slate-500" : "font-medium"}>{o.name}</span>{" "}
                <span className="text-slate-500">{o.kind}</span></span>
              {used.has(o.name)
                ? <span className="text-xs text-slate-500">{t("already a dataset")}</span>
                : <Button variant="ghost" size="sm" onClick={() => setNewDataset(o.name)}><Plus size={14} /> {t("Create dataset")}</Button>}
            </li>
          ))}
        </ul>
      </Card>

      {editing && c.type === "file" && <UploadModal replace={c} onClose={() => setEditing(false)} />}
      {editing && c.type === "salesforce" && <SalesforceConnectionModal existing={c} onClose={() => setEditing(false)} />}
      {editing && c.type === "rest" && <RestConnectionModal existing={c} onClose={() => setEditing(false)} />}
      {editing && (c.type === "oracle" || c.type === "postgres") && (
        <DatabaseConnectionModal type={c.type} existing={c} onClose={() => setEditing(false)} />
      )}
      {newDataset !== null && (
        <DatasetForm connection={c} initialObject={newDataset || undefined} onClose={() => setNewDataset(null)}
          onSaved={(d) => navigate(`/data/datasets/${d.id}`)} />
      )}
    </>
  );
}
