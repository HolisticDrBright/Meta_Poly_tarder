"use client";

import { useMemo } from "react";
import { Colors } from "@/lib/rork-types";

function getGaugeColor(score: number): string {
  if (score >= 70) return Colors.green;
  if (score >= 40) return Colors.amber;
  return Colors.coral;
}

export default function RadialGauge({ score, size = 44 }: { score: number; size?: number }) {
  const strokeWidth = 3;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const color = getGaugeColor(score);
  const offset = useMemo(() => {
    const p = Math.min(Math.max(score, 0), 100) / 100;
    return circumference * (1 - p);
  }, [score, circumference]);

  return (
    <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={radius} stroke="rgba(255,255,255,0.06)" strokeWidth={strokeWidth} fill="none" />
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          stroke={color} strokeWidth={strokeWidth} fill="none"
          strokeDasharray={circumference} strokeDashoffset={offset}
          strokeLinecap="round"
          transform={`rotate(-90, ${size / 2}, ${size / 2})`}
        />
      </svg>
      <span className="absolute font-mono font-bold" style={{ color, fontSize: size > 40 ? 13 : 10 }}>
        {score}
      </span>
    </div>
  );
}
