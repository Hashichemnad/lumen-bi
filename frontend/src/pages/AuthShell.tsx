import type { ReactNode } from "react";

import { Logo } from "../components/Layout";
import { useBranding } from "../lib/branding";
import { language } from "../lib/i18n";
import { applyLook } from "../lib/prefs";

/** Before signing in there is no saved preference yet: switch the language for this browser only. */
function LanguageSwitch() {
  const other = language() === "ar" ? "en" : "ar";
  return (
    <button
      type="button"
      onClick={() => {
        const root = document.documentElement;
        applyLook({ theme: (root.dataset.theme as "classic") || "classic", language: other, calendar: "gregorian" });
        window.location.reload();
      }}
      className="rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-white/70"
      lang={other}
    >
      {other === "ar" ? "العربية" : "English"}
    </button>
  );
}

export default function AuthShell({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  const { name } = useBranding();
  return (
    <div className="relative flex min-h-full items-center justify-center bg-slate-50 p-4">
      <div className="absolute end-4 top-4"><LanguageSwitch /></div>
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2.5">
          <Logo size={32} />
          <span className="font-display text-xl font-bold tracking-tight">{name}</span>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-xl font-bold">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
          <div className="mt-5">{children}</div>
        </div>
      </div>
    </div>
  );
}
