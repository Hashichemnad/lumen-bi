import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Sparkles, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";

import { get, post } from "../lib/api";
import { aiLanguage, type AskAnswer, type FormulaAnswer } from "../lib/ai";
import { t } from "../lib/i18n";
import { Alert, Button, Field, Input, Modal, Select } from "./ui";

/** "What stands out?" for a chart: a few plain bullet points from the AI model. */
export function ExplainModal({ title, request, onClose }: {
  title: string;
  /** POST path and body: /ai/explain with a query, or /ai/explain-tile for a dashboard tile */
  request: { path: string; body: Record<string, unknown> };
  onClose: () => void;
}) {
  const q = useQuery({
    queryKey: ["ai-explain", request.path, JSON.stringify(request.body)],
    queryFn: () => post<{ text: string; provider: string }>(request.path, { ...request.body, title, language: aiLanguage() }),
    retry: false,
    staleTime: Infinity,
  });
  const bullets = (q.data?.text ?? "").split("\n").map((l) => l.replace(/^\s*[-•*]\s*/, "").trim()).filter(Boolean);
  return (
    <Modal open title={t("What stands out: {title}", { title })} onClose={onClose} wide
      footer={<Button variant="secondary" onClick={onClose}>{t("Close")}</Button>}>
      {q.isPending && (
        <p className="flex items-center gap-2 text-sm text-slate-500" role="status"><Loader2 size={16} className="animate-spin" /> {t("Looking at the numbers…")}</p>
      )}
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {q.data && (
        <>
          <ul className="space-y-2" dir="auto">
            {bullets.map((b, i) => (
              <li key={i} className="flex gap-2.5 text-[15px] leading-relaxed text-slate-800">
                <Sparkles size={15} className="mt-1 shrink-0 text-brand-600" /> <span>{b}</span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-slate-500">{t("Written by {name} from the numbers you can see. Check important numbers.", { name: q.data.provider })}</p>
        </>
      )}
    </Modal>
  );
}

/** A one-line "describe the chart" box for the chart builder. */
export function AskBar({ datasetId, onAnswer }: { datasetId: number; onAnswer: (a: AskAnswer) => void }) {
  const [q, setQ] = useState("");
  const ask = useMutation({
    mutationFn: (question: string) => post<AskAnswer>("/ai/ask", { question, dataset_id: datasetId, answer: false, language: aiLanguage() }),
    onSuccess: (a) => { onAnswer(a); setQ(""); },
  });
  return (
    <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (q.trim()) ask.mutate(q.trim()); }}>
      <div className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 focus-within:border-brand-500 focus-within:bg-white focus-within:ring-2 focus-within:ring-brand-100">
        <Sparkles size={15} className="shrink-0 text-brand-600" />
        <label htmlFor="ask-bar" className="sr-only">{t("Describe the chart you want")}</label>
        <input id="ask-bar" value={q} onChange={(e) => setQ(e.target.value)} maxLength={1000} autoComplete="off"
          placeholder={t("Describe the chart you want, e.g. monthly sales by region")}
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-slate-400" />
        {ask.isPending && <Loader2 size={15} className="animate-spin text-slate-400" />}
      </div>
      <Button size="sm" type="submit" disabled={!q.trim() || ask.isPending}>{t("Build it")}</Button>
      {ask.error && <span className="max-w-72 truncate text-xs text-red-700" title={(ask.error as Error).message}>{(ask.error as Error).message}</span>}
    </form>
  );
}

/** "Write it for me": a formula from a description, checked by Lumen before it is offered. */
export function FormulaHelper({ datasetId, onUse }: { datasetId: number; onUse: (a: FormulaAnswer) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const write = useMutation({
    mutationFn: () => post<FormulaAnswer>("/ai/formula", { dataset_id: datasetId, description: text, language: aiLanguage() }),
  });
  useEffect(() => {
    if (!open) write.reset();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="inline-flex items-center gap-1.5 text-sm font-medium text-brand-700 hover:underline">
        <Wand2 size={14} /> {t("Write it for me")}
      </button>
    );
  }
  return (
    <div className="space-y-2 rounded-lg border border-brand-100 bg-brand-50/50 p-3">
      <Field label={t("What should the field show?")}>
        <Input value={text} onChange={(e) => setText(e.target.value)} placeholder={t("e.g. profit margin as a percent of sales")} autoFocus
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); if (text.trim().length > 2) write.mutate(); } }} />
      </Field>
      {write.error && <Alert>{(write.error as Error).message}</Alert>}
      {write.data && (
        <div className="space-y-1 rounded-md bg-white p-2.5 text-sm">
          <code className="block whitespace-pre-wrap break-words font-mono text-[13px] text-slate-900" dir="ltr">{write.data.formula}</code>
          {write.data.explanation && <p className="text-xs text-slate-600" dir="auto">{write.data.explanation}</p>}
          {write.data.sample.length > 0 && (
            <p className="text-xs text-slate-500">{t("First values: {v0}", { v0: write.data.sample.slice(0, 5).map(String).join(", ") })}</p>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" disabled={text.trim().length < 3 || write.isPending} onClick={() => write.mutate()}>
          {write.isPending ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />} {write.data ? t("Try again") : t("Write formula")}
        </Button>
        {write.data && <Button size="sm" onClick={() => { onUse(write.data!); setOpen(false); }}>{t("Use this formula")}</Button>}
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>{t("Cancel")}</Button>
      </div>
    </div>
  );
}

/** A starter dashboard for a dataset: the AI proposes charts, Lumen checks each one and lays them out. */
export function AutoDashboardModal({ workspaceId, onClose, onCreated }: {
  workspaceId: number | null;
  onClose: () => void;
  onCreated: (id: number) => void;
}) {
  const [dataset, setDataset] = useState<number | "">("");
  const [goal, setGoal] = useState("");
  const datasets = useQuery({ queryKey: ["catalog"], queryFn: () => get<{ id: number; name: string }[]>("/catalog/datasets") });
  const make = useMutation({
    mutationFn: () => post<{ id: number }>("/ai/dashboard", { dataset_id: dataset, goal, language: aiLanguage(), workspace_id: workspaceId }),
    onSuccess: (d) => onCreated(d.id),
  });
  return (
    <Modal open title={t("Build a dashboard with AI")} onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
        <Button disabled={!dataset || make.isPending} onClick={() => make.mutate()}>
          {make.isPending ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />} {make.isPending ? t("Building…") : t("Build dashboard")}
        </Button>
      </>}>
      <p className="text-sm text-slate-600">{t("Lumen suggests a few headline numbers, trends and breakdowns. You can change everything afterwards.")}</p>
      <Field label={t("Data")}>
        <Select value={dataset} onChange={(e) => setDataset(e.target.value ? Number(e.target.value) : "")}>
          <option value="">{t("Choose a dataset")}</option>
          {(datasets.data ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </Select>
      </Field>
      <Field label={t("What is it for? (optional)")}>
        <Input value={goal} onChange={(e) => setGoal(e.target.value)} maxLength={1000} placeholder={t("e.g. weekly sales review for branch managers")} />
      </Field>
      {make.error && <Alert>{(make.error as Error).message}</Alert>}
    </Modal>
  );
}
