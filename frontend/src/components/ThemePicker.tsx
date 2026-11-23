import clsx from "clsx";
import { Check } from "lucide-react";

import { t } from "../lib/i18n";
import type { Theme } from "../lib/prefs";

/** Small pictures of each theme (fixed colours: they show the theme, not the current one). */
const SWATCH: Record<Theme, { bg: string; side: string; card: string; line: string; accent: string; radius: number; name: string; help: string }> = {
  classic: { bg: "#f5f6f8", side: "#ffffff", card: "#ffffff", line: "#e3e6ea", accent: "#2a78d6", radius: 4, name: "Classic", help: "Light and calm, with roomy cards" },
  advanced: { bg: "#0b0e13", side: "#0f131a", card: "#141922", line: "#232b37", accent: "#5aa2ff", radius: 5, name: "Advanced", help: "Dark, with large numbers" },
  analyst: { bg: "#eceef1", side: "#f4f5f7", card: "#ffffff", line: "#d5d9de", accent: "#1f6fb5", radius: 1, name: "Analyst", help: "Compact and dense, like analyst tools" },
};

function Picture({ theme }: { theme: Theme }) {
  const s = SWATCH[theme];
  return (
    <svg viewBox="0 0 120 72" className="block w-full" aria-hidden="true">
      <rect width="120" height="72" fill={s.bg} />
      <rect width="26" height="72" fill={s.side} stroke={s.line} />
      <rect x="6" y="8" width="14" height="4" rx="1" fill={s.accent} />
      {[18, 26, 34].map((y) => <rect key={y} x="6" y={y} width="14" height="3" rx="1" fill={s.line} />)}
      <rect x="32" y="8" width="82" height="20" rx={s.radius} fill={s.card} stroke={s.line} />
      <rect x="32" y="34" width="39" height="30" rx={s.radius} fill={s.card} stroke={s.line} />
      <rect x="75" y="34" width="39" height="30" rx={s.radius} fill={s.card} stroke={s.line} />
      {[0, 1, 2, 3].map((i) => <rect key={i} x={38 + i * 8} y={58 - (6 + i * 4)} width="5" height={6 + i * 4} fill={s.accent} />)}
      <polyline points="80,56 88,50 96,53 104,44 110,46" fill="none" stroke={s.accent} strokeWidth="1.5" />
      <rect x="38" y="14" width="24" height="4" rx="1" fill={s.line} />
    </svg>
  );
}

export function ThemePicker({ value, onChange, allowDefault, defaultTheme }: {
  value: Theme | null;
  onChange: (t: Theme | null) => void;
  allowDefault?: boolean;
  defaultTheme?: Theme;
}) {
  return (
    <div role="radiogroup" aria-label={t("Theme")} className="grid gap-3 sm:grid-cols-3">
      {(Object.keys(SWATCH) as Theme[]).map((k) => {
        const on = value === k || (value === null && allowDefault && defaultTheme === k);
        return (
          <button key={k} type="button" role="radio" aria-checked={value === k} onClick={() => onChange(k)}
            className={clsx("overflow-hidden rounded-xl border-2 text-start transition-colors", on ? "border-brand-600" : "border-slate-200 hover:border-slate-300")}>
            <Picture theme={k} />
            <span className="flex items-start gap-2 bg-white p-3">
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-slate-900">
                  {t(SWATCH[k].name)}
                  {allowDefault && defaultTheme === k && <span className="ms-1 font-normal text-slate-500">({t("default")})</span>}
                </span>
                <span className="block text-xs text-slate-500">{t(SWATCH[k].help)}</span>
              </span>
              {on && <Check size={16} className="mt-0.5 shrink-0 text-brand-600" />}
            </span>
          </button>
        );
      })}
    </div>
  );
}
