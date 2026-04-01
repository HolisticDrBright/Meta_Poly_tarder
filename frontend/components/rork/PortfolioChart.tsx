"use client";

import { useMemo } from "react";
import { Colors, type PortfolioGrowthPoint } from "@/lib/rork-types";

export default function PortfolioChart({
  data,
  width = 500,
  height = 160,
}: {
  data: PortfolioGrowthPoint[];
  width?: number;
  height?: number;
}) {
  const pad = { top: 12, bottom: 28, left: 0, right: 0 };
  const cw = width - pad.left - pad.right;
  const ch = height - pad.top - pad.bottom;

  const { points, fillPoints, lastPoint, labels } = useMemo(() => {
    if (data.length < 2) return { points: "", fillPoints: "", lastPoint: { x: 0, y: 0 }, labels: [] as { x: number; label: string }[] };

    const vals = data.map((d) => d.value);
    const mn = Math.min(...vals), mx = Math.max(...vals);
    const r = mx - mn || 1;
    const bMin = mn - r * 0.05, bMax = mx + r * 0.05, bR = bMax - bMin;
    const sx = cw / (data.length - 1);

    const pts = data.map((d, i) => ({
      x: pad.left + i * sx,
      y: pad.top + ch - ((d.value - bMin) / bR) * ch,
    }));

    const poly = pts.map((p) => `${p.x},${p.y}`).join(" ");
    const fill = `${pts[0].x},${pad.top + ch} ${poly} ${pts[pts.length - 1].x},${pad.top + ch}`;
    const li = [0, Math.floor(data.length / 3), Math.floor((2 * data.length) / 3), data.length - 1];

    return {
      points: poly,
      fillPoints: fill,
      lastPoint: pts[pts.length - 1],
      labels: li.map((i) => ({ x: pad.left + i * sx, label: data[i].day })),
    };
  }, [data, cw, ch]);

  const growth = data.length >= 2 ? data[data.length - 1].value - data[0].value : 0;
  const pct = data.length >= 2 ? ((growth / data[0].value) * 100).toFixed(1) : "0";
  const pos = growth >= 0;
  const lc = pos ? Colors.green : Colors.coral;

  return (
    <div className="rounded-xl p-3.5 pb-1.5 mb-3" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
      <div className="flex justify-between items-center mb-2.5">
        <span className="text-[10px] font-bold font-mono tracking-widest" style={{ color: Colors.textTertiary }}>
          PORTFOLIO GROWTH
        </span>
        <div className="flex items-center gap-1.5">
          <span className="text-[13px] font-bold font-mono" style={{ color: lc }}>
            {pos ? "+" : ""}{pct}%
          </span>
          <span
            className="text-[10px] font-semibold font-mono px-1.5 py-0.5 rounded"
            style={{ color: Colors.textTertiary, backgroundColor: "rgba(255,255,255,0.05)" }}
          >
            30D
          </span>
        </div>
      </div>
      <svg width={width} height={height}>
        <defs>
          <linearGradient id="pChartFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={lc} stopOpacity="0.2" />
            <stop offset="0.7" stopColor={lc} stopOpacity="0.05" />
            <stop offset="1" stopColor={lc} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1={pad.left} y1={pad.top + ch * (1 - f)} x2={width - pad.right} y2={pad.top + ch * (1 - f)} stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
        ))}
        <polygon points={fillPoints} fill="url(#pChartFill)" />
        <polyline points={points} fill="none" stroke={lc} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={lastPoint.x} cy={lastPoint.y} r="4" fill={lc} />
        <circle cx={lastPoint.x} cy={lastPoint.y} r="7" fill={lc} opacity={0.2} />
      </svg>
      <div className="relative" style={{ height: 16 }}>
        {labels.map((l, i) => (
          <span
            key={i}
            className="absolute text-[9px] font-mono text-center"
            style={{ color: Colors.textTertiary, left: l.x - 18, width: 36 }}
          >
            {l.label}
          </span>
        ))}
      </div>
    </div>
  );
}
