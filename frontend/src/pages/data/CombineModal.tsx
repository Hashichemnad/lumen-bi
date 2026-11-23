import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Alert, Button, Field, Input, Modal, Select } from "../../components/ui";
import { get, post, put } from "../../lib/api";
import type { Dataset, JoinSpec } from "../../lib/types";
import { t } from "../../lib/i18n";

type Join = JoinSpec["joins"][number];

export default function CombineModal({ existing, onClose, onSaved }: {
  existing?: Dataset;
  onClose: () => void;
  onSaved?: (d: Dataset) => void;
}) {
  const qc = useQueryClient();
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => get<Dataset[]>("/datasets") });
  const usable = (datasets.data ?? []).filter((d) => d.columns.length && d.id !== existing?.id);
  const [mode, setMode] = useState<"join" | "union">(existing?.kind === "union" ? "union" : "join");
  const [name, setName] = useState(existing?.name ?? "");
  const [base, setBase] = useState<number>(existing?.source.join?.base ?? 0);
  const [joins, setJoins] = useState<Join[]>(existing?.source.join?.joins ?? []);
  const [members, setMembers] = useState<number[]>(existing?.source.union?.datasets ?? []);
  const [sourceColumn, setSourceColumn] = useState(existing?.source.union?.source_column ?? "Source");
  const [error, setError] = useState("");
  const byId = (id: number) => usable.find((d) => d.id === id);

  // Columns available on the left side of join i: the base plus every earlier join (as alias.column)
  const leftColumns = (i: number): string[] => {
    const cols = (byId(base)?.columns ?? []).map((c) => c.name);
    joins.slice(0, i).forEach((j) => (byId(j.dataset)?.columns ?? []).forEach((c) => cols.push(`${j.alias}.${c.name}`)));
    return cols;
  };
  const setJoin = (i: number, patch: Partial<Join>) => setJoins(joins.map((j, k) => (k === i ? { ...j, ...patch } : j)));
  const guessKey = (leftCols: string[], right?: Dataset): { left: string; right: string } => {
    const rightCols = (right?.columns ?? []).map((c) => c.name);
    for (const r of rightCols) {
      const l = leftCols.find((c) => c.toLowerCase() === r.toLowerCase() && !c.includes("."));
      if (l) return { left: l, right: r };
    }
    const id = rightCols.find((c) => c.toLowerCase() === "id");
    const ref = right ? leftCols.find((c) => c.toLowerCase().replace(/[_\s]/g, "") === `${right.name.toLowerCase().replace(/s$/, "")}id`) : undefined;
    return { left: ref ?? leftCols[0] ?? "", right: id ?? rightCols[0] ?? "" };
  };
  const addJoin = () => {
    const right = usable.find((d) => d.id !== base && !joins.some((j) => j.dataset === d.id));
    if (!right) return;
    setJoins([...joins, { dataset: right.id, type: "left", alias: right.name, on: [guessKey(leftColumns(joins.length), right)] }]);
  };

  const save = async () => {
    setError("");
    const source = mode === "join"
      ? { join: { base, joins } }
      : { union: { datasets: members, source_column: sourceColumn } };
    try {
      const body = { name, source };
      const saved = existing ? await put<Dataset>(`/datasets/${existing.id}`, body) : await post<Dataset>("/datasets", body);
      await qc.invalidateQueries({ queryKey: ["datasets"] });
      await qc.invalidateQueries({ queryKey: ["dataset", saved.id] });
      onSaved?.(saved);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };

  return (
    <Modal open wide title={existing ? `Edit ${existing.name}` : t("Combine datasets")} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
        <Button onClick={save} disabled={!name.trim()}>{existing ? t("Save and rebuild") : t("Create")}</Button></>}>
      {error && <Alert>{error}</Alert>}
      {!existing && (
        <div className="flex gap-4 text-sm">
          <label className="flex items-start gap-2"><input type="radio" className="mt-1" checked={mode === "join"} onChange={() => setMode("join")} />
            <span><b>{t("Join")}</b><br /><span className="text-slate-500">{t("Add columns from other datasets by matching keys (e.g. orders + customer details).")}</span></span></label>
          <label className="flex items-start gap-2"><input type="radio" className="mt-1" checked={mode === "union"} onChange={() => setMode("union")} />
            <span><b>{t("Union")}</b><br /><span className="text-slate-500">{t("Stack rows of similar datasets (e.g. one file per branch or month).")}</span></span></label>
        </div>
      )}
      <Field label={t("Name of the new dataset")}><Input value={name} onChange={(e) => setName(e.target.value)} placeholder={mode === "join" ? t("Orders with customers") : t("All branches")} /></Field>

      {mode === "join" ? (
        <div className="space-y-3">
          <Field label={t("Start from")}>
            <Select value={base || ""} onChange={(e) => { setBase(Number(e.target.value)); setJoins([]); }}>
              <option value="">{t("Choose a dataset…")}</option>
              {usable.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </Select>
          </Field>
          {joins.map((j, i) => {
            const right = byId(j.dataset);
            const lefts = leftColumns(i);
            return (
              <div key={i} className="space-y-2 rounded-lg border border-slate-200 p-3">
                <div className="grid gap-2 sm:grid-cols-[1fr_130px_1fr_auto]">
                  <Select value={j.dataset} onChange={(e) => {
                    const d = byId(Number(e.target.value));
                    setJoin(i, { dataset: Number(e.target.value), alias: d?.name ?? j.alias, on: [guessKey(lefts, d)] });
                  }}>
                    {usable.filter((d) => d.id !== base).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </Select>
                  <Select value={j.type} onChange={(e) => setJoin(i, { type: e.target.value as Join["type"] })}>
                    <option value="left">{t("Keep all rows")}</option>
                    <option value="inner">{t("Only matches")}</option>
                    <option value="full">{t("All from both")}</option>
                  </Select>
                  <Input value={j.alias} title={t("Prefix for its columns")} onChange={(e) => setJoin(i, { alias: e.target.value })} />
                  <Button variant="ghost" aria-label={t("Remove")} onClick={() => setJoins(joins.filter((_, k) => k !== i))}><Trash2 size={16} /></Button>
                </div>
                {j.on.map((pair, p) => (
                  <div key={p} className="flex items-center gap-2 text-sm">
                    <Select value={pair.left} onChange={(e) => setJoin(i, { on: j.on.map((x, k) => (k === p ? { ...x, left: e.target.value } : x)) })}>
                      {lefts.map((c) => <option key={c} value={c}>{c}</option>)}
                    </Select>
                    <span className="text-slate-500">=</span>
                    <Select value={pair.right} onChange={(e) => setJoin(i, { on: j.on.map((x, k) => (k === p ? { ...x, right: e.target.value } : x)) })}>
                      {(right?.columns ?? []).map((c) => <option key={c.name} value={c.name}>{right?.name}.{c.name}</option>)}
                    </Select>
                    {j.on.length > 1 && <Button variant="ghost" size="sm" onClick={() => setJoin(i, { on: j.on.filter((_, k) => k !== p) })}><Trash2 size={14} /></Button>}
                  </div>
                ))}
                <Button variant="ghost" size="sm" onClick={() => setJoin(i, { on: [...j.on, guessKey(lefts, right)] })}><Plus size={14} /> {t("Match on another column")}</Button>
              </div>
            );
          })}
          <Button variant="secondary" size="sm" disabled={!base} onClick={addJoin}><Plus size={14} /> {t("Join a dataset")}</Button>
          <p className="text-xs text-slate-500">{t("Joined columns are named \"prefix.column\". \"Keep all rows\" keeps every row of the left side even without a match.")}</p>
        </div>
      ) : (
        <div className="space-y-3">
          <Field label={t("Datasets to stack")}>
            <div className="flex flex-wrap gap-2">
              {usable.map((d) => (
                <label key={d.id} className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1 text-sm">
                  <input type="checkbox" checked={members.includes(d.id)}
                    onChange={(e) => setMembers(e.target.checked ? [...members, d.id] : members.filter((m) => m !== d.id))} />
                  {d.name}
                </label>
              ))}
            </div>
          </Field>
          <Field label={t("Column saying where each row came from (optional)")}><Input value={sourceColumn} onChange={(e) => setSourceColumn(e.target.value)} /></Field>
          <p className="text-xs text-slate-500">{t("Columns are matched by name; a column missing in one dataset is empty for its rows.")}</p>
        </div>
      )}
      <p className="text-xs text-slate-500">{t("The result is rebuilt automatically whenever one of these datasets syncs.")}</p>
    </Modal>
  );
}
