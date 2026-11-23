/** Admin settings: notification channels (Teams, WhatsApp) and embed keys. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plus, Send, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Alert, Badge, Button, Card, Field, Input, Select } from "../../components/ui";
import { del, get, post, put } from "../../lib/api";
import type { DashboardSummary } from "../../lib/dashboard/types";
import { formatMoment } from "../../lib/dates";
import { t } from "../../lib/i18n";

interface NotifySettings {
  teams: { id?: string; name: string; has_url?: boolean; url?: string }[];
  whatsapp: { enabled: boolean; phone_number_id: string; template: string; language: string; phone_attribute: string; has_token?: boolean; token?: string };
}

export function NotificationsCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["notify-settings"], queryFn: () => get<NotifySettings>("/admin/settings/notifications") });
  const [s, setS] = useState<NotifySettings | null>(null);
  const [msg, setMsg] = useState<{ tone: "red" | "green" | "blue"; text: string } | null>(null);
  useEffect(() => {
    if (q.data) setS(q.data);
  }, [q.data]);
  if (!s) return null;
  const save = async () => {
    setMsg(null);
    try {
      const out = await put<NotifySettings>("/admin/settings/notifications", s);
      qc.setQueryData(["notify-settings"], out);
      setMsg({ tone: "green", text: t("Settings saved.") });
    } catch (err) {
      setMsg({ tone: "red", text: err instanceof Error ? err.message : t("Could not save") });
    }
  };
  const test = async (channel: string) => {
    const r = await post<{ sent: number; error: string }>("/admin/settings/notifications/test", { channel });
    setMsg(r.sent ? { tone: "green", text: t("Test message sent.") } : { tone: "red", text: t("The test failed: {error}", { error: r.error || "?" }) });
  };
  const wa = s.whatsapp;
  return (
    <Card className="p-5">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">{t("Notifications")}</h2>
        <Button onClick={save}>{t("Save")}</Button>
      </div>
      <p className="mb-4 text-sm text-slate-500">{t("Where subscriptions and alerts can be sent besides email. Addresses and tokens are stored encrypted and never shown again.")}</p>
      {msg && <div className="mb-3"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
      <h3 className="mb-2 text-sm font-semibold">Microsoft Teams</h3>
      <p className="mb-2 text-xs text-slate-500">{t("In the Teams channel: … → Workflows → Post to a channel when a webhook request is received. Paste the address it gives you.")}</p>
      <ul className="space-y-2">
        {s.teams.map((tm, i) => (
          <li key={tm.id ?? `new${i}`} className="grid gap-2 sm:grid-cols-[12rem_1fr_auto] sm:items-end">
            <Field label={t("Channel name")}><Input value={tm.name} onChange={(e) => setS({ ...s, teams: s.teams.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} /></Field>
            <Field label={t("Webhook address")} hint={tm.has_url ? t("Stored. Leave empty to keep it.") : undefined}>
              <Input value={tm.url ?? ""} placeholder="https://…" dir="ltr" onChange={(e) => setS({ ...s, teams: s.teams.map((x, j) => (j === i ? { ...x, url: e.target.value } : x)) })} />
            </Field>
            <div className="flex gap-1 pb-0.5">
              {tm.id && tm.has_url && <Button size="sm" variant="secondary" onClick={() => test(`teams:${tm.id}`)}><Send size={14} /> {t("Test")}</Button>}
              <Button size="sm" variant="ghost" aria-label={t("Remove")} onClick={() => setS({ ...s, teams: s.teams.filter((_, j) => j !== i) })}><Trash2 size={14} /></Button>
            </div>
          </li>
        ))}
      </ul>
      <Button className="mt-2" variant="secondary" size="sm" onClick={() => setS({ ...s, teams: [...s.teams, { name: "", url: "" }] })}><Plus size={14} /> {t("Add a Teams channel")}</Button>

      <h3 className="mb-2 mt-6 text-sm font-semibold">WhatsApp</h3>
      <p className="mb-2 text-xs text-slate-500">{t("Uses the WhatsApp Business Cloud API (Meta). Create an approved message template with three values: title, message and link. Each person's number comes from a user attribute.")}</p>
      <label className="mb-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={wa.enabled} onChange={(e) => setS({ ...s, whatsapp: { ...wa, enabled: e.target.checked } })} /> {t("Send WhatsApp messages")}</label>
      {wa.enabled && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("Phone number ID")}><Input value={wa.phone_number_id} dir="ltr" onChange={(e) => setS({ ...s, whatsapp: { ...wa, phone_number_id: e.target.value } })} /></Field>
          <Field label={t("Access token")} hint={wa.has_token ? t("Stored. Leave empty to keep it.") : undefined}>
            <Input type="password" value={wa.token ?? ""} onChange={(e) => setS({ ...s, whatsapp: { ...wa, token: e.target.value } })} autoComplete="off" />
          </Field>
          <Field label={t("Template name")}><Input value={wa.template} dir="ltr" onChange={(e) => setS({ ...s, whatsapp: { ...wa, template: e.target.value } })} /></Field>
          <Field label={t("Template language")}>
            <Select value={wa.language} onChange={(e) => setS({ ...s, whatsapp: { ...wa, language: e.target.value } })}>
              <option value="en">English</option>
              <option value="ar">العربية</option>
            </Select>
          </Field>
          <Field label={t("User attribute with the phone number")} hint={t("International format, e.g. +971501234567")}>
            <Input value={wa.phone_attribute} dir="ltr" onChange={(e) => setS({ ...s, whatsapp: { ...wa, phone_attribute: e.target.value } })} />
          </Field>
          {q.data?.whatsapp.enabled && <div className="flex items-end"><Button variant="secondary" onClick={() => test("whatsapp")}><Send size={14} /> {t("Send me a test")}</Button></div>}
        </div>
      )}
    </Card>
  );
}

interface EmbedKey {
  id: number;
  name: string;
  dashboards: number[];
  enabled: boolean;
  created_at: string;
  last_used_at: string | null;
  secret?: string;
}

export function EmbedKeysCard() {
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: ["embed-keys"], queryFn: () => get<EmbedKey[]>("/admin/embed-keys") });
  const dashboards = useQuery({ queryKey: ["dashboards"], queryFn: () => get<DashboardSummary[]>("/dashboards") });
  const [name, setName] = useState("");
  const [secret, setSecret] = useState<{ id: number; secret: string } | null>(null);
  const [error, setError] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["embed-keys"] });
  const create = async () => {
    setError("");
    try {
      const k = await post<EmbedKey>("/admin/embed-keys", { name, dashboards: [] });
      setSecret({ id: k.id, secret: k.secret ?? "" });
      setName("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };
  const change = async (k: EmbedKey, patch: Partial<EmbedKey>) => {
    await put(`/admin/embed-keys/${k.id}`, { name: k.name, dashboards: k.dashboards, enabled: k.enabled, ...patch });
    await refresh();
  };
  const dashName = (id: number) => dashboards.data?.find((d) => d.id === id)?.name ?? `#${id}`;
  return (
    <Card className="p-5">
      <h2 className="text-base font-semibold">{t("Embedding")}</h2>
      <p className="mb-4 text-sm text-slate-500">{t("Show dashboards inside another application (for example a portal). The application signs a short-lived token with a key; people see the data of the Lumen user named in the token. The sites that may show Lumen in a frame are set in EMBED_FRAME_ANCESTORS.")}</p>
      {error && <div className="mb-3"><Alert>{error}</Alert></div>}
      {secret && (
        <div className="mb-3">
          <Alert tone="green">
            <span className="block font-semibold">{t("Copy the secret now: it is shown only once.")}</span>
            <span className="mt-1 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate text-xs" dir="ltr">{secret.secret}</code>
              <Button size="sm" variant="secondary" onClick={() => navigator.clipboard?.writeText(secret.secret)}><Copy size={14} /> {t("Copy")}</Button>
            </span>
            <span className="mt-2 block text-xs">{t("Key ID (the token's kid): {id}. See docs/EMBEDDING.md for an example.", { id: secret.id })}</span>
          </Alert>
        </div>
      )}
      <ul className="space-y-2">
        {keys.data?.map((k) => (
          <li key={k.id} className="rounded-lg border border-slate-200 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <KeyRound size={16} className="text-slate-400" />
              <span className="min-w-0 flex-1">
                <span className="block font-medium">{k.name} <span className="text-xs font-normal text-slate-500">· kid {k.id}</span></span>
                <span className="block text-xs text-slate-500">{k.last_used_at ? t("last used {v0}", { v0: formatMoment(k.last_used_at) }) : t("Not used yet")}</span>
              </span>
              {!k.enabled && <Badge>{t("Off")}</Badge>}
              <Button size="sm" variant="secondary" onClick={() => change(k, { enabled: !k.enabled })}>{k.enabled ? t("Turn off") : t("Turn on")}</Button>
              <Button size="sm" variant="ghost" aria-label={t("Delete")} onClick={async () => { if (confirm(t("Delete this key? Embedded dashboards using it stop at once."))) { await del(`/admin/embed-keys/${k.id}`); await refresh(); } }}><Trash2 size={14} /></Button>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-slate-500">{t("May open:")}</span>
              {k.dashboards.map((id) => (
                <button key={id} onClick={() => change(k, { dashboards: k.dashboards.filter((x) => x !== id) })} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs hover:bg-red-50 hover:text-red-700" title={t("Remove")}>{dashName(id)} ×</button>
              ))}
              <Select value="" className="w-56 py-1" aria-label={t("Add a dashboard")} onChange={(e) => e.target.value && change(k, { dashboards: [...k.dashboards, Number(e.target.value)] })}>
                <option value="">{t("Add a dashboard…")}</option>
                {dashboards.data?.filter((d) => !k.dashboards.includes(d.id)).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </Select>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <Field label={t("New key name")}><Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("Customer portal")} className="w-64" /></Field>
        <Button onClick={create} disabled={!name.trim()}><Plus size={15} /> {t("Create key")}</Button>
      </div>
    </Card>
  );
}
