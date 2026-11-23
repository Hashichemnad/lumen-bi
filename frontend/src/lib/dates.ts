/**
 * Dates as people read them: Gregorian, Hijri (Umm al-Qura, the UAE's official Hijri calendar) or both, in
 * the interface language. Set from the regional settings and personal preferences at start-up.
 */
import { language, locale } from "./i18n";

export type CalendarDisplay = "gregorian" | "hijri" | "both";

let calendar: CalendarDisplay = "gregorian";
let fiscalStart = 1;

let week = { start: 1, weekend: [6, 7] };

/** The first day of the week and the weekend, as ISO weekdays (1 = Monday … 7 = Sunday). */
export function setWeek(start: number, weekend: number[]) {
  week = { start, weekend };
}

export function weekSettings() {
  return week;
}

/** The month (1–12) the organisation's fiscal year starts in. */
export function setFiscalStart(month: number) {
  fiscalStart = month;
}

/** Fiscal years are named by the year they end in: July 2026 – June 2027 is FY2027. */
export function fiscalYearOf(d: Date): number {
  const s = fiscalStart - 1;
  return s === 0 ? d.getFullYear() : d.getMonth() >= s ? d.getFullYear() + 1 : d.getFullYear();
}

export function fiscalQuarterOf(d: Date): number {
  return Math.floor(((d.getMonth() - (fiscalStart - 1) + 12) % 12) / 3) + 1;
}

export function setCalendar(c: CalendarDisplay) {
  calendar = c;
}

export function calendarDisplay(): CalendarDisplay {
  return calendar;
}

function fmt(d: Date, opts: Intl.DateTimeFormatOptions, hijri: boolean): string {
  const loc = hijri ? `${language() === "ar" ? "ar-SA" : "en"}-u-ca-islamic-umalqura-nu-latn` : locale();
  return new Intl.DateTimeFormat(loc, opts).format(d);
}

/** A day: "23 Nov 2026", "12 Jumada II 1448 AH" or both. */
export function formatDay(value: Date | string | null | undefined, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium" }) {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value.length === 10 ? `${value}T00:00:00` : value) : value;
  if (Number.isNaN(d.getTime())) return String(value);
  if (calendar === "hijri") return fmt(d, opts, true);
  if (calendar === "both") return `${fmt(d, opts, false)} · ${fmt(d, opts, true)}`;
  return fmt(d, opts, false);
}

/** A moment: date and time (time is the same in both calendars). */
export function formatMoment(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const time = new Intl.DateTimeFormat(locale(), { timeStyle: "short" }).format(d);
  const day = calendar === "hijri" ? fmt(d, { dateStyle: "medium" }, true) : fmt(d, { dateStyle: "medium" }, false);
  return `${day}, ${time}`;
}

/** The Hijri date alone, for a second line under a Gregorian one. */
export function hijriDay(value: Date | string, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium" }): string {
  const d = typeof value === "string" ? new Date(value.length === 10 ? `${value}T00:00:00` : value) : value;
  return fmt(d, opts, true);
}
