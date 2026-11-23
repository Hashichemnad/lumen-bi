import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import EChart from "../../components/chart/EChart";
import { get } from "../../lib/api";
import { FONT, INK } from "../../lib/chart/palette";
import type { Boundary } from "./MapsPage";
import { t } from "../../lib/i18n";

export default function MapPreview({ boundary }: { boundary: Boundary }) {
  const geo = useQuery({ queryKey: ["boundary", boundary.id], queryFn: () => get<{ geojson: { features: { properties: Record<string, unknown> }[] } }>(`/boundaries/${boundary.id}`) });
  const name = `preview-${boundary.id}`;
  const option = useMemo(() => ({
    tooltip: { formatter: (p: { name: string }) => p.name },
    series: [{
      type: "map", map: name, nameProperty: boundary.name_property, roam: true,
      itemStyle: { areaColor: "#cde2fb", borderColor: "#ffffff", borderWidth: 1 },
      emphasis: { itemStyle: { areaColor: "#6da7ec" }, label: { show: true, color: INK.primary, fontFamily: FONT } },
      label: { show: boundary.features <= 30, color: INK.secondary, fontFamily: FONT, fontSize: 11 },
      select: { disabled: true },
    }],
  }), [name, boundary]);
  if (!geo.data) return <p className="text-sm text-slate-500">{t("Loading…")}</p>;
  const names = geo.data.geojson.features.map((f) => String(f.properties?.[boundary.name_property] ?? "")).filter(Boolean);
  return (
    <>
      <EChart option={option} height={380} maps={{ [name]: geo.data.geojson }} />
      <p className="text-xs text-slate-500">{t("Area names a dimension must match ({length}): {v1}{v2}", { length: names.length, v1: names.slice(0, 20).join(", "), v2: names.length > 20 ? "…" : "" })}</p>
    </>
  );
}
