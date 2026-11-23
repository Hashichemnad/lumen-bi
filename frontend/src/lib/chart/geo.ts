/**
 * Geography for map charts. Countries are filled shapes (Natural Earth 1:50m via world-atlas, loaded only
 * when a map is shown). Emirates and cities have no boundaries in that data, so they are shown as sized
 * bubbles at known coordinates.
 */
import type { FeatureCollection, Geometry } from "geojson";
import type { GeometryCollection, Topology } from "topojson-specification";

const norm = (s: string) =>
  s.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/^(the |emirate of |state of |kingdom of )/, "").replace(/[^a-z0-9؀-ۿ]+/g, "");

/** Alternative names and codes → the country name used by the map data. */
const COUNTRY_ALIASES: Record<string, string> = {
  uae: "United Arab Emirates", ae: "United Arab Emirates", are: "United Arab Emirates", emirates: "United Arab Emirates",
  "الإمارات": "United Arab Emirates", "الاماراتالعربيةالمتحدة": "United Arab Emirates", "الإماراتالعربيةالمتحدة": "United Arab Emirates", "الامارات": "United Arab Emirates",
  ksa: "Saudi Arabia", sa: "Saudi Arabia", sau: "Saudi Arabia", saudi: "Saudi Arabia", "السعودية": "Saudi Arabia", "المملكةالعربيةالسعودية": "Saudi Arabia",
  qa: "Qatar", qat: "Qatar", "قطر": "Qatar",
  om: "Oman", omn: "Oman", "عمان": "Oman", "عُمان": "Oman",
  kw: "Kuwait", kwt: "Kuwait", "الكويت": "Kuwait",
  bh: "Bahrain", bhr: "Bahrain", "البحرين": "Bahrain",
  eg: "Egypt", egy: "Egypt", "مصر": "Egypt",
  jo: "Jordan", jor: "Jordan", "الأردن": "Jordan", "الاردن": "Jordan",
  lb: "Lebanon", lbn: "Lebanon", "لبنان": "Lebanon",
  iq: "Iraq", irq: "Iraq", "العراق": "Iraq", ir: "Iran", irn: "Iran", "إيران": "Iran",
  ye: "Yemen", yem: "Yemen", "اليمن": "Yemen", sy: "Syria", syr: "Syria", "سوريا": "Syria",
  ma: "Morocco", mar: "Morocco", "المغرب": "Morocco", dz: "Algeria", dza: "Algeria", tn: "Tunisia", tun: "Tunisia",
  ly: "Libya", lby: "Libya", sd: "Sudan", sdn: "Sudan", ps: "Palestine", pse: "Palestine", "فلسطين": "Palestine",
  tr: "Turkey", tur: "Turkey", turkiye: "Turkey",
  in: "India", ind: "India", "الهند": "India", pk: "Pakistan", pak: "Pakistan", "باكستان": "Pakistan",
  bd: "Bangladesh", bgd: "Bangladesh", lk: "Sri Lanka", lka: "Sri Lanka", np: "Nepal", npl: "Nepal",
  ph: "Philippines", phl: "Philippines", "الفلبين": "Philippines", id: "Indonesia", idn: "Indonesia",
  cn: "China", chn: "China", "الصين": "China", jp: "Japan", jpn: "Japan", kr: "South Korea", kor: "South Korea", southkorea: "South Korea", korea: "South Korea",
  us: "United States of America", usa: "United States of America", unitedstates: "United States of America", america: "United States of America", "الولاياتالمتحدة": "United States of America",
  uk: "United Kingdom", gb: "United Kingdom", gbr: "United Kingdom", greatbritain: "United Kingdom", britain: "United Kingdom", england: "United Kingdom", "المملكةالمتحدة": "United Kingdom", "بريطانيا": "United Kingdom",
  de: "Germany", deu: "Germany", fr: "France", fra: "France", it: "Italy", ita: "Italy", es: "Spain", esp: "Spain",
  nl: "Netherlands", nld: "Netherlands", ch: "Switzerland", che: "Switzerland", ru: "Russia", rus: "Russia", russianfederation: "Russia",
  ca: "Canada", can: "Canada", au: "Australia", aus: "Australia", br: "Brazil", bra: "Brazil", mx: "Mexico", mex: "Mexico",
  za: "South Africa", zaf: "South Africa", ng: "Nigeria", nga: "Nigeria", ke: "Kenya", ken: "Kenya", et: "Ethiopia", eth: "Ethiopia",
  sg: "Singapore", sgp: "Singapore", my: "Malaysia", mys: "Malaysia", th: "Thailand", tha: "Thailand", vn: "Vietnam", vnm: "Vietnam",
};

/** Emirates and major cities (lon, lat), English and Arabic names. */
const PLACES: [string[], number, number][] = [
  [["Abu Dhabi", "AUH", "أبوظبي", "أبو ظبي", "ابوظبي"], 54.3773, 24.4539],
  [["Dubai", "DXB", "دبي"], 55.2708, 25.2048],
  [["Sharjah", "SHJ", "الشارقة"], 55.4209, 25.3463],
  [["Ajman", "AJM", "عجمان"], 55.5136, 25.4052],
  [["Umm Al Quwain", "Umm al-Quwain", "UAQ", "أم القيوين"], 55.5552, 25.5647],
  [["Ras Al Khaimah", "Ras al-Khaimah", "RAK", "رأس الخيمة"], 55.9762, 25.8007],
  [["Fujairah", "FUJ", "الفجيرة"], 56.3265, 25.1288],
  [["Al Ain", "العين"], 55.7447, 24.2075],
  [["Al Dhafra", "Western Region", "الظفرة"], 53.7, 23.65],
  [["Ruwais", "الرويس"], 52.73, 24.11],
  [["Khor Fakkan", "خورفكان"], 56.3567, 25.3393],
  [["Kalba", "كلباء"], 56.3542, 25.0742],
  [["Dibba", "دبا"], 56.2611, 25.6197],
  [["Hatta", "حتا"], 56.1272, 24.7983],
  [["Riyadh", "الرياض"], 46.6753, 24.7136],
  [["Jeddah", "Jiddah", "جدة"], 39.1925, 21.4858],
  [["Dammam", "الدمام"], 50.0888, 26.4207],
  [["Khobar", "Al Khobar", "الخبر"], 50.2083, 26.2172],
  [["Mecca", "Makkah", "مكة"], 39.8579, 21.3891],
  [["Medina", "Madinah", "المدينة"], 39.5692, 24.5247],
  [["Doha", "الدوحة"], 51.531, 25.2854],
  [["Muscat", "مسقط"], 58.3829, 23.588],
  [["Salalah", "صلالة"], 54.0924, 17.0151],
  [["Sohar", "صحار"], 56.7071, 24.3473],
  [["Kuwait City", "مدينة الكويت"], 47.9774, 29.3759],
  [["Manama", "المنامة"], 50.586, 26.2285],
  [["Cairo", "القاهرة"], 31.2357, 30.0444],
  [["Amman", "عمّان", "عمان العاصمة"], 35.9284, 31.9454],
  [["Beirut", "بيروت"], 35.5018, 33.8938],
  [["Mumbai", "Bombay"], 72.8777, 19.076],
  [["Delhi", "New Delhi"], 77.209, 28.6139],
  [["Karachi"], 67.0011, 24.8607],
  [["London"], -0.1276, 51.5072],
  [["Istanbul"], 28.9784, 41.0082],
];

const PLACE_INDEX = new Map<string, [number, number, string]>();
for (const [names, lon, lat] of PLACES) for (const n of names) PLACE_INDEX.set(norm(n), [lon, lat, names[0]]);

type World = FeatureCollection<Geometry, { name: string }>;
let world: Promise<World> | null = null;

export function loadWorld(): Promise<World> {
  world ??= Promise.all([import("world-atlas/countries-50m.json"), import("topojson-client")]).then(([topo, client]) => {
    const t = ((topo as unknown as { default?: Topology }).default ?? topo) as unknown as Topology;
    const countries = t.objects.countries as GeometryCollection<{ name: string }>;
    return client.feature(t, countries) as unknown as World;
  });
  return world;
}

export function countryName(value: unknown, names: Set<string>, lowerIndex: Map<string, string>): string | null {
  if (value === null || value === undefined) return null;
  const raw = String(value).trim();
  if (names.has(raw)) return raw;
  const key = norm(raw);
  return COUNTRY_ALIASES[key] ?? COUNTRY_ALIASES[raw] ?? lowerIndex.get(key) ?? null;
}

export function place(value: unknown): [number, number, string] | null {
  if (value === null || value === undefined) return null;
  return PLACE_INDEX.get(norm(String(value))) ?? null;
}

function bbox(g: Geometry): [number, number, number, number] {
  let [x0, y0, x1, y1] = [180, 90, -180, -90];
  const walk = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number") {
      const [x, y] = c as number[];
      x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
    } else if (Array.isArray(c)) c.forEach(walk);
  };
  if ("coordinates" in g) walk(g.coordinates);
  return [x0, y0, x1, y1];
}

export interface Region {
  geo: World;
  /** [[west, north], [east, south]] for ECharts boundingCoords, or undefined for the whole world */
  bounds?: [[number, number], [number, number]];
}

/** Only the land around the data (so the map zooms to it), cut to the polygons near it. */
export function regionAround(w: World, points: [number, number][], margin = 4): Region {
  if (!points.length) return { geo: w };
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const [x0, y0, x1, y1] = [Math.min(...xs) - margin, Math.min(...ys) - margin, Math.max(...xs) + margin, Math.max(...ys) + margin];
  if (x1 - x0 > 200) return { geo: w };
  const near = (g: Geometry) => {
    const [a, b, c, d] = bbox(g);
    return a <= x1 && c >= x0 && b <= y1 && d >= y0;
  };
  const features = w.features.flatMap((f) => {
    const g = f.geometry;
    if (g.type === "MultiPolygon") {
      // Keep only the parts near the data (drops overseas territories far away)
      const parts = g.coordinates.filter((poly) => near({ type: "Polygon", coordinates: poly }));
      return parts.length ? [{ ...f, geometry: { type: "MultiPolygon" as const, coordinates: parts } }] : [];
    }
    return near(g) ? [f] : [];
  });
  return { geo: { ...w, features }, bounds: [[x0, y1], [x1, y0]] };
}

export function countryCenters(w: World, names: string[]): [number, number][] {
  const wanted = new Set(names);
  return w.features
    .filter((f) => wanted.has(f.properties.name))
    .map((f) => {
      const [a, b, c, d] = bbox(f.geometry);
      return [(a + c) / 2, (b + d) / 2] as [number, number];
    });
}

export function countryIndex(w: World) {
  const names = new Set(w.features.map((f) => f.properties.name));
  const lower = new Map([...names].map((n) => [norm(n), n]));
  return { names, lower };
}
