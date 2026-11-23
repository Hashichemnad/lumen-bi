/** Admin → AI: models (cloud and self-hosted), routing, usage, comparing models, and the MCP server. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Bot, CheckCircle2, Cloud, Copy, Pencil, Play, Plus, Server, Trash2, XCircle } from "lucide-react";
import { useState } from "react";

import { Alert, Badge, Button, Card, Field, Input, Modal, PageHeader, Select } from "../../components/ui";
import { del, get, post, put } from "../../lib/api";
import type { AiFeature, AiProvider, ConnectedApp } from "../../lib/ai";
import { formatMoment } from "../../lib/dates";
import { t } from "../../lib/i18n";

const FEATURES: { key: AiFeature; label: string }[] = [
  { key: "ask", label: "Ask Data" },
  { key: "explain", label: "Explain" },
  { key: "formula", label: "Formula helper" },
  { key: "dashboard", label: "Build dashboards" },
  { key: "digest", label: "Daily summary" },
];
const KIND_LABEL: Record<AiProvider["kind"], string> = {
  anthropic: "Anthropic (Claude)",
  openai: "OpenAI",
  openai_compatible: "Self-hosted (OpenAI-compatible)",
};

type Draft = Omit<AiProvider, "id" | "has_key" | "updated_at"> & { id?: number; api_key: string | null; has_key?: boolean };

const blank = (kind: AiProvider["kind"] = "anthropic", suggested: Record<string, string> = {}): Draft => ({
  name: kind === "openai_compatible" ? "In-house model" : kind === "anthropic" ? "Claude" : "OpenAI",
  kind,
  base_url: kind === "openai_compatible" ? "http://ollama:11434/v1" : "",
  model: suggested[kind] ?? "",
  api_key: "",
  local: kind === "openai_compatible",
  enabled: true,
  is_default: false,
  features: [],
});

function ProviderModal({ draft, suggested, onClose, onSaved }: {
  draft: Draft;
  suggested: Record<string, string>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [d, setD] = useState<Draft>(draft);
  const [error, setError] = useState("");
  const set = (p: Partial<Draft>) => setD((x) => ({ ...x, ...p }));
  const save = async () => {
    setError("");
    try {
      const body = { ...d, api_key: d.id && !d.api_key ? null : d.api_key };
      if (d.id) await put(`/admin/ai/providers/${d.id}`, body);
      else await post("/admin/ai/providers", body);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };
  const selfHosted = d.kind === "openai_compatible";
  return (
    <Modal open wide title={d.id ? t("Edit AI model") : t("Add an AI model")} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button><Button onClick={save}>{t("Save")}</Button></>}>
      {error && <Alert>{error}</Alert>}
      {!d.id && (
        <div role="radiogroup" aria-label={t("Kind")} className="grid gap-2 sm:grid-cols-3">
          {(["anthropic", "openai", "openai_compatible"] as const).map((k) => (
            <button key={k} type="button" role="radio" aria-checked={d.kind === k} onClick={() => setD({ ...blank(k, suggested), features: d.features })}
              className={clsx("flex min-h-14 items-center gap-2 rounded-lg border px-3 text-start text-sm", d.kind === k ? "border-brand-600 bg-brand-50 font-semibold text-brand-700" : "border-slate-200 hover:bg-slate-50")}>
              {k === "openai_compatible" ? <Server size={16} /> : <Cloud size={16} />} {t(KIND_LABEL[k])}
            </button>
          ))}
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("Name")}><Input value={d.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field label={t("Model")} hint={suggested[d.kind] ? t("For example {v0}", { v0: suggested[d.kind] }) : undefined}>
          <Input value={d.model} onChange={(e) => set({ model: e.target.value })} dir="ltr" />
        </Field>
      </div>
      {(selfHosted || d.base_url) && (
        <Field label={t("Address")} hint={selfHosted ? t("The OpenAI-compatible address, ending in /v1 (Ollama, vLLM, LM Studio, Azure OpenAI)") : t("Leave empty for the service's own address")}>
          <Input value={d.base_url} onChange={(e) => set({ base_url: e.target.value })} dir="ltr" placeholder="https://" />
        </Field>
      )}
      <Field label={t("API key")} hint={d.has_key ? t("Saved. Leave empty to keep it.") : selfHosted ? t("Only if your server needs one") : undefined}>
        <Input type="password" autoComplete="off" value={d.api_key ?? ""} onChange={(e) => set({ api_key: e.target.value })} dir="ltr" />
      </Field>
      {selfHosted && (
        <label className="flex items-start gap-3 rounded-lg border border-slate-200 p-3 text-sm">
          <input type="checkbox" className="mt-0.5 h-4 w-4" checked={d.local} onChange={(e) => set({ local: e.target.checked })} />
          <span>
            <span className="font-medium">{t("Runs on our own servers")}</span>
            <span className="block text-slate-500">{t("Only models marked like this may see data marked sensitive.")}</span>
          </span>
        </label>
      )}
      <fieldset>
        <legend className="mb-1 text-sm font-medium text-slate-700">{t("Use it for")}</legend>
        <div className="flex flex-wrap gap-2">
          {FEATURES.map((f) => {
            const on = !d.features.length || d.features.includes(f.key);
            return (
              <button key={f.key} type="button" aria-pressed={on}
                onClick={() => {
                  const all = d.features.length ? d.features : FEATURES.map((x) => x.key);
                  const next = on ? all.filter((x) => x !== f.key) : [...all, f.key];
                  set({ features: next.length === FEATURES.length ? [] : next });
                }}
                className={clsx("rounded-full border px-3 py-1 text-sm", on ? "border-brand-600 bg-brand-50 text-brand-700" : "border-slate-200 text-slate-500")}>
                {t(f.label)}
              </button>
            );
          })}
        </div>
      </fieldset>
      <div className="flex flex-wrap gap-5 text-sm">
        <label className="flex items-center gap-2"><input type="checkbox" className="h-4 w-4" checked={d.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> {t("On")}</label>
        <label className="flex items-center gap-2"><input type="checkbox" className="h-4 w-4" checked={d.is_default} onChange={(e) => set({ is_default: e.target.checked })} /> {t("Default model")}</label>
      </div>
    </Modal>
  );
}

function ProvidersCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["ai-providers"], queryFn: () => get<{ providers: AiProvider[]; suggested_models: Record<string, string> }>("/admin/ai/providers") });
  const [editing, setEditing] = useState<Draft | null>(null);
  const [tests, setTests] = useState<Record<number, { ok: boolean; text: string } | "running">>({});
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ["ai-providers"] }), qc.invalidateQueries({ queryKey: ["ai-status"] })]);
  const suggested = q.data?.suggested_models ?? {};
  const test = async (p: AiProvider) => {
    setTests((x) => ({ ...x, [p.id]: "running" }));
    const r = await post<{ ok: boolean; reply?: string; ms?: number; error?: string }>(`/admin/ai/providers/${p.id}/test`);
    setTests((x) => ({ ...x, [p.id]: r.ok ? { ok: true, text: t("Works ({v0} ms)", { v0: r.ms ?? 0 }) } : { ok: false, text: r.error ?? "" } }));
  };
  const remove = async (p: AiProvider) => {
    if (!confirm(t("Remove {name}?", { name: p.name }))) return;
    await del(`/admin/ai/providers/${p.id}`);
    await refresh();
  };
  const list = q.data?.providers ?? [];
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">{t("AI models")}</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            {t("Models only see field names, a few common values and totals, read with each person's own access. Data marked sensitive goes only to models on your own servers.")}
          </p>
        </div>
        <Button onClick={() => setEditing(blank("anthropic", suggested))}><Plus size={16} /> {t("Add model")}</Button>
      </div>
      {list.length === 0 && q.data && <p className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-600">{t("No AI model yet. AI features stay hidden until you add one.")}</p>}
      <ul className="mt-4 divide-y divide-slate-100">
        {list.map((p) => {
          const r = tests[p.id];
          return (
            <li key={p.id} className="flex flex-wrap items-center gap-3 py-3">
              <span className={clsx("flex h-9 w-9 items-center justify-center rounded-lg", p.local ? "bg-emerald-50 text-emerald-700" : "bg-brand-50 text-brand-700")}>
                {p.local ? <Server size={17} /> : <Cloud size={17} />}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2 font-medium">
                  {p.name}
                  {p.is_default && <Badge tone="blue">{t("Default")}</Badge>}
                  {p.local && <Badge tone="green">{t("Own servers")}</Badge>}
                  {!p.enabled && <Badge>{t("Off")}</Badge>}
                </div>
                <div className="text-xs text-slate-500" dir="auto">
                  {t(KIND_LABEL[p.kind])} · <span dir="ltr">{p.model}</span> · {p.features.length ? p.features.map((f) => t(FEATURES.find((x) => x.key === f)?.label ?? f)).join(", ") : t("All features")}
                </div>
                {r && r !== "running" && (
                  <div className={clsx("mt-1 flex items-center gap-1 text-xs", r.ok ? "text-emerald-700" : "text-red-700")}>
                    {r.ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />} {r.text}
                  </div>
                )}
              </div>
              <div className="flex gap-1">
                <Button size="sm" variant="secondary" onClick={() => test(p)} disabled={r === "running"}><Play size={13} /> {r === "running" ? t("Testing…") : t("Test")}</Button>
                <button onClick={() => setEditing({ ...p, api_key: "" })} className="rounded p-1.5 text-slate-500 hover:bg-slate-100" aria-label={t("Edit")}><Pencil size={15} /></button>
                <button onClick={() => remove(p)} className="rounded p-1.5 text-slate-500 hover:bg-red-50 hover:text-red-600" aria-label={t("Delete")}><Trash2 size={15} /></button>
              </div>
            </li>
          );
        })}
      </ul>
      {editing && <ProviderModal draft={editing} suggested={suggested} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); await refresh(); }} />}
    </Card>
  );
}

// ------------------------------------------------------------------ MCP
interface McpSettings {
  enabled: boolean;
  min_role: "viewer" | "explorer" | "creator" | "admin";
  server_url: string;
  connections: ConnectedApp[];
}

function McpCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-mcp"], queryFn: () => get<McpSettings>("/admin/mcp") });
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  if (!q.data) return null;
  const s = q.data;
  const save = async (p: Partial<McpSettings>) => {
    setError("");
    try {
      qc.setQueryData(["admin-mcp"], await put<McpSettings>("/admin/mcp", { enabled: s.enabled, min_role: s.min_role, ...p }));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };
  const disconnect = async (c: ConnectedApp) => {
    await del(`/admin/mcp/connections/${c.user_id}/${encodeURIComponent(c.client_id)}`);
    await qc.invalidateQueries({ queryKey: ["admin-mcp"] });
  };
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 font-semibold"><Bot size={17} /> {t("Connect Claude and other AI apps (MCP)")}</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            {t("People can add Lumen to Claude as a connector. Claude then reads dashboards and data as that person: read only, their row-level security applies, and every request is in the audit log.")}
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm font-medium">
          <input type="checkbox" className="h-4 w-4" checked={s.enabled} onChange={(e) => save({ enabled: e.target.checked })} /> {t("On")}
        </label>
      </div>
      {error && <div className="mt-3"><Alert>{error}</Alert></div>}
      {s.enabled && (
        <div className="mt-4 space-y-4">
          <div className="grid gap-4 sm:grid-cols-[1fr_220px]">
            <Field label={t("Server address (paste it into Claude: Settings → Connectors → Add custom connector)")}>
              <div className="flex gap-2">
                <Input readOnly value={s.server_url} dir="ltr" onFocus={(e) => e.target.select()} />
                <Button variant="secondary" onClick={async () => { await navigator.clipboard.writeText(s.server_url); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
                  <Copy size={14} /> {copied ? t("Copied") : t("Copy")}
                </Button>
              </div>
            </Field>
            <Field label={t("Who may connect")}>
              <Select value={s.min_role} onChange={(e) => save({ min_role: e.target.value as McpSettings["min_role"] })}>
                <option value="viewer">{t("Everyone")}</option>
                <option value="explorer">{t("Explorers and up")}</option>
                <option value="creator">{t("Creators and up")}</option>
                <option value="admin">{t("Admins only")}</option>
              </Select>
            </Field>
          </div>
          <div>
            <h3 className="mb-2 text-sm font-semibold">{t("Connected now")}</h3>
            {s.connections.length ? (
              <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                {s.connections.map((c) => (
                  <li key={`${c.user_id}-${c.client_id}`} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                    <span className="min-w-0">
                      <span className="font-medium">{c.user}</span> · {c.name}
                      <span className="block text-xs text-slate-500">{c.last_used_at ? t("Last used {when}", { when: formatMoment(c.last_used_at) }) : t("Not used yet")}</span>
                    </span>
                    <Button size="sm" variant="secondary" onClick={() => disconnect(c)}>{t("Disconnect")}</Button>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-slate-500">{t("Nobody has connected an app yet.")}</p>}
          </div>
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ usage
interface Usage {
  summary: { provider: string; feature: string; requests: number; failed: number; input_tokens: number; output_tokens: number; avg_ms: number }[];
  recent: { at: string; user: string; provider: string; feature: string; status: string; error: string; ms: number }[];
}

function UsageCard() {
  const q = useQuery({ queryKey: ["ai-usage"], queryFn: () => get<Usage>("/admin/ai/usage?days=30") });
  if (!q.data) return null;
  const feature = (f: string) => t(FEATURES.find((x) => x.key === f)?.label ?? f);
  return (
    <Card className="p-5">
      <h2 className="font-semibold">{t("Use in the last 30 days")}</h2>
      {q.data.summary.length ? (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-slate-500">
                <th className="border-b border-slate-200 py-2 text-start font-medium">{t("Model")}</th>
                <th className="border-b border-slate-200 py-2 text-start font-medium">{t("Feature")}</th>
                <th className="border-b border-slate-200 py-2 text-end font-medium">{t("Requests")}</th>
                <th className="border-b border-slate-200 py-2 text-end font-medium">{t("Failed")}</th>
                <th className="border-b border-slate-200 py-2 text-end font-medium">{t("Tokens in / out")}</th>
                <th className="border-b border-slate-200 py-2 text-end font-medium">{t("Average time")}</th>
              </tr>
            </thead>
            <tbody>
              {q.data.summary.map((r) => (
                <tr key={`${r.provider}-${r.feature}`}>
                  <td className="border-b border-slate-100 py-2">{r.provider}</td>
                  <td className="border-b border-slate-100 py-2">{feature(r.feature)}</td>
                  <td className="border-b border-slate-100 py-2 text-end tabular-nums">{r.requests.toLocaleString()}</td>
                  <td className={clsx("border-b border-slate-100 py-2 text-end tabular-nums", r.failed && "text-red-700")}>{r.failed.toLocaleString()}</td>
                  <td className="border-b border-slate-100 py-2 text-end tabular-nums">{r.input_tokens.toLocaleString()} / {r.output_tokens.toLocaleString()}</td>
                  <td className="border-b border-slate-100 py-2 text-end tabular-nums">{(r.avg_ms / 1000).toFixed(1)} s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="mt-2 text-sm text-slate-500">{t("No AI requests yet.")}</p>}
      {q.data.recent.some((r) => r.status === "failed") && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer font-medium text-slate-700">{t("Recent failures")}</summary>
          <ul className="mt-2 space-y-1 text-xs text-slate-600">
            {q.data.recent.filter((r) => r.status === "failed").slice(0, 10).map((r, i) => (
              <li key={i}>{formatMoment(r.at)} · {r.provider} · {feature(r.feature)}: {r.error}</li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ compare
interface TestSet {
  dataset_id: number | null;
  questions: { question: string; expect_fields: string[]; expect_chart: string }[];
}
interface CompareResult {
  provider_id: number;
  provider: string;
  skipped: string;
  score: number | null;
  avg_ms?: number | null;
  results: { question: string; ok: boolean; ms: number; title: string; chart: string; fields: string[]; rows: number; error: string }[];
}

function CompareCard() {
  const qc = useQueryClient();
  const providers = useQuery({ queryKey: ["ai-providers"], queryFn: () => get<{ providers: AiProvider[] }>("/admin/ai/providers") });
  const saved = useQuery({ queryKey: ["ai-test-set"], queryFn: () => get<TestSet>("/admin/ai/test-set") });
  const datasets = useQuery({ queryKey: ["catalog"], queryFn: () => get<{ id: number; name: string }[]>("/catalog/datasets") });
  const [set, setSet] = useState<TestSet | null>(null);
  const [picked, setPicked] = useState<number[] | null>(null);
  const [results, setResults] = useState<CompareResult[] | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const s = set ?? saved.data ?? null;
  const list = (providers.data?.providers ?? []).filter((p) => p.enabled);
  const chosen = picked ?? list.map((p) => p.id);
  if (!s || list.length === 0) return null;
  const update = (next: TestSet) => setSet(next);
  const run = async () => {
    setRunning(true);
    setError("");
    try {
      await put("/admin/ai/test-set", s);
      await qc.invalidateQueries({ queryKey: ["ai-test-set"] });
      const r = await post<{ results: CompareResult[] }>("/admin/ai/compare", { ...s, provider_ids: chosen });
      setResults(r.results);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not run the comparison"));
    } finally {
      setRunning(false);
    }
  };
  return (
    <Card className="p-5">
      <h2 className="font-semibold">{t("Compare models")}</h2>
      <p className="mt-1 text-sm text-slate-500">{t("Ask the same questions of each model and check which fields it uses. Saved for next time.")}</p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Field label={t("Data")}>
          <Select value={s.dataset_id ?? ""} onChange={(e) => update({ ...s, dataset_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">{t("Choose a dataset")}</option>
            {(datasets.data ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </Select>
        </Field>
        <fieldset>
          <legend className="mb-1 text-sm font-medium text-slate-700">{t("Models")}</legend>
          <div className="flex flex-wrap gap-2">
            {list.map((p) => (
              <label key={p.id} className="flex items-center gap-1.5 text-sm">
                <input type="checkbox" checked={chosen.includes(p.id)} onChange={(e) => setPicked(e.target.checked ? [...chosen, p.id] : chosen.filter((x) => x !== p.id))} /> {p.name}
              </label>
            ))}
          </div>
        </fieldset>
      </div>
      <div className="mt-4 space-y-2">
        {s.questions.map((q, i) => (
          <div key={i} className="grid gap-2 sm:grid-cols-[1fr_200px_130px_auto]">
            <Input value={q.question} placeholder={t("Question")} aria-label={t("Question")} dir="auto"
              onChange={(e) => update({ ...s, questions: s.questions.map((x, j) => (j === i ? { ...x, question: e.target.value } : x)) })} />
            <Input value={q.expect_fields.join(", ")} placeholder={t("Fields it should use")} aria-label={t("Fields it should use")}
              onChange={(e) => update({ ...s, questions: s.questions.map((x, j) => (j === i ? { ...x, expect_fields: e.target.value.split(",").map((f) => f.trim()).filter(Boolean) } : x)) })} />
            <Select value={q.expect_chart} aria-label={t("Chart it should pick")}
              onChange={(e) => update({ ...s, questions: s.questions.map((x, j) => (j === i ? { ...x, expect_chart: e.target.value } : x)) })}>
              <option value="">{t("Any chart")}</option>
              {["kpi", "bar", "line", "area", "donut", "table", "scatter", "heatmap", "treemap", "map"].map((c) => <option key={c} value={c}>{c}</option>)}
            </Select>
            <button onClick={() => update({ ...s, questions: s.questions.filter((_, j) => j !== i) })} className="rounded p-2 text-slate-500 hover:bg-red-50 hover:text-red-600" aria-label={t("Remove")}><Trash2 size={15} /></button>
          </div>
        ))}
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={() => update({ ...s, questions: [...s.questions, { question: "", expect_fields: [], expect_chart: "" }] })} disabled={s.questions.length >= 30}>
            <Plus size={14} /> {t("Add question")}
          </Button>
          <Button size="sm" onClick={run} disabled={running || !s.dataset_id || !s.questions.some((q) => q.question.trim()) || !chosen.length}>
            <Play size={14} /> {running ? t("Running…") : t("Run comparison")}
          </Button>
        </div>
      </div>
      {error && <div className="mt-3"><Alert>{error}</Alert></div>}
      {results && (
        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          {results.map((r) => (
            <div key={r.provider_id} className="rounded-lg border border-slate-200 p-4">
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="font-semibold">{r.provider}</h3>
                {r.score !== null && <span className={clsx("text-2xl font-bold tabular-nums", r.score >= 80 ? "text-emerald-700" : r.score >= 50 ? "text-amber-700" : "text-red-700")}>{r.score}%</span>}
              </div>
              {r.skipped ? <p className="mt-2 text-sm text-slate-500">{t(r.skipped)}</p> : (
                <>
                  {r.avg_ms != null && <p className="text-xs text-slate-500">{t("Average {v0} s per question", { v0: (r.avg_ms / 1000).toFixed(1) })}</p>}
                  <ul className="mt-2 space-y-1.5 text-sm">
                    {r.results.map((x, i) => (
                      <li key={i} className="flex gap-2">
                        {x.ok ? <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-emerald-600" /> : <XCircle size={15} className="mt-0.5 shrink-0 text-red-600" />}
                        <span className="min-w-0">
                          <span className="block" dir="auto">{x.question}</span>
                          <span className="block text-xs text-slate-500">{x.error || `${x.chart} · ${x.fields.join(", ")} · ${x.rows} ${t("rows")}`}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function AdminAi() {
  return (
    <>
      <PageHeader title={t("AI")} description={t("Models for Ask Data, explanations, formulas and summaries, and connecting Claude to Lumen.")} />
      <div className="space-y-6">
        <ProvidersCard />
        <McpCard />
        <CompareCard />
        <UsageCard />
      </div>
    </>
  );
}
