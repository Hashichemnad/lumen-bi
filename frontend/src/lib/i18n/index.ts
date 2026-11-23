/**
 * Translations. Text is written in English in the code and looked up by its English wording:
 *   t("Save")                       -> "حفظ" in Arabic
 *   t("Welcome, {name}", { name })  -> placeholders are filled in after translation
 * Missing translations fall back to English. The language is set once at start-up (and the app is redrawn
 * when it changes), so t() can be used anywhere, also outside components.
 */
import { AR } from "./ar";

export type Language = "en" | "ar";

let current: Language = "en";

export function setLanguage(lang: Language) {
  current = lang;
}

export function language(): Language {
  return current;
}

export function isRtl(): boolean {
  return current === "ar";
}

export function t(text: string, vars?: Record<string, string | number | null | undefined>): string {
  let out = current === "ar" ? (AR[text] ?? text) : text;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k] ?? "") : m));
  return out;
}

/** The locale for numbers and dates: Arabic text with Western digits, as used in the UAE. */
export function locale(): string {
  return current === "ar" ? "ar-AE-u-nu-latn" : "en-GB";
}
