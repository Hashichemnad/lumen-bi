/**
 * Theme, language and calendar for the signed-in person: their own choice, else the organisation's default
 * (Settings). Applied to <html> (data-theme, lang, dir) so every screen and chart follows. The last choice
 * is remembered in this browser so the page does not flash in the wrong theme while it loads.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, Fragment, useContext, useEffect, useMemo, type ReactNode } from "react";

import { get, put } from "./api";
import { useAuth } from "./auth";
import { applyChartTheme } from "./chart/palette";
import { setCalendar, setFiscalStart, setWeek, type CalendarDisplay } from "./dates";
import { setLanguage, type Language } from "./i18n";
import type { User } from "./types";

export type Theme = "classic" | "advanced" | "analyst";

export interface RegionalSettings {
  default_theme: Theme;
  default_language: Language;
  calendar: CalendarDisplay;
  week_start: number;
  weekend: number[];
  fiscal_year_start: number;
}

export interface Effective {
  theme: Theme;
  language: Language;
  calendar: CalendarDisplay;
}

export interface Preferences {
  theme?: Theme;
  language?: Language;
  calendar?: CalendarDisplay;
  /** A short AI summary of favourite dashboards, emailed each morning */
  ai_digest?: boolean;
}

const STORE = "lumen.look";
const DEFAULT_REGIONAL: RegionalSettings = {
  default_theme: "classic",
  default_language: "en",
  calendar: "gregorian",
  week_start: 1,
  weekend: [6, 7],
  fiscal_year_start: 1,
};

let regionalNow: RegionalSettings = DEFAULT_REGIONAL;
/** The organisation's calendar settings, for code outside React (chart labels, date presets). */
export function regionalSettings(): RegionalSettings {
  return regionalNow;
}

function remembered(): Effective {
  try {
    const v = JSON.parse(localStorage.getItem(STORE) || "null");
    if (v && v.theme && v.language) return v as Effective;
  } catch {
    /* private mode or bad value */
  }
  return { theme: "classic", language: "en", calendar: "gregorian" };
}

/** Apply a look to the page (also used before React starts). */
export function applyLook(look: Effective) {
  const root = document.documentElement;
  root.dataset.theme = look.theme;
  root.lang = look.language;
  root.dir = look.language === "ar" ? "rtl" : "ltr";
  setLanguage(look.language);
  setCalendar(look.calendar);
  applyChartTheme(look.theme);
  try {
    localStorage.setItem(STORE, JSON.stringify(look));
  } catch {
    /* ignore */
  }
}

// Before the first paint
applyLook(remembered());

interface PrefsState {
  look: Effective;
  regional: RegionalSettings;
  preferences: Preferences;
  savePreferences: (p: Preferences) => Promise<void>;
}

const PrefsContext = createContext<PrefsState | null>(null);

export function PrefsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const settings = useQuery({
    queryKey: ["settings", user?.id],
    queryFn: () => get<{ regional: RegionalSettings; effective: Effective }>("/settings"),
    enabled: !!user,
    staleTime: 5 * 60_000,
  });
  const look = settings.data?.effective ?? remembered();
  const regional = settings.data?.regional ?? DEFAULT_REGIONAL;
  regionalNow = regional;
  setFiscalStart(regional.fiscal_year_start);
  setWeek(regional.week_start, regional.weekend);
  // Apply during render, so the first render with new settings already uses them
  const key = `${look.theme}|${look.language}|${look.calendar}`;
  useMemo(() => applyLook(look), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    // Signed out: keep the remembered look but use the defaults for everything else
    if (!user) regionalNow = DEFAULT_REGIONAL;
  }, [user]);

  const value: PrefsState = {
    look,
    regional,
    preferences: (user?.preferences ?? {}) as Preferences,
    savePreferences: async (p) => {
      const me = await put<User>("/me/preferences", p);
      qc.setQueryData(["me"], me);
      await qc.invalidateQueries({ queryKey: ["settings"] });
    },
  };
  // Redraw everything when the look changes, so charts and text pick up the new theme and language
  return <PrefsContext.Provider value={value}><Fragment key={key}>{children}</Fragment></PrefsContext.Provider>;
}

export function usePrefs(): PrefsState {
  const ctx = useContext(PrefsContext);
  if (!ctx) throw new Error("usePrefs must be used inside PrefsProvider");
  return ctx;
}
