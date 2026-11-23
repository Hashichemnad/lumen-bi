/** Admin → Settings: sign-in security, branding, email (SMTP) and backups. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, HardDriveDownload, ImagePlus, Mail, Palette, Send, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert, Button, Card, Field, Input, Select } from "../../components/ui";
import { del, get, post, put, saveFile } from "../../lib/api";
import type { Branding } from "../../lib/branding";
import { formatMoment } from "../../lib/dates";
import { t } from "../../lib/i18n";

type Msg = { tone: "red" | "green" | "blue"; text: string } | null;
const errText = (e: unknown) => (e instanceof Error ? e.message : t("Could not save"));

export function SecurityCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["security-settings"], queryFn: () => get<{ require_two_factor: string; users_with_two_factor: number; password_users: number }>("/admin/settings/security") });
  const [msg, setMsg] = useState<Msg>(null);
  if (!q.data) return null;
  const save = async (value: string) => {
    setMsg(null);
    try {
      qc.setQueryData(["security-settings"], await put("/admin/settings/security", { require_two_factor: value }));
      setMsg({ tone: "green", text: t("Settings saved.") });
    } catch (e) {
      setMsg({ tone: "red", text: errText(e) });
    }
  };
  return (
    <Card className="p-5">
      <h2 className="flex items-center gap-2 font-semibold"><ShieldCheck size={17} /> {t("Two-factor sign-in")}</h2>
      <p className="mt-1 text-sm text-slate-500">
        {t("A code from an authenticator app, on top of the password. Single sign-on (Microsoft, UAE PASS) uses the provider's own checks.")}
      </p>
      {msg && <div className="mt-3"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
      <div className="mt-4 grid gap-4 sm:grid-cols-[260px_1fr] sm:items-end">
        <Field label={t("Required for")}>
          <Select value={q.data.require_two_factor} onChange={(e) => save(e.target.value)}>
            <option value="off">{t("Nobody (people can turn it on)")}</option>
            <option value="admins">{t("Admins")}</option>
            <option value="everyone">{t("Everyone who signs in with a password")}</option>
          </Select>
        </Field>
        <p className="pb-2 text-sm text-slate-600">
          {t("{n} of {total} people with a password have it on. Others set it up at their next sign-in when it is required.", { n: q.data.users_with_two_factor, total: q.data.password_users })}
        </p>
      </div>
    </Card>
  );
}

export function BrandingCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["branding"], queryFn: () => get<Branding>("/branding") });
  const [b, setB] = useState<Branding | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (q.data) setB(q.data);
  }, [q.data]);
  if (!b) return null;
  const save = async () => {
    setMsg(null);
    try {
      qc.setQueryData(["branding"], await put<Branding>("/admin/settings/branding", b));
      setMsg({ tone: "green", text: t("Settings saved.") });
    } catch (e) {
      setMsg({ tone: "red", text: errText(e) });
    }
  };
  const pick = (f: File | undefined) => {
    if (!f) return;
    if (f.size > 200_000) return setMsg({ tone: "red", text: t("The logo must be smaller than 200 KB") });
    const r = new FileReader();
    r.onload = () => setB({ ...b, logo: String(r.result) });
    r.readAsDataURL(f);
  };
  return (
    <Card className="p-5">
      <h2 className="flex items-center gap-2 font-semibold"><Palette size={17} /> {t("Branding")}</h2>
      <p className="mt-1 text-sm text-slate-500">{t("Your organisation's name, logo and colour, on every page and the sign-in page.")}</p>
      {msg && <div className="mt-3"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Field label={t("Name")}><Input value={b.name} maxLength={60} onChange={(e) => setB({ ...b, name: e.target.value })} /></Field>
        <Field label={t("Colour")} hint={t("Buttons and highlights. Charts keep their own colours.")}>
          <div className="flex items-center gap-2">
            <input type="color" aria-label={t("Colour")} value={b.accent ?? "#2370cc"} onChange={(e) => setB({ ...b, accent: e.target.value })} className="h-10 w-14 cursor-pointer rounded border border-slate-300 bg-white p-1" />
            <Input value={b.accent ?? ""} placeholder={t("Theme colour")} dir="ltr" onChange={(e) => setB({ ...b, accent: e.target.value || null })} className="w-32" />
            {b.accent && <Button type="button" variant="ghost" size="sm" onClick={() => setB({ ...b, accent: null })}>{t("Reset")}</Button>}
          </div>
        </Field>
        <Field label={t("Logo")} hint={t("PNG, JPEG, WebP or SVG, square, under 200 KB")}>
          <div className="flex items-center gap-3">
            <span className="flex h-12 w-12 items-center justify-center overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
              {b.logo ? <img src={b.logo} alt="" className="h-full w-full object-contain" /> : <ImagePlus size={18} className="text-slate-400" />}
            </span>
            <input ref={file} type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
            <Button type="button" variant="secondary" size="sm" onClick={() => file.current?.click()}>{t("Choose a file")}</Button>
            {b.logo && <Button type="button" variant="ghost" size="sm" onClick={() => setB({ ...b, logo: null })}>{t("Remove")}</Button>}
          </div>
        </Field>
        <Field label={t("Message on the sign-in page")}>
          <Input value={b.login_message} maxLength={300} onChange={(e) => setB({ ...b, login_message: e.target.value })} placeholder={t("Use your work email and password.")} />
        </Field>
      </div>
      <div className="mt-4 flex justify-end"><Button onClick={save}>{t("Save")}</Button></div>
    </Card>
  );
}

interface EmailView { host: string; port: number; user: string; has_password: boolean; sender: string; tls: boolean; in_use: string; environment_host: string }

export function EmailCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["email-settings"], queryFn: () => get<EmailView>("/admin/settings/email") });
  const [s, setS] = useState<(EmailView & { password: string | null }) | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  useEffect(() => {
    if (q.data) setS({ ...q.data, password: null });
  }, [q.data]);
  if (!s) return null;
  const save = async () => {
    setMsg(null);
    try {
      const out = await put<EmailView>("/admin/settings/email", { host: s.host, port: s.port, user: s.user, password: s.password, sender: s.sender, tls: s.tls });
      qc.setQueryData(["email-settings"], out);
      setMsg({ tone: "green", text: t("Settings saved.") });
    } catch (e) {
      setMsg({ tone: "red", text: errText(e) });
    }
  };
  const test = async () => {
    const r = await post<{ ok: boolean; to?: string; error?: string }>("/admin/settings/email/test");
    setMsg(r.ok ? { tone: "green", text: t("Test email sent to {to}.", { to: r.to ?? "" }) } : { tone: "red", text: t("The test failed: {error}", { error: r.error ?? "?" }) });
  };
  const using = { settings: t("Using the settings below."), environment: t("Using the server's SMTP settings ({host}).", { host: s.environment_host }), none: t("Email is not set up: messages are only written to the server log.") }[s.in_use as "settings"];
  return (
    <Card className="p-5">
      <h2 className="flex items-center gap-2 font-semibold"><Mail size={17} /> {t("Email")}</h2>
      <p className="mt-1 text-sm text-slate-500">{t("The mail server for password resets, reports, alerts and the daily summary.")} {using}</p>
      {msg && <div className="mt-3"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
      <div className="mt-4 grid gap-4 sm:grid-cols-[1fr_120px]">
        <Field label={t("SMTP server")}><Input value={s.host} onChange={(e) => setS({ ...s, host: e.target.value })} placeholder="smtp.office365.com" dir="ltr" /></Field>
        <Field label={t("Port")}><Input type="number" value={s.port} onChange={(e) => setS({ ...s, port: Number(e.target.value) || 587 })} dir="ltr" /></Field>
        <Field label={t("User name")}><Input value={s.user} onChange={(e) => setS({ ...s, user: e.target.value })} autoComplete="off" dir="ltr" /></Field>
        <Field label={t("Password")} hint={s.has_password ? t("Saved. Leave empty to keep it.") : undefined}>
          <Input type="password" value={s.password ?? ""} onChange={(e) => setS({ ...s, password: e.target.value })} autoComplete="new-password" dir="ltr" />
        </Field>
        <Field label={t("From")}><Input value={s.sender} onChange={(e) => setS({ ...s, sender: e.target.value })} placeholder="Lumen BI <no-reply@company.ae>" dir="ltr" /></Field>
        <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" checked={s.tls} onChange={(e) => setS({ ...s, tls: e.target.checked })} /> STARTTLS</label>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" onClick={test}><Send size={15} /> {t("Send a test to me")}</Button>
        <Button onClick={save}>{t("Save")}</Button>
      </div>
    </Card>
  );
}

interface BackupsView { enabled: boolean; keep: number; hour: number; folder: string; backups: { name: string; size: number; created_at: string }[] }

export function BackupsCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["backups"], queryFn: () => get<BackupsView>("/admin/backups"), refetchInterval: 15_000 });
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  if (!q.data) return null;
  const v = q.data;
  const setCfg = async (p: Partial<BackupsView>) => {
    setMsg(null);
    try {
      qc.setQueryData(["backups"], await put<BackupsView>("/admin/backups/settings", { enabled: v.enabled, keep: v.keep, hour: v.hour, ...p }));
    } catch (e) {
      setMsg({ tone: "red", text: errText(e) });
    }
  };
  const now = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await post("/admin/backups");
      setMsg({ tone: "blue", text: t("Backing up. It appears in the list when it is ready.") });
      setTimeout(() => qc.invalidateQueries({ queryKey: ["backups"] }), 1500);
    } catch (e) {
      setMsg({ tone: "red", text: errText(e) });
    } finally {
      setBusy(false);
    }
  };
  const download = async (name: string) => {
    const res = await fetch(`/api/v1/admin/backups/${encodeURIComponent(name)}`, { credentials: "same-origin" });
    if (res.ok) saveFile(await res.blob(), name);
  };
  const remove = async (name: string) => {
    if (!confirm(t("Delete {name}?", { name }))) return;
    await del(`/admin/backups/${encodeURIComponent(name)}`);
    await qc.invalidateQueries({ queryKey: ["backups"] });
  };
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 font-semibold"><HardDriveDownload size={17} /> {t("Backups")}</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            {t("Everything Lumen keeps: settings, users, dashboards, charts, datasets and their latest data. Stored on the server; download them to keep a copy elsewhere. Restoring is done on the server (see the admin guide).")}
          </p>
        </div>
        <Button onClick={now} disabled={busy}><HardDriveDownload size={15} /> {t("Back up now")}</Button>
      </div>
      {msg && <div className="mt-3"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
      <div className="mt-4 flex flex-wrap items-end gap-4">
        <label className="flex items-center gap-2 pb-2 text-sm"><input type="checkbox" checked={v.enabled} onChange={(e) => setCfg({ enabled: e.target.checked })} /> {t("Every night")}</label>
        <Field label={t("At")}>
          <Select value={v.hour} onChange={(e) => setCfg({ hour: Number(e.target.value) })} className="w-28">
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
          </Select>
        </Field>
        <Field label={t("Keep the last")}>
          <Select value={v.keep} onChange={(e) => setCfg({ keep: Number(e.target.value) })} className="w-28">
            {[3, 7, 14, 30].map((n) => <option key={n} value={n}>{n}</option>)}
          </Select>
        </Field>
      </div>
      <ul className="mt-4 divide-y divide-slate-100 rounded-lg border border-slate-200">
        {v.backups.map((b) => (
          <li key={b.name} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
            <span className="min-w-0">
              <span className="block truncate font-medium" dir="ltr">{b.name}</span>
              <span className="block text-xs text-slate-500">{formatMoment(b.created_at)} · {(b.size / 1e6).toFixed(1)} MB</span>
            </span>
            <span className="flex gap-1">
              <Button size="sm" variant="secondary" onClick={() => download(b.name)}><Download size={14} /> {t("Download")}</Button>
              <button onClick={() => remove(b.name)} className="rounded p-1.5 text-slate-500 hover:bg-red-50 hover:text-red-600" aria-label={t("Delete")}><Trash2 size={15} /></button>
            </span>
          </li>
        ))}
        {!v.backups.length && <li className="px-3 py-4 text-center text-sm text-slate-500">{t("No backups yet.")}</li>}
      </ul>
    </Card>
  );
}
