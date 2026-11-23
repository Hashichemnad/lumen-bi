import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { useState, type ReactNode } from "react";

import KeyValueEditor, { toMap, toRows, type Row } from "../../components/KeyValueEditor";
import { Alert, Button, Field, Input, Modal, Select } from "../../components/ui";
import { post, put } from "../../lib/api";
import type { Connection } from "../../lib/types";
import { t } from "../../lib/i18n";

type Values = Record<string, string>;

/** Shared modal: name, test button, save; the caller renders the type-specific fields. */
function ConnectionShell({
  title, type, existing, name, setName, buildConfig, buildSecrets, onClose, onSaved, children,
}: {
  title: string;
  type: string;
  existing?: Connection;
  name: string;
  setName: (v: string) => void;
  buildConfig: () => Record<string, unknown>;
  buildSecrets: () => Record<string, string> | null;
  onClose: () => void;
  onSaved?: (c: Connection) => void;
  children: ReactNode;
}) {
  const qc = useQueryClient();
  const [error, setError] = useState("");
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const body = () => ({ name, type, config: buildConfig(), secrets: buildSecrets() });

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
      await qc.invalidateQueries({ queryKey: ["objects", saved.id] });
      onSaved?.(saved);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };

  return (
    <Modal open wide title={title} onClose={onClose}
      footer={<>
        <Button variant="secondary" className="me-auto" onClick={runTest} disabled={testing}>{testing ? t("Testing…") : t("Test connection")}</Button>
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
      {children}
    </Modal>
  );
}

function secretHint(existing: Connection | undefined, key: string): string {
  return existing?.secrets_set.includes(key) ? "Stored. Leave empty to keep it." : "Stored encrypted.";
}

/** Only send secrets the user typed; for a new connection send what we have. */
function pickSecrets(existing: Connection | undefined, values: Values, keys: string[]): Record<string, string> | null {
  const typed = Object.fromEntries(keys.filter((k) => values[k]).map((k) => [k, values[k]]));
  if (existing) return Object.keys(typed).length ? typed : null;
  return typed;
}

// ===================================================================== Salesforce
export function SalesforceConnectionModal({ existing, onClose, onSaved }: { existing?: Connection; onClose: () => void; onSaved?: (c: Connection) => void }) {
  const cfg = (existing?.config ?? {}) as Record<string, unknown>;
  const [name, setName] = useState(existing?.name ?? "Salesforce");
  const [f, setF] = useState<Values>({
    auth: String(cfg.auth ?? "client_credentials"), domain_url: String(cfg.domain_url ?? ""),
    client_id: String(cfg.client_id ?? ""), username: String(cfg.username ?? ""), sandbox: cfg.sandbox ? "yes" : "",
    api_version: String(cfg.api_version ?? ""), bulk_threshold: String(cfg.bulk_threshold ?? 50000),
    client_secret: "", private_key: "",
  });
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }));

  return (
    <ConnectionShell title={existing ? `Edit ${existing.name}` : t("New Salesforce connection")} type="salesforce"
      existing={existing} name={name} setName={setName} onClose={onClose} onSaved={onSaved}
      buildConfig={() => ({
        auth: f.auth, domain_url: f.domain_url, client_id: f.client_id, username: f.username,
        sandbox: f.sandbox === "yes", api_version: f.api_version, bulk_threshold: Number(f.bulk_threshold) || 50000,
      })}
      buildSecrets={() => pickSecrets(existing, f, f.auth === "jwt" ? ["private_key"] : ["client_secret"])}>
      <Alert tone="blue">
        {t("In Salesforce Setup, create a connected app (or external client app) with the")} <b>{t("api")}</b> {t("scope. For client credentials, tick")} <b>{t("Enable Client Credentials Flow")}</b> {t("and choose a")} <b>{t("Run As")}</b> {t("integration user with read access to the objects you need. See docs/CONNECTORS.md for step-by-step instructions.")}
      </Alert>
      <Field label={t("My Domain URL")} hint={t("Setup → My Domain. Sandboxes look like yourcompany--uat.sandbox.my.salesforce.com.")}>
        <Input placeholder={t("https://yourcompany.my.salesforce.com")} value={f.domain_url} onChange={(e) => set("domain_url", e.target.value)} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("Sign-in method")}>
          <Select value={f.auth} onChange={(e) => set("auth", e.target.value)}>
            <option value="client_credentials">{t("Client credentials (recommended)")}</option>
            <option value="jwt">{t("JWT bearer (certificate)")}</option>
          </Select>
        </Field>
        <Field label={t("Consumer key (client ID)")}><Input value={f.client_id} onChange={(e) => set("client_id", e.target.value)} /></Field>
      </div>
      {f.auth === "client_credentials" ? (
        <Field label={t("Consumer secret")} hint={secretHint(existing, "client_secret")}>
          <Input type="password" autoComplete="new-password" value={f.client_secret}
            placeholder={existing?.secrets_set.includes("client_secret") ? "••••••••" : ""} onChange={(e) => set("client_secret", e.target.value)} />
        </Field>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("Integration username")}><Input placeholder={t("integration@company.com")} value={f.username} onChange={(e) => set("username", e.target.value)} /></Field>
            <Field label={t("Org type")}>
              <Select value={f.sandbox} onChange={(e) => set("sandbox", e.target.value)}>
                <option value="">{t("Production / Developer")}</option>
                <option value="yes">{t("Sandbox")}</option>
              </Select>
            </Field>
          </div>
          <Field label={t("Private key (PEM)")} hint={secretHint(existing, "private_key")}>
            <textarea className="block h-28 w-full rounded-lg border border-slate-300 p-2 font-mono text-xs"
              placeholder={existing?.secrets_set.includes("private_key") ? t("Stored. Paste a new key to replace it.") : t("-----BEGIN PRIVATE KEY-----")}
              value={f.private_key} onChange={(e) => set("private_key", e.target.value)} />
          </Field>
        </>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("API version (optional)")} hint={t("Empty = the newest your org supports.")}><Input placeholder="66.0" value={f.api_version} onChange={(e) => set("api_version", e.target.value)} /></Field>
        <Field label={t("Use Bulk API from (rows)")} hint={t("Large objects are read with Bulk API 2.0.")}><Input value={f.bulk_threshold} onChange={(e) => set("bulk_threshold", e.target.value)} /></Field>
      </div>
    </ConnectionShell>
  );
}

// ===================================================================== REST / JSON
export function RestConnectionModal({ existing, onClose, onSaved }: { existing?: Connection; onClose: () => void; onSaved?: (c: Connection) => void }) {
  const cfg = (existing?.config ?? {}) as Record<string, unknown>;
  const str = (k: string, d = "") => (cfg[k] === undefined || cfg[k] === null ? d : String(cfg[k]));
  const [name, setName] = useState(existing?.name ?? "REST API");
  const [f, setF] = useState<Values>({
    base_url: str("base_url"), endpoints: Array.isArray(cfg.endpoints) ? (cfg.endpoints as string[]).join("\n") : "",
    auth: str("auth", "none"), api_key_name: str("api_key_name", "X-API-Key"), api_key_in: str("api_key_in", "header"),
    username: str("username"), token_url: str("token_url"), client_id: str("client_id"), scope: str("scope"),
    records_path: str("records_path"), pagination: str("pagination", "none"), page_param: str("page_param", "page"),
    size_param: str("size_param"), page_size: str("page_size", "100"), first_page: str("first_page", "1"),
    offset_param: str("offset_param", "offset"), cursor_param: str("cursor_param", "cursor"),
    cursor_path: str("cursor_path"), next_path: str("next_path"), max_pages: str("max_pages", "1000"),
    since_param: str("since_param"), since_format: str("since_format", "iso"),
    api_key: "", token: "", password: "", client_secret: "",
  });
  const [headers, setHeaders] = useState<Row[]>(toRows((cfg.headers as Record<string, string>) ?? {}));
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }));
  const secretFor: Record<string, string> = { api_key: "api_key", bearer: "token", basic: "password", oauth2: "client_secret" };

  return (
    <ConnectionShell title={existing ? `Edit ${existing.name}` : t("New REST API connection")} type="rest"
      existing={existing} name={name} setName={setName} onClose={onClose} onSaved={onSaved}
      buildConfig={() => ({ ...Object.fromEntries(Object.entries(f).filter(([k]) => !["api_key", "token", "password", "client_secret"].includes(k))),
        endpoints: f.endpoints.split("\n").map((e) => e.trim()).filter(Boolean), headers: toMap(headers) })}
      buildSecrets={() => pickSecrets(existing, f, secretFor[f.auth] ? [secretFor[f.auth]] : [])}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("Base URL")}><Input placeholder={t("https://api.example.com")} value={f.base_url} onChange={(e) => set("base_url", e.target.value)} /></Field>
        <Field label={t("Records are at (path in the response)")} hint={t("e.g. data.items · empty if the response is the list itself")}>
          <Input placeholder={t("data")} value={f.records_path} onChange={(e) => set("records_path", e.target.value)} />
        </Field>
      </div>
      <Field label={t("Endpoints (one per line)")} hint={t("Each becomes a choice when creating a dataset. Query strings are allowed: /v1/orders?status=open")}>
        <textarea className="block h-20 w-full rounded-lg border border-slate-300 p-2 font-mono text-sm" placeholder={"/v1/orders\n/v1/customers"}
          value={f.endpoints} onChange={(e) => set("endpoints", e.target.value)} />
      </Field>

      <div className="space-y-3 border-t border-slate-200 pt-4">
        <Field label={t("Sign-in")}>
          <Select value={f.auth} onChange={(e) => set("auth", e.target.value)}>
            <option value="none">{t("None")}</option>
            <option value="api_key">{t("API key")}</option>
            <option value="bearer">{t("Bearer token")}</option>
            <option value="basic">{t("Username and password (Basic)")}</option>
            <option value="oauth2">{t("OAuth 2.0 client credentials")}</option>
          </Select>
        </Field>
        {f.auth === "api_key" && (
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label={t("Key name")}><Input value={f.api_key_name} onChange={(e) => set("api_key_name", e.target.value)} /></Field>
            <Field label={t("Sent in")}>
              <Select value={f.api_key_in} onChange={(e) => set("api_key_in", e.target.value)}>
                <option value="header">{t("Header")}</option><option value="query">{t("Query parameter")}</option>
              </Select>
            </Field>
            <Field label={t("API key")} hint={secretHint(existing, "api_key")}><Input type="password" value={f.api_key} onChange={(e) => set("api_key", e.target.value)} /></Field>
          </div>
        )}
        {f.auth === "bearer" && (
          <Field label={t("Token")} hint={secretHint(existing, "token")}><Input type="password" value={f.token} onChange={(e) => set("token", e.target.value)} /></Field>
        )}
        {f.auth === "basic" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("Username")}><Input value={f.username} onChange={(e) => set("username", e.target.value)} /></Field>
            <Field label={t("Password")} hint={secretHint(existing, "password")}><Input type="password" value={f.password} onChange={(e) => set("password", e.target.value)} /></Field>
          </div>
        )}
        {f.auth === "oauth2" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("Token URL")}><Input placeholder={t("https://auth.example.com/oauth/token")} value={f.token_url} onChange={(e) => set("token_url", e.target.value)} /></Field>
            <Field label={t("Scope (optional)")}><Input value={f.scope} onChange={(e) => set("scope", e.target.value)} /></Field>
            <Field label={t("Client ID")}><Input value={f.client_id} onChange={(e) => set("client_id", e.target.value)} /></Field>
            <Field label={t("Client secret")} hint={secretHint(existing, "client_secret")}><Input type="password" value={f.client_secret} onChange={(e) => set("client_secret", e.target.value)} /></Field>
          </div>
        )}
      </div>

      <div className="space-y-3 border-t border-slate-200 pt-4">
        <Field label={t("Pages")}>
          <Select value={f.pagination} onChange={(e) => set("pagination", e.target.value)}>
            <option value="none">{t("Everything in one response")}</option>
            <option value="page">{t("Page number (?page=2)")}</option>
            <option value="offset">{t("Offset and limit (?offset=100&limit=100)")}</option>
            <option value="cursor">{t("Cursor from the response")}</option>
            <option value="next_link">{t("Next-page URL in the response")}</option>
            <option value="link_header">{t("Link header (rel=\"next\")")}</option>
          </Select>
        </Field>
        {f.pagination === "page" && (
          <div className="grid gap-4 sm:grid-cols-4">
            <Field label={t("Page parameter")}><Input value={f.page_param} onChange={(e) => set("page_param", e.target.value)} /></Field>
            <Field label={t("Size parameter")}><Input placeholder={t("per_page")} value={f.size_param} onChange={(e) => set("size_param", e.target.value)} /></Field>
            <Field label={t("Page size")}><Input value={f.page_size} onChange={(e) => set("page_size", e.target.value)} /></Field>
            <Field label={t("First page")}><Input value={f.first_page} onChange={(e) => set("first_page", e.target.value)} /></Field>
          </div>
        )}
        {f.pagination === "offset" && (
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label={t("Offset parameter")}><Input value={f.offset_param} onChange={(e) => set("offset_param", e.target.value)} /></Field>
            <Field label={t("Limit parameter")}><Input placeholder={t("limit")} value={f.size_param} onChange={(e) => set("size_param", e.target.value)} /></Field>
            <Field label={t("Page size")}><Input value={f.page_size} onChange={(e) => set("page_size", e.target.value)} /></Field>
          </div>
        )}
        {f.pagination === "cursor" && (
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label={t("Next cursor is at")}><Input placeholder={t("meta.next_cursor")} value={f.cursor_path} onChange={(e) => set("cursor_path", e.target.value)} /></Field>
            <Field label={t("Cursor parameter")}><Input value={f.cursor_param} onChange={(e) => set("cursor_param", e.target.value)} /></Field>
            <Field label={t("Size parameter (optional)")}><Input placeholder={t("limit")} value={f.size_param} onChange={(e) => set("size_param", e.target.value)} /></Field>
          </div>
        )}
        {f.pagination === "next_link" && (
          <Field label={t("Next-page URL is at")}><Input placeholder={t("links.next")} value={f.next_path} onChange={(e) => set("next_path", e.target.value)} /></Field>
        )}
        {f.pagination !== "none" && (
          <Field label={t("Stop after (pages)")} hint={t("Safety limit against endless paging.")}><Input value={f.max_pages} onChange={(e) => set("max_pages", e.target.value)} /></Field>
        )}
      </div>

      <div className="grid gap-4 border-t border-slate-200 pt-4 sm:grid-cols-2">
        <Field label={t("'Changed since' parameter (optional)")} hint={t("Enables incremental sync, e.g. updated_since.")}>
          <Input value={f.since_param} onChange={(e) => set("since_param", e.target.value)} />
        </Field>
        <Field label={t("Its format")}>
          <Select value={f.since_format} onChange={(e) => set("since_format", e.target.value)}>
            <option value="iso">2026-01-31T08:00:00Z</option>
            <option value="date">2026-01-31</option>
            <option value="epoch">{t("Unix seconds")}</option>
            <option value="epoch_ms">{t("Unix milliseconds")}</option>
          </Select>
        </Field>
      </div>
      <Field label={t("Extra headers (optional)")} hint={t("Not for credentials: use the sign-in settings above, which are encrypted.")}>
        <KeyValueEditor rows={headers} onChange={setHeaders} keyPlaceholder="Header" valuePlaceholder="Value" addLabel="Add header" />
      </Field>
    </ConnectionShell>
  );
}
