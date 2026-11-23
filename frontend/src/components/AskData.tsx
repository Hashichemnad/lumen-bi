import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowUp, Loader2, Sparkles } from "lucide-react";
import { lazy, Suspense, useState } from "react";

import { post, get } from "../lib/api";
import { aiLanguage, useAi, type AskAnswer } from "../lib/ai";
import { t } from "../lib/i18n";
import { Alert, Card } from "./ui";

const AskResult = lazy(() => import("./AskResult"));

interface CatalogItem {
  id: number;
  name: string;
}

const EXAMPLES = ["Total sales by month this year", "Top 5 regions by amount", "How many orders last quarter?"];

/** Ask a question in plain English or Arabic; get a chart and a short answer from your own data. */
export default function AskData({ datasetId }: { datasetId?: number }) {
  const can = useAi();
  const [q, setQ] = useState("");
  const [dataset, setDataset] = useState<number | "">(datasetId ?? "");
  const datasets = useQuery({
    queryKey: ["catalog"],
    queryFn: () => get<CatalogItem[]>("/catalog/datasets"),
    enabled: can("ask") && datasetId === undefined,
  });
  const ask = useMutation({
    mutationFn: (question: string) =>
      post<AskAnswer>("/ai/ask", { question, dataset_id: dataset || null, language: aiLanguage() }),
  });
  if (!can("ask")) return null;
  const submit = (text = q) => {
    const question = text.trim();
    if (!question || ask.isPending) return;
    setQ(question);
    ask.mutate(question);
  };
  return (
    <section aria-labelledby="ask-h" className="space-y-3">
      <Card className="p-4">
        <h2 id="ask-h" className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Sparkles size={16} className="text-brand-600" /> {t("Ask your data")}
        </h2>
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <div className="flex min-h-11 min-w-0 flex-[1_1_320px] items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-100">
            <label htmlFor="ask-q" className="sr-only">{t("Question")}</label>
            <input id="ask-q" value={q} onChange={(e) => setQ(e.target.value)} maxLength={1000} autoComplete="off"
              placeholder={t("For example: sales by region this year")}
              className="min-w-0 flex-1 bg-transparent py-2 text-sm text-slate-900 outline-none placeholder:text-slate-400" />
            <button type="submit" disabled={!q.trim() || ask.isPending} aria-label={t("Ask")}
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-40">
              {ask.isPending ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={16} strokeWidth={2.4} />}
            </button>
          </div>
          {datasetId === undefined && (
            <select aria-label={t("Data to ask about")} value={dataset} onChange={(e) => setDataset(e.target.value ? Number(e.target.value) : "")}
              className="h-11 max-w-56 rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-700">
              <option value="">{t("Any data (Lumen chooses)")}</option>
              {(datasets.data ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          )}
        </form>
        {!ask.data && !ask.isPending && !ask.error && (
          <div className="mt-3 flex flex-wrap gap-2">
            {EXAMPLES.map((e) => (
              <button key={e} type="button" onClick={() => submit(t(e))}
                className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:border-brand-300 hover:bg-brand-50 hover:text-brand-700">
                {t(e)}
              </button>
            ))}
          </div>
        )}
        {ask.isPending && <p className="mt-3 text-sm text-slate-500" role="status">{t("Reading your data…")}</p>}
        {ask.error && <div className="mt-3"><Alert>{(ask.error as Error).message}</Alert></div>}
      </Card>
      {ask.data && (
        <Suspense fallback={<Card className="h-64 animate-pulse"><span className="sr-only">{t("Loading…")}</span></Card>}>
          <div className={clsx(ask.isPending && "opacity-50")}>
            <AskResult answer={ask.data} />
          </div>
        </Suspense>
      )}
    </section>
  );
}
