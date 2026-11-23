/** Data quality and freshness checks of a dataset, and column masking (PII). */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, Check, Clock, EyeOff, Play, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Alert, Badge, Button, Card, Field, Input, Modal, Select } from "../../components/ui";
import { del, get, post, put } from "../../lib/api";
import { formatMoment } from "../../lib/dates";
import type { DataCheckItem } from "../../lib/governance";
import { t } from "../../lib/i18n";

const KINDS: { kind: DataCheckItem["kind"]; label: string; help: string }[] = [
  { kind: "freshness", label: "Freshness", help: "The data (or the newest date in a column) is not older than a number of hours" },
  { kind: "row_count", label: "Number of rows", help: "The dataset has at least (or at most) this many rows" },
  { kind: "not_null", label: "No empty values", help: "A column is filled in (or at most a share of it is empty)" },
  { kind: "unique", label: "No repeats", help: "Each value of a column appears once, e.g. ticket numbers" },
  { kind: "range", label: "Values in a range", help: "Numbers stay between a minimum and a maximum" },
  { kind: "allowed", label: "Allowed values", help: "A column only has values from a list, e.g. Open, Closed" },
];

function StatusIcon({ s }: { s: DataCheckItem["last_status"] }) {
  if (s === "pass") return <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-700"><Check size={12} strokeWidth={3} /> {t("Passed")}</span>;
  if (s === "never") return <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600"><Clock size={12} /> {t("Not run yet")}</span>;
  return <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700"><AlertTriangle size={12} /> {s === "error" ? t("Could not run") : t("Failed")}</span>;
}

function CheckModal({ datasetId, columns, existing, onClose }: { datasetId: number; columns: string[]; existing?: DataCheckItem; onClose: () => void }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState<DataCheckItem["kind"]>(existing?.kind ?? "freshness");
  const [cfg, setCfg] = useState<Record<string, unknown>>(existing?.config ?? { hours: 24 });
  const [severity, setSeverity] = useState(existing?.severity ?? "warning");
  const [error, setError] = useState("");
  const set = (k: string, v: unknown) => setCfg({ ...cfg, [k]: v });
  const num = (k: string) => (cfg[k] === null || cfg[k] === undefined ? "" : String(cfg[k]));
  const save = async () => {
    setError("");
    try {
      const body = { kind, config: cfg, severity, enabled: true };
      if (existing) await put(`/checks/${existing.id}`, body);
      else await post(`/datasets/${datasetId}/checks`, body);
      await qc.invalidateQueries({ queryKey: ["checks", datasetId] });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };
  const column = (required = true) => (
    <Field label={required ? t("Column") : t("Date column (optional)")} hint={required ? undefined : t("Empty: when the data was last synced")}>
      <Select value={String(cfg.column ?? "")} onChange={(e) => set("column", e.target.value || null)}>
        <option value="">{required ? t("Choose a column") : t("When it was last synced")}</option>
        {columns.map((c) => <option key={c} value={c}>{c}</option>)}
      </Select>
    </Field>
  );
  return (
    <Modal open title={existing ? t("Edit check") : t("New check")} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button><Button onClick={save}>{t("Save and run")}</Button></>}>
      {error && <Alert>{error}</Alert>}
      <Field label={t("Check")}>
        <Select value={kind} onChange={(e) => { setKind(e.target.value as DataCheckItem["kind"]); setCfg(e.target.value === "freshness" ? { hours: 24 } : {}); }}>
          {KINDS.map((k) => <option key={k.kind} value={k.kind}>{t(k.label)}</option>)}
        </Select>
      </Field>
      <p className="text-sm text-slate-500">{t(KINDS.find((k) => k.kind === kind)!.help)}</p>
      {kind === "freshness" && (
        <>
          <Field label={t("At most this many hours old")}><Input type="number" min={1} value={num("hours")} onChange={(e) => set("hours", Number(e.target.value))} /></Field>
          {column(false)}
        </>
      )}
      {(kind === "row_count" || kind === "range") && (
        <>
          {kind === "range" && column()}
          <div className="grid grid-cols-2 gap-3">
            <Field label={t("Minimum")}><Input type="number" value={num("min")} onChange={(e) => set("min", e.target.value === "" ? null : Number(e.target.value))} /></Field>
            <Field label={t("Maximum")}><Input type="number" value={num("max")} onChange={(e) => set("max", e.target.value === "" ? null : Number(e.target.value))} /></Field>
          </div>
        </>
      )}
      {kind === "not_null" && (
        <>
          {column()}
          <Field label={t("Empty values allowed (%)")}><Input type="number" min={0} max={100} value={num("max_percent") || "0"} onChange={(e) => set("max_percent", Number(e.target.value))} /></Field>
        </>
      )}
      {kind === "unique" && column()}
      {kind === "allowed" && (
        <>
          {column()}
          <Field label={t("Allowed values")} hint={t("Comma-separated. Empty = any value.")}>
            <Input value={((cfg.values as string[]) ?? []).join(", ")} onChange={(e) => set("values", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
          </Field>
        </>
      )}
      <Field label={t("Importance")}>
        <Select value={severity} onChange={(e) => setSeverity(e.target.value as "warning" | "error")}>
          <option value="warning">{t("Warning")}</option>
          <option value="error">{t("Error")}</option>
        </Select>
      </Field>
    </Modal>
  );
}

export default function QualityTab({ datasetId, columns, canEdit }: { datasetId: number; columns: string[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["checks", datasetId], queryFn: () => get<DataCheckItem[]>(`/datasets/${datasetId}/checks`) });
  const [editing, setEditing] = useState<DataCheckItem | "new" | null>(null);
  const [running, setRunning] = useState(false);
  const run = async () => {
    setRunning(true);
    try {
      qc.setQueryData(["checks", datasetId], await post<DataCheckItem[]>(`/datasets/${datasetId}/checks/run`));
    } finally {
      setRunning(false);
    }
  };
  const failing = (list.data ?? []).filter((c) => c.last_status === "fail" || c.last_status === "error").length;
  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="font-semibold">{t("Data checks")}</h2>
            <p className="text-sm text-slate-500">{t("They run after every sync. The owner gets an email when one starts failing, and dashboards show it.")}</p>
          </div>
          <div className="flex gap-2">
            {!!list.data?.length && <Button variant="secondary" onClick={run} disabled={running}><Play size={15} /> {running ? t("Running…") : t("Run now")}</Button>}
            {canEdit && <Button onClick={() => setEditing("new")}><Plus size={15} /> {t("Add check")}</Button>}
          </div>
        </div>
        {failing > 0 && <div className="mb-3"><Alert>{t(failing === 1 ? "1 data check failing" : "{n} data checks failing", { n: failing })}</Alert></div>}
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
          {list.data?.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{c.description}</span>
                <span className="block text-xs text-slate-500">
                  {c.last_message || (c.last_value ? t("Now: {value}", { value: c.last_value }) : "")}{c.last_run_at ? ` · ${formatMoment(c.last_run_at)}` : ""}
                </span>
              </span>
              {c.severity === "error" && <Badge tone="red">{t("Error")}</Badge>}
              <StatusIcon s={c.last_status} />
              {canEdit && <Button size="sm" variant="secondary" onClick={() => setEditing(c)}>{t("Edit")}</Button>}
              {canEdit && <Button size="sm" variant="ghost" aria-label={t("Delete")} onClick={async () => { await del(`/checks/${c.id}`); await qc.invalidateQueries({ queryKey: ["checks", datasetId] }); }}><Trash2 size={14} /></Button>}
            </li>
          ))}
          {list.data && !list.data.length && <li className="px-3 py-6 text-center text-sm text-slate-500">{t("No checks yet. Add one to be told when the data is late or looks wrong.")}</li>}
        </ul>
      </Card>
      {editing && <CheckModal datasetId={datasetId} columns={columns} existing={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

interface Mask {
  column: string;
  method: "full" | "partial" | "hash";
  exempt_groups: number[];
  inherited_from: { id: number; name: string } | null;
}

const METHODS: { value: Mask["method"]; label: string; example: string }[] = [
  { value: "full", label: "Hide completely", example: "••••" },
  { value: "partial", label: "Show the last 4 characters", example: "••••4567 · a•••@company.ae" },
  { value: "hash", label: "Replace with a code", example: "3f9a1c07be" },
];

export function MaskingCard({ datasetId, columns, canEdit }: { datasetId: number; columns: string[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const masks = useQuery({ queryKey: ["masks", datasetId], queryFn: () => get<Mask[]>(`/datasets/${datasetId}/masks`), enabled: canEdit });
  const groups = useQuery({ queryKey: ["directory"], queryFn: () => get<{ groups: { id: number; name: string }[] }>("/directory"), enabled: canEdit });
  const [rows, setRows] = useState<Mask[]>([]);
  const [msg, setMsg] = useState<{ tone: "red" | "green"; text: string } | null>(null);
  useEffect(() => {
    if (masks.data) setRows(masks.data.filter((m) => !m.inherited_from));
  }, [masks.data]);
  if (!canEdit) return null;
  const inherited = (masks.data ?? []).filter((m) => m.inherited_from);
  const save = async () => {
    setMsg(null);
    try {
      await put(`/datasets/${datasetId}/masks`, rows.map(({ column, method, exempt_groups }) => ({ column, method, exempt_groups })));
      await qc.invalidateQueries({ queryKey: ["masks", datasetId] });
      await qc.invalidateQueries({ queryKey: ["preview", datasetId] });
      setMsg({ tone: "green", text: t("Saved") });
    } catch (err) {
      setMsg({ tone: "red", text: err instanceof Error ? err.message : t("Could not save") });
    }
  };
  return (
    <Card className="p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-2 font-semibold"><EyeOff size={18} /> {t("Hide personal data (masking)")}</h2>
          <p className="text-sm text-slate-500">{t("Masked columns show hidden values in charts, filters, exports and the rows behind charts. Admins and the groups you choose see real values.")}</p>
        </div>
        <Button onClick={save}>{t("Save")}</Button>
      </div>
      {msg && <div className="mb-3"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
      <ul className="space-y-2">
        {rows.map((m, i) => (
          <li key={i} className="grid gap-2 rounded-lg border border-slate-200 p-3 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end">
            <Field label={t("Column")}>
              <Select value={m.column} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, column: e.target.value } : x)))}>
                {columns.map((c) => <option key={c} value={c}>{c}</option>)}
              </Select>
            </Field>
            <Field label={t("How")} hint={METHODS.find((x) => x.value === m.method)?.example}>
              <Select value={m.method} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, method: e.target.value as Mask["method"] } : x)))}>
                {METHODS.map((x) => <option key={x.value} value={x.value}>{t(x.label)}</option>)}
              </Select>
            </Field>
            <Field label={t("Groups that see real values")}>
              <Select value="" onChange={(e) => e.target.value && setRows(rows.map((x, j) => (j === i ? { ...x, exempt_groups: [...new Set([...x.exempt_groups, Number(e.target.value)])] } : x)))}>
                <option value="">{m.exempt_groups.length ? m.exempt_groups.map((g) => groups.data?.groups.find((x) => x.id === g)?.name ?? g).join(", ") : t("Only admins")}</option>
                {groups.data?.groups.filter((g) => !m.exempt_groups.includes(g.id)).map((g) => <option key={g.id} value={g.id}>{t("Add {name}", { name: g.name })}</option>)}
              </Select>
            </Field>
            <div className="flex gap-1">
              {m.exempt_groups.length > 0 && <Button size="sm" variant="ghost" onClick={() => setRows(rows.map((x, j) => (j === i ? { ...x, exempt_groups: [] } : x)))}>{t("Only admins")}</Button>}
              <Button size="sm" variant="ghost" aria-label={t("Remove")} onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 size={14} /></Button>
            </div>
          </li>
        ))}
      </ul>
      <Button className={clsx("mt-3")} variant="secondary" disabled={!columns.length}
        onClick={() => setRows([...rows, { column: columns.find((c) => !rows.some((r) => r.column === c)) ?? columns[0], method: "full", exempt_groups: [], inherited_from: null }])}>
        <Plus size={15} /> {t("Mask a column")}
      </Button>
      {inherited.length > 0 && (
        <div className="mt-4 space-y-1 text-sm text-slate-600">
          {inherited.map((m) => <p key={m.column}><Badge>{t("Inherited")}</Badge> {m.column}: {t(METHODS.find((x) => x.value === m.method)?.label ?? "")} · {t("from {name}", { name: m.inherited_from!.name })}</p>)}
        </div>
      )}
    </Card>
  );
}
