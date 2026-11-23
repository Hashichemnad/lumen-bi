import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Alert, Button, Field, Input, Modal, Select } from "../../components/ui";
import { get, post, put } from "../../lib/api";
import type { Column, Connection, ConnectionType, DataObject, Dataset } from "../../lib/types";
import { buildCron, parseCron, useDataSettings, type Preset } from "./common";

export default function DatasetForm({
  connection,
  existing,
  initialObject,
  onClose,
  onSaved,
}: {
  connection: Connection;
  existing?: Dataset;
  initialObject?: string;
  onClose: () => void;
  onSaved?: (d: Dataset) => void;
}) {
  const qc = useQueryClient();
  const settings = useDataSettings();
  const types = useQuery({ queryKey: ["connection-types"], queryFn: () => get<ConnectionType[]>("/connection-types") });
  const caps = types.data?.find((t) => t.type === connection.type);
  const objects = useQuery({
    queryKey: ["objects", connection.id],
    queryFn: () => get<DataObject[]>(`/connections/${connection.id}/objects`),
  });

  const parsed = parseCron(existing?.schedule ?? null);
  const isSf = connection.type === "salesforce";
  const restNoSince = connection.type === "rest" && !(connection.config as Record<string, unknown>).since_param;
  const canIncremental = !!caps?.supports_incremental && !restNoSince;
  const [name, setName] = useState(existing?.name ?? initialObject?.split(".").pop() ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [kind, setKind] = useState<"object" | "sql">(existing?.source.sql ? "sql" : "object");
  const [object, setObject] = useState(existing?.source.object ?? initialObject ?? "");
  const [sql, setSql] = useState(existing?.source.sql ?? "");
  const [mode, setMode] = useState<"full" | "incremental">(existing?.sync_mode ?? "full");
  // Salesforce: SystemModstamp changes on every edit and Id is the key, so they are good defaults
  const [incCol, setIncCol] = useState(existing?.incremental_column ?? (connection.type === "salesforce" ? "SystemModstamp" : ""));
  const [pk, setPk] = useState<string[]>(existing?.primary_key ?? (connection.type === "salesforce" ? ["Id"] : []));
  const [preset, setPreset] = useState<Preset>(parsed.preset);
  const [time, setTime] = useState(parsed.time);
  const [custom, setCustom] = useState(parsed.preset === "custom" ? existing?.schedule ?? "" : "");
  const [columns, setColumns] = useState<Column[]>(existing?.columns ?? []);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    if (!object && objects.data?.length === 1) setObject(objects.data[0].name);
  }, [objects.data, object]);

  const source = kind === "sql" ? { sql } : { object };

  const loadColumns = async () => {
    setChecking(true);
    setError("");
    try {
      setColumns(await post<Column[]>(`/connections/${connection.id}/schema`, source));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not read the columns");
    } finally {
      setChecking(false);
    }
  };

  // Columns are needed to pick the incremental column and key; load them for the chosen table
  useEffect(() => {
    if (mode === "incremental" && kind === "object" && object && !columns.length) void loadColumns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, object, kind]);

  const save = async () => {
    setError("");
    const body = {
      name: name || object, description, connection_id: connection.id, source,
      sync_mode: mode, incremental_column: mode === "incremental" ? incCol || null : null,
      primary_key: mode === "incremental" ? pk : [], schedule: buildCron(preset, time, custom),
    };
    try {
      const saved = existing
        ? await put<Dataset>(`/datasets/${existing.id}`, body)
        : await post<Dataset>("/datasets", body);
      await qc.invalidateQueries({ queryKey: ["datasets"] });
      await qc.invalidateQueries({ queryKey: ["dataset", saved.id] });
      await qc.invalidateQueries({ queryKey: ["connections"] });
      onSaved?.(saved);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    }
  };

  return (
    <Modal open wide title={existing ? `Edit ${existing.name}` : `New dataset from ${connection.name}`} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button onClick={save}>{existing ? "Save" : "Create and sync"}</Button></>}>
      {error && <Alert>{error}</Alert>}

      {caps?.supports_sql && (
        <div className="flex gap-4 text-sm">
          <label className="flex items-center gap-2"><input type="radio" checked={kind === "object"} onChange={() => setKind("object")} /> Table or view</label>
          <label className="flex items-center gap-2"><input type="radio" checked={kind === "sql"} onChange={() => setKind("sql")} /> Custom {isSf ? "SOQL" : "SQL"} query</label>
        </div>
      )}
      {kind === "object" ? (
        <Field label={{ file: "Sheet", salesforce: "Object", rest: "Endpoint" }[connection.type] ?? "Table or view"}>
          <Select value={object} onChange={(e) => { setObject(e.target.value); setColumns([]); if (!existing) setName(e.target.value.split(".").pop() ?? ""); }}>
            <option value="">{objects.isLoading ? "Loading…" : "Choose…"}</option>
            {objects.data?.map((o) => <option key={o.name} value={o.name}>{o.label && o.label !== o.name ? `${o.label} (${o.name})` : `${o.name} (${o.kind})`}</option>)}
          </Select>
          {objects.error && <p className="mt-1 text-sm text-red-700">{(objects.error as Error).message}</p>}
        </Field>
      ) : (
        <Field label={isSf ? "SOQL query" : "SQL query"} hint={isSf ? "e.g. SELECT Id, Name, Amount, Account.Name FROM Opportunity WHERE IsClosed = false" : "A single SELECT. It runs on the source database with the connection's user."}>
          <textarea className="block h-32 w-full rounded-lg border border-slate-300 p-3 font-mono text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
            value={sql} onChange={(e) => { setSql(e.target.value); setColumns([]); }}
            placeholder="SELECT order_id, region, amount, updated_at FROM sales.orders WHERE status <> 'CANCELLED'" />
          <Button type="button" variant="secondary" size="sm" className="mt-2" onClick={loadColumns} disabled={!sql || checking}>
            {checking ? "Checking…" : "Check query"}
          </Button>
          {columns.length > 0 && <p className="mt-1 text-sm text-emerald-700">Query OK: {columns.length} columns.</p>}
        </Field>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Dataset name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Description (optional)"><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      </div>

      <div className="space-y-3 border-t border-slate-200 pt-4">
        <p className="text-sm font-medium text-slate-700">How to refresh</p>
        <label className="flex items-start gap-2 text-sm">
          <input type="radio" className="mt-1" checked={mode === "full"} onChange={() => setMode("full")} />
          <span>Full: copy everything every time<br /><span className="text-slate-500">Simplest; fine for small and medium tables.</span></span>
        </label>
        <label className={`flex items-start gap-2 text-sm ${canIncremental ? "" : "opacity-50"}`}>
          <input type="radio" className="mt-1" disabled={!canIncremental} checked={mode === "incremental"} onChange={() => setMode("incremental")} />
          <span>Incremental: only new and changed rows<br />
            <span className="text-slate-500">
              {restNoSince ? "Set a 'changed since' parameter on the connection first."
                : !canIncremental ? "Not available for uploaded files."
                : isSf ? "Uses SystemModstamp and also removes records deleted in Salesforce."
                : "Needs a column that changes when a row changes, such as a modified date."}
            </span></span>
        </label>
        {mode === "incremental" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="New/changed rows have a higher…">
              <Select value={incCol} onChange={(e) => setIncCol(e.target.value)}>
                <option value="">{checking ? "Loading columns…" : "Choose a column"}</option>
                {columns.filter((c) => ["datetime", "date", "integer", "number"].includes(c.type)).map((c) =>
                  <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
              </Select>
            </Field>
            <Field label="Unique key (recommended)" hint="Changed rows replace their old version. Without a key, rows are only added.">
              <div className="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
                {columns.map((c) => (
                  <label key={c.name} className="flex items-center gap-1 rounded border border-slate-200 px-2 py-0.5 text-xs">
                    <input type="checkbox" checked={pk.includes(c.name)}
                      onChange={(e) => setPk(e.target.checked ? [...pk, c.name] : pk.filter((k) => k !== c.name))} />
                    {c.name}
                  </label>
                ))}
              </div>
            </Field>
          </div>
        )}
      </div>

      <div className="grid gap-4 border-t border-slate-200 pt-4 sm:grid-cols-2">
        <Field label="Schedule" hint={`Times are ${settings.data?.timezone ?? "server"} time.`}>
          <Select value={preset} onChange={(e) => setPreset(e.target.value as Preset)}>
            <option value="manual">Manual only</option>
            <option value="15m">Every 15 minutes</option>
            <option value="30m">Every 30 minutes</option>
            <option value="hourly">Every hour</option>
            <option value="daily">Daily at…</option>
            <option value="weekdays">Weekdays at…</option>
            <option value="custom">Custom (cron)</option>
          </Select>
        </Field>
        {(preset === "daily" || preset === "weekdays") && (
          <Field label="Time"><Input type="time" value={time} onChange={(e) => setTime(e.target.value)} /></Field>
        )}
        {preset === "custom" && (
          <Field label="Cron expression" hint="minute hour day month weekday, e.g. 0 6,18 * * *">
            <Input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="0 6 * * *" />
          </Field>
        )}
      </div>
    </Modal>
  );
}
