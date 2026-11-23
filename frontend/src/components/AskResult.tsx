import { useQuery } from "@tanstack/react-query";
import { Check, ExternalLink, Info, Save } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { get, post } from "../lib/api";
import { saveDraft, type AskAnswer } from "../lib/ai";
import { useAuth } from "../lib/auth";
import { resolvedType } from "../lib/chart/showme";
import type { Catalog, SavedChart } from "../lib/chart/types";
import { t } from "../lib/i18n";
import { useWorkspace } from "../lib/workspace";
import ChartView from "./chart/ChartView";
import { Button, Card } from "./ui";

const NO_IDENTITY: unknown[] = [];

/** An Ask Data answer: the chart, a short answer in words, and how the question was read. */
export default function AskResult({ answer }: { answer: AskAnswer }) {
  const navigate = useNavigate();
  const { hasRole } = useAuth();
  const { target } = useWorkspace();
  const [saved, setSaved] = useState<SavedChart | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const catalog = useQuery({
    queryKey: ["catalog", answer.dataset_id],
    queryFn: () => get<Catalog>(`/catalog/datasets/${answer.dataset_id}`),
  });
  const type = catalog.data ? resolvedType(answer.config, catalog.data.fields) : null;
  const name = answer.title || t("Answer");
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const c = await post<SavedChart>("/charts", {
        name, dataset_id: answer.dataset_id, config: answer.config, query: answer.query, workspace_id: target,
      });
      setSaved(c);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const openBuilder = () => {
    saveDraft({ dataset_id: answer.dataset_id, name, config: answer.config });
    navigate(`/charts/new?dataset=${answer.dataset_id}&draft=1`);
  };
  return (
    <Card className="overflow-hidden" >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-slate-900">{name}</h3>
          <p className="text-xs text-slate-500">{answer.dataset_name}</p>
        </div>
        {hasRole("explorer") && (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={openBuilder}><ExternalLink size={14} /> {t("Open in chart builder")}</Button>
            {saved ? (
              <Link to={`/charts/${saved.id}`} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-emerald-50 px-3 text-sm font-medium text-emerald-700">
                <Check size={14} /> {t("Saved")}
              </Link>
            ) : (
              <Button size="sm" onClick={save} disabled={saving}><Save size={14} /> {t("Save chart")}</Button>
            )}
          </div>
        )}
      </div>
      {answer.answer && <p className="px-5 pt-4 text-[15px] leading-relaxed text-slate-800" dir="auto">{answer.answer}</p>}
      <div className="px-3 py-3">
        {type ? (
          <ChartView cfg={answer.config} type={type} result={answer.result} identity={NO_IDENTITY} height={320} />
        ) : (
          <div className="h-80 animate-pulse rounded-lg bg-slate-50" />
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-slate-100 bg-slate-50 px-5 py-2.5 text-xs text-slate-500">
        {answer.note && <span className="inline-flex items-center gap-1.5" dir="auto"><Info size={13} /> {answer.note}</span>}
        <span>{t("Answered by {name}. Check important numbers.", { name: answer.provider })}</span>
        {error && <span className="text-red-700">{error}</span>}
      </div>
    </Card>
  );
}
