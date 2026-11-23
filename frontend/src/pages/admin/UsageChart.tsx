import EChart from "../../components/chart/EChart";
import { categoryAxis, TOOLTIP_BASE, tooltipBox } from "../../lib/chart/build";
import { CATEGORICAL, FONT, INK } from "../../lib/chart/palette";

/** One series over days: a 2px line with a hover tooltip (loaded on demand with ECharts). */
export default function UsageChart({ labels, values, name }: { labels: string[]; values: number[]; name: string }) {
  const color = CATEGORICAL[0];
  return (
    <EChart height={210} option={{
      animationDuration: 300,
      textStyle: { fontFamily: FONT },
      grid: { left: 8, right: 12, top: 12, bottom: 8, containLabel: true },
      tooltip: {
        ...TOOLTIP_BASE, trigger: "axis", axisPointer: { type: "line", lineStyle: { color: INK.axis, width: 1 } },
        formatter: (p: { name: string; value: number }[]) => tooltipBox(p[0].name, [{ color, value: p[0].value.toLocaleString(), name }]),
      },
      xAxis: categoryAxis(labels),
      yAxis: { type: "value", minInterval: 1, splitLine: { lineStyle: { color: INK.grid } }, axisLabel: { color: INK.secondary, fontFamily: FONT } },
      series: [{
        name, type: "line", data: values, smooth: false, symbol: "circle", symbolSize: 8, showSymbol: values.length <= 31,
        lineStyle: { width: 2, color }, itemStyle: { color, borderColor: INK.surface, borderWidth: 2 },
        areaStyle: { color, opacity: 0.08 },
      }],
    }} />
  );
}
