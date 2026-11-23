/**
 * Dashboard extras (milestone 12): KPI status chips, the scorecard, comments and annotations, versions,
 * subscriptions, alerts and display links for TV screens.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, Bell, Check, CircleSlash, Copy, History, Link2, Mail, MessageSquare, RotateCcw, Send, Trash2, X } from "lucide-react";
import { useState } from "react";

import { Alert, Badge, Button, Field, Input, Modal, Select } from "../../components/ui";
import { del, get, patch, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { formatValue } from "../../lib/chart/format";
import type { Dashboard, Tile } from "../../lib/dashboard/types";
import { formatMoment } from "../../lib/dates";
import type {
  AlertItem, CommentItem, DisplayLinkItem, HistoryItem, KpiResult, KpiStatus, Recipients, SubscriptionItem, VersionItem,
} from "../../lib/governance";
import { STATUS_LABEL } from "../../lib/governance";
import { t } from "../../lib/i18n";
import type { QueryColumn } from "../../lib/types";

// ------------------------------------------------------------------ KPI status
export const STATUS_STYLE: Record<KpiStatus, { chip: string; dot: string; bar: string }> = {
  on_track: { chip: "bg-emerald-50 text-emerald-700", dot: "bg-emerald-600", bar: "bg-emerald-600" },
  at_risk: { chip: "bg-amber-50 text-amber-800", dot: "bg-amber-500", bar: "bg-amber-500" },
  off_track: { chip: "bg-red-50 text-red-700", dot: "bg-red-600", bar: "bg-red-600" },
  no_data: { chip: "bg-slate-100 text-slate-600", dot: "bg-slate-300", bar: "bg-slate-300" },
};

export function StatusChip({ status }: { status: KpiStatus }) {
  const icon = status === "on_track" ? <Check size={12} strokeWidth={3} /> : status === "no_data" ? <CircleSlash size={12} /> : <AlertTriangle size={12} strokeWidth={2.6} />;
  return (
    <span className={clsx("inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold", STATUS_STYLE[status].chip)}>
      {icon}{t(STATUS_LABEL[status])}
    </span>
  );
}

export function kpiValue(k: KpiResult, v: number | null): string {
  if (v === null) return "—";
  return formatValue(v, { type: "number", format: (k.format ?? { type: "number" }) as unknown as QueryColumn["format"], grain: null, part: null } as Pick<QueryColumn, "type" | "format" | "grain" | "part">, Math.abs(v) >= 100000);
}

/** Actual against target, as shown in a section header. */
export function KpiLine({ k }: { k: KpiResult }) {
  return (
    <span className="flex flex-wrap items-center gap-2 text-sm">
      <span className="font-display text-lg font-bold tabular-nums text-slate-900" dir="ltr">{kpiValue(k, k.actual)}</span>
      <span className="text-slate-500">{t("Target {target}", { target: `${k.direction === "lower" ? "≤" : "≥"} ${kpiValue(k, k.target)}` })}</span>
      <StatusChip status={k.status} />
    </span>
  );
}

export function Scorecard({ kpis, onGo, onClose }: { kpis: KpiResult[]; onGo: (sectionId: string) => void; onClose: () => void }) {
  const counts = { on_track: 0, at_risk: 0, off_track: 0, no_data: 0 } as Record<KpiStatus, number>;
  for (const k of kpis) counts[k.status]++;
  return (
    <Modal open wide title={t("Scorecard")} onClose={onClose}>
      <div className="flex flex-wrap gap-2">
        {(["on_track", "at_risk", "off_track", "no_data"] as KpiStatus[]).filter((s) => counts[s]).map((s) => (
          <span key={s} className={clsx("rounded-full px-3 py-1 text-sm font-semibold", STATUS_STYLE[s].chip)}>{counts[s]} {t(STATUS_LABEL[s])}</span>
        ))}
      </div>
      <ul className="divide-y divide-slate-100">
        {kpis.map((k) => {
          const share = k.actual !== null && k.target ? Math.max(0, Math.min(1, k.direction === "lower" ? k.target / Math.max(k.actual, 1e-9) : k.actual / k.target)) : 0;
          return (
            <li key={k.section} className="py-3">
              <button onClick={() => { onGo(k.section); onClose(); }} className="w-full text-start">
                <div className="flex flex-wrap items-center gap-2">
                  {k.code && <span className="section-code rounded-md bg-brand-50 px-2 py-0.5 text-xs font-bold text-brand-700">{k.code}</span>}
                  <span className="min-w-0 flex-1 font-semibold text-slate-900">{k.title}</span>
                  <StatusChip status={k.status} />
                </div>
                <div className="mt-2 flex items-center gap-3">
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100" role="img" aria-label={t("{share}% of the target", { share: Math.round(share * 100) })}>
                    <div className={clsx("h-full rounded-full", STATUS_STYLE[k.status].bar)} style={{ width: `${share * 100}%` }} />
                  </div>
                  <span className="w-40 text-end text-sm tabular-nums text-slate-600" dir="ltr">{kpiValue(k, k.actual)} / {kpiValue(k, k.target)}</span>
                </div>
                {k.error && <p className="mt-1 text-xs text-red-700">{t(k.error)}</p>}
              </button>
            </li>
          );
        })}
      </ul>
      {!kpis.length && <p className="text-sm text-slate-500">{t("No KPI goals yet. Edit a section to give it a target.")}</p>}
    </Modal>
  );
}

// ------------------------------------------------------------------ comments and annotations
export function CommentsPanel({ dash, tiles, onClose }: { dash: Dashboard; tiles: Tile[]; onClose: () => void }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["comments", dash.id], queryFn: () => get<CommentItem[]>(`/dashboards/${dash.id}/comments`) });
  const [text, setText] = useState("");
  const [tile, setTile] = useState("");
  const [reply, setReply] = useState<{ id: number; text: string } | null>(null);
  const [showResolved, setShowResolved] = useState(false);
  const [error, setError] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["comments", dash.id] });
  const send = async (body: Record<string, unknown>) => {
    setError("");
    try {
      await post(`/dashboards/${dash.id}/comments`, body);
      await refresh();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
      return false;
    }
  };
  const tileName = (id: string | null) => {
    const tl = tiles.find((x) => x.id === id);
    return tl ? tl.title || (tl.chart_id ? dash.charts[String(tl.chart_id)]?.name : "") || (tl.kind === "section" ? tl.code : "") || t("Tile") : "";
  };
  const roots = (list.data ?? []).filter((c) => !c.parent_id && (showResolved || !c.resolved));
  const replies = (id: number) => (list.data ?? []).filter((c) => c.parent_id === id);
  return (
    <aside aria-labelledby="comments-h" className="fixed inset-y-0 end-0 z-40 flex w-full max-w-sm flex-col border-s border-slate-200 bg-white shadow-xl">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <h2 id="comments-h" className="font-semibold">{t("Comments")}</h2>
        <button onClick={onClose} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100" aria-label={t("Close")}><X size={18} /></button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        <label className="flex items-center gap-2 text-xs text-slate-600">
          <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} /> {t("Show resolved")}
        </label>
        {roots.map((c) => (
          <div key={c.id} className={clsx("rounded-lg border p-3", c.resolved ? "border-slate-100 bg-slate-50" : "border-slate-200")}>
            <CommentBody c={c} tileName={tileName(c.tile_id)} onChanged={refresh} />
            <div className="mt-2 space-y-2 border-s-2 border-slate-100 ps-3">
              {replies(c.id).map((r) => <CommentBody key={r.id} c={r} tileName="" onChanged={refresh} />)}
            </div>
            {reply?.id === c.id ? (
              <form className="mt-2 flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (await send({ body: reply.text, parent_id: c.id })) setReply(null); }}>
                <Input autoFocus value={reply.text} onChange={(e) => setReply({ id: c.id, text: e.target.value })} placeholder={t("Write a reply")} aria-label={t("Write a reply")} />
                <Button size="sm" type="submit" disabled={!reply.text.trim()}>{t("Reply")}</Button>
              </form>
            ) : (
              <button onClick={() => setReply({ id: c.id, text: "" })} className="mt-2 text-xs font-semibold text-brand-700 hover:underline">{t("Reply")}</button>
            )}
          </div>
        ))}
        {list.data && !roots.length && <p className="text-sm text-slate-500">{t("No comments yet. Ask a question or leave a note for the team.")}</p>}
      </div>
      <form className="space-y-2 border-t border-slate-200 p-4" onSubmit={async (e) => { e.preventDefault(); if (await send({ body: text, tile_id: tile || null })) setText(""); }}>
        {error && <Alert>{error}</Alert>}
        <Select value={tile} onChange={(e) => setTile(e.target.value)} aria-label={t("About")}>
          <option value="">{t("The whole dashboard")}</option>
          {tiles.filter((x) => x.kind === "chart" || x.kind === "section").map((x) => <option key={x.id} value={x.id}>{tileName(x.id)}</option>)}
        </Select>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} placeholder={t("Write a comment")} aria-label={t("Write a comment")}
          className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100" />
        <Button type="submit" disabled={!text.trim()} className="w-full"><MessageSquare size={15} /> {t("Comment")}</Button>
        <p className="text-xs text-slate-500">{t("To pin a note to a point of a chart, click the point and choose Add a note here.")}</p>
      </form>
    </aside>
  );
}

function CommentBody({ c, tileName, onChanged }: { c: CommentItem; tileName: string; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(c.body);
  return (
    <div>
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <span className="font-semibold text-slate-800">{c.author || t("Someone")}</span>
        <span>{formatMoment(c.created_at)}</span>
        {c.edited_at && <span>· {t("edited")}</span>}
      </div>
      {(tileName || c.anchor) && (
        <div className="mt-1 flex flex-wrap gap-1">
          {tileName && <Badge>{tileName}</Badge>}
          {c.anchor && <Badge tone="blue">{t("Note at {label}", { label: c.anchor.label })}</Badge>}
        </div>
      )}
      {editing ? (
        <form className="mt-1 flex gap-2" onSubmit={async (e) => { e.preventDefault(); await patch(`/comments/${c.id}`, { body: text }); setEditing(false); onChanged(); }}>
          <Input value={text} onChange={(e) => setText(e.target.value)} aria-label={t("Comment")} />
          <Button size="sm" type="submit">{t("Save")}</Button>
        </form>
      ) : (
        <p className="mt-1 whitespace-pre-wrap text-sm text-slate-800">{c.body}</p>
      )}
      <div className="mt-1 flex gap-3 text-xs">
        {c.can_edit && !editing && <button className="text-slate-500 hover:text-slate-800" onClick={() => setEditing(true)}>{t("Edit")}</button>}
        {!c.parent_id && (c.can_edit || c.can_delete) && (
          <button className="text-slate-500 hover:text-slate-800" onClick={async () => { await patch(`/comments/${c.id}`, { resolved: !c.resolved }); onChanged(); }}>
            {c.resolved ? t("Reopen") : t("Resolve")}
          </button>
        )}
        {c.can_delete && <button className="text-slate-500 hover:text-red-700" onClick={async () => { if (confirm(t("Delete this comment?"))) { await del(`/comments/${c.id}`); onChanged(); } }}>{t("Delete")}</button>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ versions
export function VersionsModal({ kind, id, onClose, onRestored }: { kind: "dashboards" | "charts"; id: number; onClose: () => void; onRestored: () => void }) {
  const list = useQuery({ queryKey: ["versions", kind, id], queryFn: () => get<VersionItem[]>(`/${kind}/${id}/versions`) });
  const [error, setError] = useState("");
  const restore = async (v: VersionItem) => {
    if (!confirm(t("Go back to the version from {when}? The current version is kept, so you can undo this.", { when: formatMoment(v.saved_at) }))) return;
    try {
      await post(`/${kind}/${id}/versions/${v.id}/restore`);
      onRestored();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not restore"));
    }
  };
  return (
    <Modal open title={t("Earlier versions")} onClose={onClose}>
      {error && <Alert>{error}</Alert>}
      <p className="text-sm text-slate-500">{t("The last two versions are kept. A version is saved when someone starts changing it.")}</p>
      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
        {list.data?.map((v) => (
          <li key={v.id} className="flex items-center gap-3 px-3 py-2.5">
            <History size={16} className="shrink-0 text-slate-400" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{v.name}</span>
              <span className="block text-xs text-slate-500">{formatMoment(v.saved_at)} · {v.saved_by}{v.tiles !== null ? ` · ${t("{n} tiles", { n: v.tiles })}` : ""}</span>
            </span>
            <Button size="sm" variant="secondary" onClick={() => restore(v)}><RotateCcw size={14} /> {t("Restore")}</Button>
          </li>
        ))}
        {list.data && !list.data.length && <li className="px-3 py-4 text-center text-sm text-slate-500">{t("No earlier versions yet.")}</li>}
      </ul>
    </Modal>
  );
}

// ------------------------------------------------------------------ schedules
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

type Freq = "daily" | "weekdays" | "weekly" | "monthly" | "15m" | "30m" | "hourly" | "custom";

function parseCron(cron: string): { freq: Freq; time: string; day: number } {
  const p = cron.trim().split(/\s+/);
  const time = p.length === 5 && /^\d+$/.test(p[0]) && /^\d+$/.test(p[1]) ? `${p[1].padStart(2, "0")}:${p[0].padStart(2, "0")}` : "07:00";
  if (cron === "*/15 * * * *") return { freq: "15m", time, day: 1 };
  if (cron === "*/30 * * * *") return { freq: "30m", time, day: 1 };
  if (cron === "0 * * * *") return { freq: "hourly", time, day: 1 };
  if (p.length === 5 && p[2] === "*" && p[3] === "*" && p[4] === "*") return { freq: "daily", time, day: 1 };
  if (p.length === 5 && p[2] === "*" && p[3] === "*" && p[4] === "1-5") return { freq: "weekdays", time, day: 1 };
  if (p.length === 5 && p[2] === "*" && p[3] === "*" && /^[0-6]$/.test(p[4])) return { freq: "weekly", time, day: Number(p[4]) || 7 };
  if (p.length === 5 && /^\d+$/.test(p[2]) && p[3] === "*" && p[4] === "*") return { freq: "monthly", time, day: Number(p[2]) };
  return { freq: "custom", time, day: 1 };
}

function buildCron(freq: Freq, time: string, day: number, custom: string): string {
  const [h, m] = time.split(":").map((x) => Number(x) || 0);
  switch (freq) {
    case "15m": return "*/15 * * * *";
    case "30m": return "*/30 * * * *";
    case "hourly": return "0 * * * *";
    case "daily": return `${m} ${h} * * *`;
    case "weekdays": return `${m} ${h} * * 1-5`;
    case "weekly": return `${m} ${h} * * ${day % 7}`;
    case "monthly": return `${m} ${h} ${Math.min(28, Math.max(1, day))} * *`;
    default: return custom;
  }
}

export function SchedulePicker({ value, onChange, frequent }: { value: string; onChange: (cron: string) => void; frequent?: boolean }) {
  const parsed = parseCron(value);
  const [custom, setCustom] = useState(value);
  const set = (freq: Freq, time = parsed.time, day = parsed.day) => onChange(buildCron(freq, time, day, custom));
  const options: [Freq, string][] = [
    ...(frequent ? ([["15m", "Every 15 minutes"], ["30m", "Every 30 minutes"], ["hourly", "Every hour"]] as [Freq, string][]) : []),
    ["daily", "Every day"], ["weekdays", "Working days (Monday to Friday)"], ["weekly", "Every week"], ["monthly", "Every month"], ["custom", "Custom (cron)"],
  ];
  return (
    <div className="flex flex-wrap items-end gap-2">
      <Field label={t("How often")}>
        <Select value={parsed.freq} onChange={(e) => set(e.target.value as Freq)} className="w-60">
          {options.map(([k, label]) => <option key={k} value={k}>{t(label)}</option>)}
        </Select>
      </Field>
      {parsed.freq === "weekly" && (
        <Field label={t("On")}>
          <Select value={parsed.day === 0 ? 7 : parsed.day} onChange={(e) => set("weekly", parsed.time, Number(e.target.value))} className="w-36">
            {DAYS.map((d, i) => <option key={d} value={i + 1}>{t(d)}</option>)}
          </Select>
        </Field>
      )}
      {parsed.freq === "monthly" && (
        <Field label={t("Day of the month")}>
          <Input type="number" min={1} max={28} value={parsed.day} onChange={(e) => set("monthly", parsed.time, Number(e.target.value))} className="w-24" />
        </Field>
      )}
      {["daily", "weekdays", "weekly", "monthly"].includes(parsed.freq) && (
        <Field label={t("At")}>
          <Input type="time" value={parsed.time} onChange={(e) => set(parsed.freq, e.target.value)} className="w-32" />
        </Field>
      )}
      {parsed.freq === "custom" && (
        <Field label={t("Cron expression")} hint={t("minute hour day month weekday, e.g. 0 6,18 * * *")}>
          <Input value={custom} onChange={(e) => { setCustom(e.target.value); onChange(e.target.value); }} className="w-56 font-mono" />
        </Field>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ recipients and channels
interface Principal {
  id: number;
  name: string;
  email?: string;
}

function RecipientsPicker({ value, onChange, me }: { value: Recipients; onChange: (r: Recipients) => void; me: number }) {
  const dir = useQuery({ queryKey: ["directory"], queryFn: () => get<{ users: Principal[]; groups: Principal[] }>("/directory") });
  const users = dir.data?.users ?? [];
  const groups = dir.data?.groups ?? [];
  const name = (id: number) => { const u = users.find((x) => x.id === id); return u ? u.name || u.email || "" : id === me ? t("Me") : String(id); };
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {value.users.map((id) => (
          <span key={`u${id}`} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs">
            {name(id)}<button type="button" onClick={() => onChange({ ...value, users: value.users.filter((x) => x !== id) })} aria-label={t("Remove")}><X size={12} /></button>
          </span>
        ))}
        {value.groups.map((id) => (
          <span key={`g${id}`} className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs text-brand-700">
            {groups.find((g) => g.id === id)?.name ?? id}<button type="button" onClick={() => onChange({ ...value, groups: value.groups.filter((x) => x !== id) })} aria-label={t("Remove")}><X size={12} /></button>
          </span>
        ))}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <Select value="" aria-label={t("Add a person")} onChange={(e) => e.target.value && onChange({ ...value, users: [...new Set([...value.users, Number(e.target.value)])] })}>
          <option value="">{t("Add a person…")}</option>
          {users.filter((u) => !value.users.includes(u.id)).map((u) => <option key={u.id} value={u.id}>{u.name ? `${u.name} (${u.email})` : u.email}</option>)}
        </Select>
        <Select value="" aria-label={t("Add a group")} onChange={(e) => e.target.value && onChange({ ...value, groups: [...new Set([...value.groups, Number(e.target.value)])] })}>
          <option value="">{t("Add a group…")}</option>
          {groups.filter((g) => !value.groups.includes(g.id)).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </Select>
      </div>
      <p className="text-xs text-slate-500">{t("Each person gets only the data they are allowed to see, and only if they can open this dashboard.")}</p>
    </div>
  );
}

function ChannelsPicker({ dashId, value, onChange }: { dashId: number; value: string[]; onChange: (v: string[]) => void }) {
  const list = useQuery({ queryKey: ["channels", dashId], queryFn: () => get<{ id: string; name: string }[]>(`/dashboards/${dashId}/channels`) });
  return (
    <fieldset>
      <legend className="mb-1 text-sm font-medium text-slate-700">{t("Send by")}</legend>
      <div className="flex flex-wrap gap-2">
        {list.data?.map((c) => {
          const on = value.includes(c.id);
          return (
            <label key={c.id} className={clsx("flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm", on ? "border-brand-600 bg-brand-50 text-brand-700" : "border-slate-200")}>
              <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked ? [...value, c.id] : value.filter((x) => x !== c.id))} />
              {c.id === "email" ? t("Email") : c.id === "whatsapp" ? "WhatsApp" : c.name}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function HistoryList({ items }: { items: HistoryItem[] }) {
  if (!items.length) return null;
  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-slate-500">{t("Recent messages")}</summary>
      <ul className="mt-1 space-y-0.5">
        {items.map((h, i) => (
          <li key={i} className={h.status === "sent" ? "text-slate-600" : "text-red-700"}>
            {formatMoment(h.at)} · {h.channel} · {h.recipient} · {t(h.status === "sent" ? "Sent" : h.status === "skipped" ? "Skipped" : "Failed")}{h.error ? ` (${t(h.error)})` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

// ------------------------------------------------------------------ subscriptions
export function SubscriptionsModal({ dash, state, onClose }: { dash: Dashboard; state: string | null; onClose: () => void }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["subs", dash.id], queryFn: () => get<SubscriptionItem[]>(`/dashboards/${dash.id}/subscriptions`) });
  const blank = (): Omit<SubscriptionItem, "id" | "next_run_at" | "last_run_at" | "last_status" | "last_error" | "owner" | "can_edit" | "history"> => ({
    name: dash.name, recipients: { users: user ? [user.id] : [], groups: [] }, channels: ["email"], format: "pdf", schedule: "0 7 * * 1", state, message: "", enabled: true,
  });
  const [editing, setEditing] = useState<(ReturnType<typeof blank> & { id?: number }) | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["subs", dash.id] });
  const save = async () => {
    if (!editing) return;
    setError("");
    try {
      if (editing.id) await put(`/subscriptions/${editing.id}`, editing);
      else await post(`/dashboards/${dash.id}/subscriptions`, editing);
      setEditing(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };
  const sendNow = async (s: SubscriptionItem) => {
    setNotice(t("Sending…"));
    const r = await post<{ sent: number; failed: number }>(`/subscriptions/${s.id}/send`);
    setNotice(t("Sent {sent} messages, {failed} failed.", { sent: r.sent, failed: r.failed }));
    await refresh();
  };
  return (
    <Modal open wide title={t("Subscriptions")} onClose={onClose}
      footer={editing ? <><Button variant="secondary" onClick={() => setEditing(null)}>{t("Cancel")}</Button><Button onClick={save}>{t("Save")}</Button></> : <Button onClick={() => setEditing(blank())}><Mail size={15} /> {t("New subscription")}</Button>}>
      {error && <Alert>{error}</Alert>}
      {notice && !editing && <Alert tone="blue">{notice}</Alert>}
      {editing ? (
        <div className="space-y-4">
          <Field label={t("Name")}><Input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></Field>
          <SchedulePicker value={editing.schedule} onChange={(schedule) => setEditing({ ...editing, schedule })} />
          <Field label={t("Send as")}>
            <Select value={editing.format} onChange={(e) => setEditing({ ...editing, format: e.target.value as SubscriptionItem["format"] })} className="w-60">
              <option value="pdf">{t("PDF attached")}</option>
              <option value="png">{t("Picture (PNG) attached")}</option>
              <option value="link">{t("Link only")}</option>
            </Select>
          </Field>
          <ChannelsPicker dashId={dash.id} value={editing.channels} onChange={(channels) => setEditing({ ...editing, channels })} />
          {dash.can_manage ? (
            <Field label={t("Recipients")}><RecipientsPicker value={editing.recipients} onChange={(recipients) => setEditing({ ...editing, recipients })} me={user?.id ?? 0} /></Field>
          ) : <p className="text-sm text-slate-600">{t("Sent to you.")}</p>}
          <Field label={t("Message (optional)")}><Input value={editing.message} onChange={(e) => setEditing({ ...editing, message: e.target.value })} /></Field>
          {editing.state && <p className="text-xs text-slate-500">{t("Uses the filters you have chosen now.")}</p>}
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={editing.enabled} onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })} /> {t("On")}</label>
        </div>
      ) : (
        <ul className="divide-y divide-slate-100">
          {list.data?.map((s) => (
            <li key={s.id} className="py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1">
                  <span className="block font-medium">{s.name}</span>
                  <span className="block text-xs text-slate-500">
                    {s.enabled ? t("Next: {when}", { when: formatMoment(s.next_run_at) }) : t("Off")} · {s.format.toUpperCase()} · {s.owner}
                  </span>
                </span>
                {s.last_status === "failed" && <Badge tone="red">{t("Last send failed")}</Badge>}
                {s.can_edit && <Button size="sm" variant="secondary" onClick={() => sendNow(s)}><Send size={14} /> {t("Send now")}</Button>}
                {s.can_edit && <Button size="sm" variant="secondary" onClick={() => setEditing({ ...s })}>{t("Edit")}</Button>}
                {s.can_edit && <Button size="sm" variant="ghost" aria-label={t("Delete")} onClick={async () => { if (confirm(t("Delete this subscription?"))) { await del(`/subscriptions/${s.id}`); await refresh(); } }}><Trash2 size={14} /></Button>}
              </div>
              <HistoryList items={s.history} />
            </li>
          ))}
          {list.data && !list.data.length && <li className="py-6 text-center text-sm text-slate-500">{t("Get this dashboard by email on a schedule, with your own data.")}</li>}
        </ul>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ alerts
function describeCondition(c: AlertItem["condition"]): string {
  if (c.kind === "kpi") return c.status === "at_risk" ? t("the KPI is at risk or off track") : t("the KPI is off track");
  const sign = { gt: ">", gte: "≥", lt: "<", lte: "≤" }[c.op ?? "gt"];
  return t("the value is {sign} {value}", { sign, value: (c.value ?? 0).toLocaleString() });
}

export function AlertsModal({ dash, tiles, kpis, onClose }: { dash: Dashboard; tiles: Tile[]; kpis: KpiResult[]; onClose: () => void }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["alerts", dash.id], queryFn: () => get<AlertItem[]>(`/dashboards/${dash.id}/alerts`) });
  const charts = tiles.filter((x) => x.kind === "chart");
  const kpiSections = kpis.map((k) => k.section);
  const blank = () => ({
    name: "", tile_id: charts[0]?.id ?? "", condition: { kind: "value", op: "gt", value: 0, status: "off_track" } as AlertItem["condition"],
    recipients: { users: user ? [user.id] : [], groups: [] } as Recipients, channels: ["email"], schedule: "*/30 * * * *", enabled: true,
  });
  const [editing, setEditing] = useState<(ReturnType<typeof blank> & { id?: number }) | null>(null);
  const [error, setError] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["alerts", dash.id] });
  const tileName = (id: string) => {
    const tl = tiles.find((x) => x.id === id);
    if (!tl) return id;
    if (tl.kind === "section") return [tl.code, tl.title].filter(Boolean).join(" · ");
    return tl.title || (tl.chart_id ? dash.charts[String(tl.chart_id)]?.name : "") || t("Chart");
  };
  const save = async () => {
    if (!editing) return;
    setError("");
    try {
      const body = { ...editing, name: editing.name || tileName(editing.tile_id) };
      if (editing.id) await put(`/alerts/${editing.id}`, body);
      else await post(`/dashboards/${dash.id}/alerts`, body);
      setEditing(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };
  const isKpi = editing?.condition.kind === "kpi";
  return (
    <Modal open wide title={t("Alerts")} onClose={onClose}
      footer={editing ? <><Button variant="secondary" onClick={() => setEditing(null)}>{t("Cancel")}</Button><Button onClick={save}>{t("Save")}</Button></> : <Button onClick={() => setEditing(blank())}><Bell size={15} /> {t("New alert")}</Button>}>
      {error && <Alert>{error}</Alert>}
      {editing ? (
        <div className="space-y-4">
          <div role="radiogroup" aria-label={t("Alert when")} className="flex flex-wrap gap-2">
            {(["value", "kpi"] as const).map((k) => (
              <button key={k} type="button" role="radio" aria-checked={editing.condition.kind === k} disabled={k === "kpi" && !kpiSections.length}
                onClick={() => setEditing({ ...editing, condition: { ...editing.condition, kind: k }, tile_id: k === "kpi" ? kpiSections[0] : charts[0]?.id ?? "" })}
                className={clsx("min-h-10 rounded-lg border px-3 text-sm disabled:opacity-40", editing.condition.kind === k ? "border-brand-600 bg-brand-50 font-semibold text-brand-700" : "border-slate-200")}>
                {k === "value" ? t("A chart's value crosses a limit") : t("A KPI gets worse")}
              </button>
            ))}
          </div>
          <Field label={isKpi ? t("KPI") : t("Chart")}>
            <Select value={editing.tile_id} onChange={(e) => setEditing({ ...editing, tile_id: e.target.value })}>
              {(isKpi ? kpiSections : charts.map((c) => c.id)).map((id) => <option key={id} value={id}>{tileName(id)}</option>)}
            </Select>
          </Field>
          {isKpi ? (
            <Field label={t("When it is")}>
              <Select value={editing.condition.status} onChange={(e) => setEditing({ ...editing, condition: { ...editing.condition, status: e.target.value as "off_track" | "at_risk" } })} className="w-60">
                <option value="off_track">{t("Off track")}</option>
                <option value="at_risk">{t("At risk or off track")}</option>
              </Select>
            </Field>
          ) : (
            <div className="flex flex-wrap items-end gap-2">
              <Field label={t("When its value is")}>
                <Select value={editing.condition.op} onChange={(e) => setEditing({ ...editing, condition: { ...editing.condition, op: e.target.value as "gt" } })} className="w-44">
                  <option value="gt">{t("more than")}</option><option value="gte">{t("at least")}</option><option value="lt">{t("less than")}</option><option value="lte">{t("at most")}</option>
                </Select>
              </Field>
              <Field label={t("Limit")}><Input type="number" value={editing.condition.value ?? 0} onChange={(e) => setEditing({ ...editing, condition: { ...editing.condition, value: Number(e.target.value) } })} className="w-36" /></Field>
              <p className="pb-2 text-xs text-slate-500">{t("A chart with dimensions uses its grand total.")}</p>
            </div>
          )}
          <SchedulePicker frequent value={editing.schedule} onChange={(schedule) => setEditing({ ...editing, schedule })} />
          <p className="-mt-2 text-xs text-slate-500">{t("Also checked whenever the data is refreshed.")}</p>
          <ChannelsPicker dashId={dash.id} value={editing.channels} onChange={(channels) => setEditing({ ...editing, channels })} />
          {dash.can_manage ? (
            <Field label={t("Recipients")}><RecipientsPicker value={editing.recipients} onChange={(recipients) => setEditing({ ...editing, recipients })} me={user?.id ?? 0} /></Field>
          ) : <p className="text-sm text-slate-600">{t("Sent to you.")}</p>}
          <Field label={t("Name (optional)")}><Input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} placeholder={tileName(editing.tile_id)} /></Field>
          <p className="text-xs text-slate-500">{t("Each person is checked with their own data and told once when it happens, again only after it was back to normal. Teams messages use your data.")}</p>
        </div>
      ) : (
        <ul className="divide-y divide-slate-100">
          {list.data?.map((a) => (
            <li key={a.id} className="py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Bell size={16} className={a.triggered ? "text-red-600" : "text-slate-400"} />
                <span className="min-w-0 flex-1">
                  <span className="block font-medium">{a.name}</span>
                  <span className="block text-xs text-slate-500">{tileName(a.tile_id)} · {describeCondition(a.condition)}{a.last_value ? ` · ${t("Now: {value}", { value: a.last_value })}` : ""}</span>
                </span>
                {a.triggered && <Badge tone="red">{t("Happening now")}</Badge>}
                {!a.enabled && <Badge>{t("Off")}</Badge>}
                {a.last_error && <Badge tone="amber">{t("Check failed")}</Badge>}
                {a.can_edit && <Button size="sm" variant="secondary" onClick={async () => { await post(`/alerts/${a.id}/check`); await refresh(); }}>{t("Check now")}</Button>}
                {a.can_edit && <Button size="sm" variant="secondary" onClick={() => setEditing({ ...a, condition: { op: "gt", value: 0, status: "off_track", ...a.condition } })}>{t("Edit")}</Button>}
                {a.can_edit && <Button size="sm" variant="ghost" aria-label={t("Delete")} onClick={async () => { if (confirm(t("Delete this alert?"))) { await del(`/alerts/${a.id}`); await refresh(); } }}><Trash2 size={14} /></Button>}
              </div>
              {a.last_error && <p className="mt-1 text-xs text-amber-800">{a.last_error}</p>}
              <HistoryList items={a.history} />
            </li>
          ))}
          {list.data && !list.data.length && <li className="py-6 text-center text-sm text-slate-500">{t("Get a message when a number crosses a limit or a KPI goes off track.")}</li>}
        </ul>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ display links (admins)
export function DisplayLinksModal({ dash, onClose }: { dash: Dashboard; onClose: () => void }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["display", dash.id], queryFn: () => get<DisplayLinkItem[]>(`/dashboards/${dash.id}/display-links`) });
  const dir = useQuery({ queryKey: ["directory"], queryFn: () => get<{ users: Principal[] }>("/directory") });
  const [name, setName] = useState(t("TV screen"));
  const [runAs, setRunAs] = useState("");
  const [days, setDays] = useState("");
  const [made, setMade] = useState<string | null>(null);
  const [error, setError] = useState("");
  const create = async () => {
    setError("");
    try {
      const link = await post<DisplayLinkItem>(`/dashboards/${dash.id}/display-links`, { name, run_as_id: Number(runAs), expires_days: days ? Number(days) : null });
      setMade(link.url ?? null);
      await qc.invalidateQueries({ queryKey: ["display", dash.id] });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };
  return (
    <Modal open wide title={t("Display links")} onClose={onClose}>
      <p className="text-sm text-slate-600">{t("A display link opens this dashboard in TV mode without signing in, showing the data of the person you choose. Anyone with the link can see it, so choose a person who sees only what the screen may show.")}</p>
      {error && <Alert>{error}</Alert>}
      {made && (
        <Alert tone="green">
          <span className="block font-semibold">{t("Copy the link now: it is shown only once.")}</span>
          <span className="mt-1 flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate text-xs" dir="ltr">{made}</code>
            <Button size="sm" variant="secondary" onClick={() => navigator.clipboard?.writeText(made)}><Copy size={14} /> {t("Copy")}</Button>
          </span>
        </Alert>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label={t("Name")}><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label={t("Show as")}>
          <Select value={runAs} onChange={(e) => setRunAs(e.target.value)}>
            <option value="">{t("Choose a person…")}</option>
            {dir.data?.users.map((u) => <option key={u.id} value={u.id}>{u.name || u.email}</option>)}
          </Select>
        </Field>
        <Field label={t("Expires after (days)")} hint={t("Empty = never")}><Input type="number" min={1} value={days} onChange={(e) => setDays(e.target.value)} /></Field>
      </div>
      <Button onClick={create} disabled={!runAs || !name.trim()}><Link2 size={15} /> {t("Create link")}</Button>
      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
        {list.data?.map((l) => (
          <li key={l.id} className="flex flex-wrap items-center gap-2 px-3 py-2.5 text-sm">
            <span className="min-w-0 flex-1">
              <span className="block font-medium">{l.name}</span>
              <span className="block text-xs text-slate-500">
                {t("As {name}", { name: l.run_as.name })} · {l.last_used_at ? t("last used {v0}", { v0: formatMoment(l.last_used_at) }) : t("Not used yet")}
                {l.expires_at ? ` · ${t("expires {when}", { when: formatMoment(l.expires_at) })}` : ""}
              </span>
            </span>
            {l.revoked ? <Badge>{t("Revoked")}</Badge> : (
              <Button size="sm" variant="secondary" onClick={async () => { if (confirm(t("Revoke this link? Screens using it stop at once."))) { await del(`/display-links/${l.id}`); await qc.invalidateQueries({ queryKey: ["display", dash.id] }); } }}>{t("Revoke")}</Button>
            )}
          </li>
        ))}
        {list.data && !list.data.length && <li className="px-3 py-4 text-center text-sm text-slate-500">{t("No display links yet.")}</li>}
      </ul>
    </Modal>
  );
}
