import { BarChart, HeatmapChart, LineChart, MapChart, PieChart, ScatterChart, TreemapChart } from "echarts/charts";
import {
  GeoComponent, GridComponent, LegendComponent, MarkLineComponent, TitleComponent, TooltipComponent, VisualMapComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import type { EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

echarts.use([
  BarChart, LineChart, PieChart, ScatterChart, HeatmapChart, TreemapChart, MapChart,
  GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, MarkLineComponent, GeoComponent, TitleComponent,
  CanvasRenderer,
]);

export { echarts };

export interface MarkEvent {
  row: number;
  x: number;
  y: number;
}

export interface EChartHandle {
  png: () => string | null;
}

/** A thin React wrapper: one ECharts instance per element, resized with its container. */
const EChart = forwardRef<EChartHandle, {
  option: EChartsCoreOption;
  height: number;
  onMark?: (e: MarkEvent) => void;
  maps?: Record<string, object>;
}>(function EChart({ option, height, onMark, maps }, ref) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const onMarkRef = useRef(onMark);
  onMarkRef.current = onMark;

  useImperativeHandle(ref, () => ({
    png: () => chart.current?.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#ffffff" }) ?? null,
  }));

  useEffect(() => {
    if (!el.current) return;
    const c = echarts.init(el.current, undefined, { renderer: "canvas" });
    chart.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el.current);
    c.on("click", (raw) => {
      const params = raw as unknown as { data?: { row?: number }; event?: { event?: MouseEvent } };
      const data = params.data;
      const ev = params.event?.event;
      if (data && typeof data.row === "number" && onMarkRef.current && ev) {
        onMarkRef.current({ row: data.row, x: ev.clientX, y: ev.clientY });
      }
    });
    return () => {
      ro.disconnect();
      c.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    for (const [name, geo] of Object.entries(maps ?? {})) {
      echarts.registerMap(name, geo as Parameters<typeof echarts.registerMap>[1]);
    }
    chart.current?.setOption(option, { notMerge: true });
  }, [option, maps]);

  return <div ref={el} style={{ height, width: "100%" }} />;
});

export default EChart;
