import { Plus, Trash2 } from "lucide-react";

import { Button, Input } from "./ui";

export type Row = { key: string; value: string };

export const toRows = (map: Record<string, string>): Row[] => Object.entries(map).map(([key, value]) => ({ key, value }));

export const toMap = (rows: Row[]): Record<string, string> =>
  Object.fromEntries(rows.filter((r) => r.key.trim() && r.value.trim()).map((r) => [r.key.trim(), r.value.trim()]));

export default function KeyValueEditor({
  rows,
  onChange,
  keyPlaceholder,
  valuePlaceholder,
  addLabel = "Add",
}: {
  rows: Row[];
  onChange: (rows: Row[]) => void;
  keyPlaceholder: string;
  valuePlaceholder: string;
  addLabel?: string;
}) {
  const set = (i: number, patch: Partial<Row>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex gap-2">
          <Input placeholder={keyPlaceholder} value={row.key} onChange={(e) => set(i, { key: e.target.value })} />
          <Input placeholder={valuePlaceholder} value={row.value} onChange={(e) => set(i, { value: e.target.value })} />
          <Button type="button" variant="ghost" aria-label="Remove" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <Trash2 size={16} />
          </Button>
        </div>
      ))}
      <Button type="button" variant="secondary" size="sm" onClick={() => onChange([...rows, { key: "", value: "" }])}>
        <Plus size={14} /> {addLabel}
      </Button>
    </div>
  );
}
