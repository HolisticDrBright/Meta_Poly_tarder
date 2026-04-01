"use client";

import { useMemo } from "react";

export default function Sparkline({
  data,
  width = 80,
  height = 32,
  color = "#00E5FF",
}: {
  data: { value: number }[];
  width?: number;
  height?: number;
  color?: string;
}) {
  const points = useMemo(() => {
    if (data.length < 2) return "";
    const min = Math.min(...data.map((d) => d.value));
    const max = Math.max(...data.map((d) => d.value));
    const range = max - min || 1;
    const stepX = width / (data.length - 1);
    return data.map((d, i) => `${i * stepX},${height - ((d.value - min) / range) * (height - 4) - 2}`).join(" ");
  }, [data, width, height]);

  const id = useMemo(() => `glow-${Math.random().toString(36).slice(2)}`, []);

  return (
    <div style={{ width, height, overflow: "hidden" }}>
      <svg width={width} height={height}>
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={color} stopOpacity="0.15" />
            <stop offset="1" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <rect x="0" y="0" width={width} height={height} fill={`url(#${id})`} rx="4" />
        <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      </svg>
    </div>
  );
}
