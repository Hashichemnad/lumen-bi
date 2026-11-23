import type { QueryColumn } from "../types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function parseDate(v: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(v);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4] ?? 0), Number(m[5] ?? 0));
}

export function formatDate(v: unknown, grain?: string | null, withTime = false): string {
  if (v === null || v === undefined || v === "") return "";
  const d = parseDate(String(v));
  if (!d) return String(v);
  const day = `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  switch (grain) {
    case "year": return String(d.getFullYear());
    case "quarter": return `Q${Math.floor(d.getMonth() / 3) + 1} ${d.getFullYear()}`;
    case "month": return `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
    case "week": return `Week of ${day}`;
    case "hour":
    case "minute": return `${day} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    default: return withTime ? `${day} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` : day;
  }
}

export function compactNumber(n: number, decimals = 1): string {
  const abs = Math.abs(n);
  const units: [number, string][] = [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]];
  for (const [size, suffix] of units) {
    if (abs >= size) return `${(n / size).toFixed(abs / size >= 100 ? 0 : decimals).replace(/\.0$/, "")}${suffix}`;
  }
  return n.toLocaleString("en-US", { maximumFractionDigits: abs < 10 ? 2 : 0 });
}

/** A value as people should read it, following the field's format. compact: 12.9K style (axes, tiles). */
export function formatValue(v: unknown, col?: Pick<QueryColumn, "type" | "format" | "grain" | "part">, compact = false): string {
  if (v === null || v === undefined) return "";
  const fmt = col?.format ?? { type: "text" };
  if (col?.part) {
    const n = Number(v);
    if (col.part === "month") return MONTHS[n - 1] ?? String(v);
    if (col.part === "weekday") return WEEKDAYS[n] ?? String(v);
    if (col.part === "quarter") return `Q${n}`;
    return String(v);
  }
  if (typeof v === "number") {
    if (fmt.type === "percent") {
      const d = fmt.decimals ?? 1;
      return `${(v * 100).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}%`;
    }
    const decimals = fmt.decimals ?? (col?.type === "integer" ? 0 : 2);
    const body = compact
      ? compactNumber(v)
      : v.toLocaleString("en-US", { minimumFractionDigits: Number.isInteger(v) && decimals > 0 && fmt.type !== "currency" ? 0 : decimals, maximumFractionDigits: decimals });
    if (fmt.type === "currency") return `${fmt.currency ?? "AED"} ${body}`;
    if (fmt.plain) return String(v);
    return body;
  }
  if (typeof v === "boolean") return v ? "True" : "False";
  if (col?.type === "date" || col?.type === "datetime" || fmt.type === "date" || fmt.type === "datetime") {
    return formatDate(v, col?.grain ?? (fmt as { grain?: string }).grain, col?.type === "datetime" && !col?.grain);
  }
  return String(v);
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
