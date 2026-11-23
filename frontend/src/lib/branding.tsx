import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { get } from "./api";

export interface Branding {
  name: string;
  accent: string | null;
  logo: string | null;
  login_message: string;
}

const DEFAULT: Branding = { name: "Lumen", accent: null, logo: null, login_message: "" };

/** The organisation's name, logo and colour (Admin → Settings → Branding); public, so the sign-in page has it. */
export function useBranding(): Branding {
  const q = useQuery({ queryKey: ["branding"], queryFn: () => get<Branding>("/branding"), staleTime: 10 * 60_000 });
  return q.data ?? DEFAULT;
}

/** Applies the accent colour and the page title. The accent replaces the theme's buttons and highlights
 * (500–700); the light tints mix it with the page colour, so it works in every theme. */
export function BrandingEffect() {
  const b = useBranding();
  useEffect(() => {
    document.title = b.name === "Lumen" ? "Lumen BI" : b.name;
    const root = document.documentElement.style;
    const vars = ["50", "100", "500", "600", "700"].map((s) => `--color-brand-${s}`);
    if (!b.accent) {
      vars.forEach((v) => root.removeProperty(v));
      return;
    }
    const dark = document.documentElement.dataset.theme === "advanced";
    root.setProperty("--color-brand-500", b.accent);
    root.setProperty("--color-brand-600", b.accent);
    root.setProperty("--color-brand-700", dark ? `color-mix(in oklab, ${b.accent} 65%, white)` : `color-mix(in oklab, ${b.accent} 80%, black)`);
    root.setProperty("--color-brand-50", `color-mix(in oklab, ${b.accent} ${dark ? 18 : 9}%, var(--color-white))`);
    root.setProperty("--color-brand-100", `color-mix(in oklab, ${b.accent} ${dark ? 28 : 18}%, var(--color-white))`);
  }, [b.accent, b.name]);
  return null;
}
