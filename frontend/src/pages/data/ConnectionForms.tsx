import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { useState } from "react";

import { Alert, Button, Field, Input, Modal, Select } from "../../components/ui";
import { api, post, put } from "../../lib/api";
import type { Connection } from "../../lib/types";
import { useDataSettings } from "./common";
import { t } from "../../lib/i18n";

type DbType = "oracle" | "postgres";

const DEFAULTS: Record<DbType, Record<string, string>> = {
  oracle: { host: "", port: "1521", target: "service_name", service: "", user: "", schemas: "" },
  postgres: { host: "", port: "5432", database: "", user: "", schemas: "", sslmode: "prefer" },
};

function fromConnection(c: Connection): Record<string, string> {
  const cfg = c.config as Record<string, unknown>;
  const schemas = Array.isArray(cfg.schemas) ? (cfg.schemas as string[]).join(", ") : "";
  if (c.type === "oracle") {
    return {
      host: String(cfg.host ?? ""), port: String(cfg.port ?? 1521), user: String(cfg.user ?? ""), schemas,
      target: cfg.sid ? "sid" : "service_name", service: String(cfg.sid || cfg.service_name || ""),
    };
  }
  return {
    host: String(cfg.host ?? ""), port: String(cfg.port ?? 5432), database: String(cfg.database ?? ""),
    user: String(cfg.user ?? ""), schemas, sslmode: String(cfg.sslmode ?? "prefer"),
  };
}

function toConfig(type: DbType, f: Record<string, string>): Record<string, unknown> {
  const schemas = f.schemas.split(",").map((s) => s.trim()).filter(Boolean);
  if (type === "oracle") {
    return {
      host: f.host, port: Number(f.port) || 1521, user: f.user, schemas,
      service_name: f.target === "service_name" ? f.service : "", sid: f.target === "sid" ? f.service : "",
    };
  }
  return { host: f.host, port: Number(f.port) || 5432, database: f.database, user: f.user, schemas, sslmode: f.sslmode };
}

export function DatabaseConnectionModal({
  type,
  existing,
  onClose,
  onSaved,
}: {
  type: DbType;
  existing?: Connection;
  onClose: () => void;
  onSaved?: (c: Connection) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(existing?.name ?? (type === "oracle" ? "Oracle" : "PostgreSQL"));
  const [f, setF] = useState<Record<string, string>>(existing ? fromConnection(existing) : DEFAULTS[type]);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const set = (key: string, value: string) => setF((prev) => ({ ...prev, [key]: value }));

  const body = () => ({
    name, type, config: toConfig(type, f),
    secrets: password ? { password } : existing ? null : { password: "" },
  });

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      setTest(await post("/connections/test", { ...body(), connection_id: existing?.id ?? null }));
    } catch (err) {
      setTest({ ok: false, message: err instanceof Error ? err.message : t("Test failed") });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    setError("");
    try {
      const saved = existing
        ? await put<Connection>(`/connections/${existing.id}`, body())
        : await post<Connection>("/connections", body());
      await qc.invalidateQueries({ queryKey: ["connections"] });
      await qc.invalidateQueries({ queryKey: ["connection", saved.id] });
      onSaved?.(saved);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };

  return (
    <Modal open wide title={existing ? `Edit ${existing.name}` : `New ${type === "oracle" ? t("Oracle") : t("PostgreSQL")} connection`}
      onClose={onClose}
      footer={<>
        <Button variant="secondary" className="me-auto" onClick={runTest} disabled={testing}>
          {testing ? t("Testing…") : t("Test connection")}
        </Button>
        <Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
        <Button onClick={save}>{t("Save")}</Button>
      </>}>
      {error && <Alert>{error}</Alert>}
      {test && (
        <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${test.ok ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-red-200 bg-red-50 text-red-800"}`}>
          {test.ok ? <CheckCircle2 size={18} /> : <XCircle size={18} />} {test.message}
        </div>
      )}
      <Field label={t("Name")}><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="sm:col-span-2"><Field label={t("Host")}><Input placeholder={t("db.company.local")} value={f.host} onChange={(e) => set("host", e.target.value)} /></Field></div>
        <Field label={t("Port")}><Input value={f.port} onChange={(e) => set("port", e.target.value)} /></Field>
      </div>
      {type === "oracle" ? (
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label={t("Connect using")}>
            <Select value={f.target} onChange={(e) => set("target", e.target.value)}>
              <option value="service_name">{t("Service name")}</option>
              <option value="sid">{t("SID")}</option>
            </Select>
          </Field>
          <div className="sm:col-span-2">
            <Field label={f.target === "sid" ? "SID" : t("Service name")}>
              <Input placeholder={f.target === "sid" ? "ORCL" : "ORCLPDB1"} value={f.service} onChange={(e) => set("service", e.target.value)} />
            </Field>
          </div>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("Database")}><Input value={f.database} onChange={(e) => set("database", e.target.value)} /></Field>
          <Field label={t("SSL")}>
            <Select value={f.sslmode} onChange={(e) => set("sslmode", e.target.value)}>
              {["prefer", "require", "verify-full", "disable"].map((m) => <option key={m} value={m}>{m}</option>)}
            </Select>
          </Field>
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("Username")} hint={t("Use a read-only database user.")}><Input value={f.user} onChange={(e) => set("user", e.target.value)} /></Field>
        <Field label={t("Password")} hint={existing?.secrets_set.includes("password") ? t("Stored. Leave empty to keep it.") : t("Stored encrypted.")}>
          <Input type="password" autoComplete="new-password" value={password}
            placeholder={existing?.secrets_set.includes("password") ? "••••••••" : ""} onChange={(e) => setPassword(e.target.value)} />
        </Field>
      </div>
      <Field label={t("Schemas to show (optional)")} hint={t("Comma-separated. Empty = the user's own tables and views.")}>
        <Input placeholder={type === "oracle" ? t("SALES, FINANCE") : t("public, sales")} value={f.schemas} onChange={(e) => set("schemas", e.target.value)} />
      </Field>
    </Modal>
  );
}

export function UploadModal({
  replace,
  onClose,
  onDone,
}: {
  replace?: Connection;
  onClose: () => void;
  onDone?: (c: Connection) => void;
}) {
  const qc = useQueryClient();
  const settings = useDataSettings();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setError("");
    const form = new FormData();
    form.append("file", file);
    if (!replace) form.append("name", name);
    try {
      const conn = replace
        ? await api<Connection>(`/connections/${replace.id}/file`, { method: "PUT", form })
        : await api<Connection>("/connections/upload", { method: "POST", form });
      await qc.invalidateQueries({ queryKey: ["connections"] });
      await qc.invalidateQueries({ queryKey: ["connection", conn.id] });
      await qc.invalidateQueries({ queryKey: ["datasets"] });
      onDone?.(conn);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Upload failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open title={replace ? `Replace file in ${replace.name}` : t("Upload a file")} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
        <Button disabled={!file || busy} onClick={submit}>{busy ? t("Uploading…") : t("Upload")}</Button></>}>
      {error && <Alert>{error}</Alert>}
      <p className="text-sm text-slate-600">
        {t("CSV, Excel (.xlsx, every sheet becomes a table) or Parquet, up to")} {settings.data?.max_upload_mb ?? "…"} {t("MB.")}
        {replace && " Datasets built on this file are refreshed automatically."}
      </p>
      <Input type="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm,.parquet" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      {!replace && (
        <Field label={t("Name (optional)")}><Input placeholder={file?.name ?? t("Branch targets 2026")} value={name} onChange={(e) => setName(e.target.value)} /></Field>
      )}
    </Modal>
  );
}
