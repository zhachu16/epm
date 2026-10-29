/** A tiny inline chart of a series; missing values break the line. */
export function Sparkline({ values, width = 240, height = 48 }: { values: (number | null)[]; width?: number; height?: number }) {
  const finite = values.filter((v): v is number => v !== null && Number.isFinite(v));
  if (finite.length < 2) return null;
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const span = max - min || 1;
  const step = width / Math.max(values.length - 1, 1);
  const segments: string[] = [];
  let current = "";
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) {
      if (current) segments.push(current);
      current = "";
      return;
    }
    const x = i * step;
    const y = height - 4 - ((v - min) / span) * (height - 8);
    current += `${current ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  });
  if (current) segments.push(current);
  return (
    <svg width={width} height={height} style={{ display: "block", overflow: "visible" }} aria-hidden>
      {segments.map((d, i) => (
        <path key={i} d={d} fill="none" stroke="var(--accent)" strokeWidth={1.6} strokeLinejoin="round" />
      ))}
    </svg>
  );
}
