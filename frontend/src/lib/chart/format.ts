import { calendarDisplay, fiscalQuarterOf, fiscalYearOf, hijriDay } from "../dates";
import { language, locale, t } from "../i18n";
import type { QueryColumn } from "../types";

const fmts = new Map<string, Intl.DateTimeFormat>();
function intl(opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  // English month names as in "Sep 2026" (British English writes "Sept")
  const loc = language() === "ar" ? locale() : opts.day ? "en-GB" : "en-US";
  const key = `${loc}|${JSON.stringify(opts)}`;
  let f = fmts.get(key);
  if (!f) fmts.set(key, (f = new Intl.DateTimeFormat(loc, opts)));
  return f;
}

function parseDate(v: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(v);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4] ?? 0), Number(m[5] ?? 0));
}

export function formatDate(v: unknown, grain?: string | null, withTime = false): string {
  if (v === null || v === undefined || v === "") return "";
  const d = parseDate(String(v));
  if (!d) return String(v);
  const day = calendarDisplay() === "hijri" && !withTime ? hijriDay(d) : intl({ day: "numeric", month: "short", year: "numeric" }).format(d);
  switch (grain) {
    case "year": return String(d.getFullYear());
    case "fiscal_year": return t("FY{year}", { year: fiscalYearOf(d) });
    case "fiscal_quarter": return t("FY{year} Q{n}", { year: fiscalYearOf(d), n: fiscalQuarterOf(d) });
    case "quarter": return t("Q{n} {year}", { n: Math.floor(d.getMonth() / 3) + 1, year: d.getFullYear() });
    case "month": return intl({ month: "short", year: "numeric" }).format(d);
    case "week": return t("Week of {day}", { day });
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
    if (col.part === "month") return n >= 1 && n <= 12 ? intl({ month: "short" }).format(new Date(2026, n - 1, 1)) : String(v);
    // 2026-06-01 is a Monday, so ISO weekday n is that date plus n - 1 days
    if (col.part === "weekday") return n >= 1 && n <= 7 ? intl({ weekday: "short" }).format(new Date(2026, 5, n)) : String(v);
    if (col.part === "quarter" || col.part === "fiscal_quarter") return t("Q{n}", { n });
    if (col.part === "fiscal_year") return t("FY{year}", { year: n });
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
