/**
 * Chart colours. Categorical hues are used in this fixed order (validated for colour-blind separation on
 * the white chart surface); never cycled or generated. Past the cap, values fold into "Other".
 */
export const CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const LIGHT_CATEGORICAL = [...CATEGORICAL];
/** The same eight hues stepped for the dark surface (validated on #141922: worst adjacent CVD ΔE 8.4,
 * normal-vision 19.3, all ≥ 3:1). */
const DARK_CATEGORICAL = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"];
/** Charts where every pair of colours can touch (scatter, maps) validate only the first three. */
export const ALL_PAIRS_CAP = 3;
export let OTHER = "#b9b7ae";
export const OTHER_LABEL = "Other";

/** One hue, light to dark, for magnitude (heat maps, shaded tables, filled maps). */
export const SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
const LIGHT_SEQUENTIAL = [...SEQUENTIAL];
export const DIVERGING = { negative: "#e34948", mid: "#f0efec", positive: "#2a78d6" };

export const INK = {
  primary: "#0b0b0b",
  secondary: "#52514e",
  muted: "#898781",
  grid: "#e1e0d9",
  axis: "#c3c2b7",
  surface: "#ffffff",
  /** Quiet fills: empty map areas, calendar days, alternate bands */
  wash: "#f0efec",
  wash2: "#e1e0d9",
  band: "#f3f2ee",
  track: "#f7f7f5",
  tooltipBorder: "rgba(11,11,11,0.10)",
  pointer: "rgba(0,0,0,0.04)",
};
const LIGHT_INK = { ...INK };
const DARK_INK = {
  primary: "#e8ebf0",
  secondary: "#b3bac5",
  muted: "#9aa3b0",
  grid: "#232b37",
  axis: "#3a4453",
  surface: "#141922",
  wash: "#232b37",
  wash2: "#323c4a",
  band: "#181e28",
  track: "#1b2230",
  tooltipBorder: "rgba(255,255,255,0.12)",
  pointer: "rgba(255,255,255,0.05)",
};
const LATIN = '"Inter Variable", Inter, "IBM Plex Sans Arabic", system-ui, -apple-system, "Segoe UI", sans-serif';
export let FONT = LATIN;

/** Switch chart colours to a theme. Charts drawn after this use the new colours. */
export function applyChartTheme(theme: string) {
  const dark = theme === "advanced";
  CATEGORICAL.splice(0, CATEGORICAL.length, ...(dark ? DARK_CATEGORICAL : LIGHT_CATEGORICAL));
  // On a dark surface, near zero recedes toward the surface (dark) and large values are light
  SEQUENTIAL.splice(0, SEQUENTIAL.length, ...(dark ? [...LIGHT_SEQUENTIAL].reverse() : LIGHT_SEQUENTIAL));
  Object.assign(INK, dark ? DARK_INK : LIGHT_INK);
  DIVERGING.mid = dark ? "#323a46" : "#f0efec";
  DIVERGING.negative = dark ? "#e66767" : "#e34948";
  DIVERGING.positive = dark ? "#3987e5" : "#2a78d6";
  OTHER = dark ? "#5c6470" : "#b9b7ae";
  FONT = theme === "analyst" ? '"IBM Plex Sans", "IBM Plex Sans Arabic", system-ui, sans-serif' : LATIN;
}

/** Colour follows the entity: the slot comes from the value's position in the dimension's full value list
 * (not the filtered result), so filtering never repaints the values that remain. */
export function colorMap(allValues: unknown[], present: unknown[], cap = CATEGORICAL.length, weights?: Map<string, number>) {
  const order = new Map<string, number>();
  allValues.forEach((v, i) => order.set(String(v), i));
  const byOrder = (a: string, b: string) => (order.get(a) ?? 1e9) - (order.get(b) ?? 1e9) || a.localeCompare(b);
  const ranked = [...new Set(present.map(String))].sort(byOrder);
  // Past the cap, the largest values keep their colours and the smallest fold into "Other"
  const keep =
    ranked.length > cap
      ? (weights ? [...ranked].sort((a, b) => (weights.get(b) ?? 0) - (weights.get(a) ?? 0)) : ranked).slice(0, cap - 1).sort(byOrder)
      : ranked;
  // Slots are taken by stable rank among *all* values, but never beyond the cap
  const slot = new Map<string, string>();
  const used = new Set<number>();
  for (const v of keep) {
    let i = (order.get(v) ?? 0) % cap;
    while (used.has(i)) i = (i + 1) % cap;
    used.add(i);
    slot.set(v, CATEGORICAL[i]);
  }
  return {
    color: (v: unknown) => slot.get(String(v)) ?? OTHER,
    folded: (v: unknown) => !slot.has(String(v)),
    kept: keep,
    hasOther: ranked.length > keep.length,
  };
}

export function sequential(t: number): string {
  if (!Number.isFinite(t)) return SEQUENTIAL[0];
  const i = Math.max(0, Math.min(SEQUENTIAL.length - 1, Math.round(t * (SEQUENTIAL.length - 1))));
  return SEQUENTIAL[i];
}

/** White or near-black text on a fill, by the fill's luminance. */
export function inkOn(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 ? "#0b0b0b" : "#ffffff";
}
