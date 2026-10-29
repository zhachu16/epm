import * as echarts from "echarts";
import { useEffect, useRef } from "react";

/** An ECharts canvas that follows its container's size and the colour scheme. */
export function Chart({ option, height = 360 }: { option: object; height?: number }) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    const instance = echarts.init(host.current!, dark ? "dark" : undefined, { renderer: "canvas" });
    chart.current = instance;
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(host.current!);
    return () => {
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);
  useEffect(() => {
    chart.current?.setOption({ backgroundColor: "transparent", ...option }, true);
  }, [option]);
  return <div ref={host} className="chart" style={{ height }} data-testid="chart" />;
}
