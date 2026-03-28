"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";

interface PricePoint {
  time: string;
  price: number;
}

interface PriceChartProps {
  data: PricePoint[];
  height?: number;
  referenceLine?: number;
  color?: string;
}

export function PriceChart({
  data,
  height = 200,
  referenceLine,
  color = "#14b8a6",
}: PriceChartProps) {
  if (!data.length) {
    return (
      <div
        className="flex items-center justify-center text-muted-foreground text-sm"
        style={{ height }}
      >
        No price data available
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data}>
        <XAxis
          dataKey="time"
          tick={{ fontSize: 10, fill: "#6b7280" }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          domain={["dataMin - 0.02", "dataMax + 0.02"]}
          tick={{ fontSize: 10, fill: "#6b7280" }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v: number) => v.toFixed(2)}
        />
        <Tooltip
          contentStyle={{
            background: "#1a1a2e",
            border: "1px solid #2a2a4e",
            borderRadius: "8px",
            fontSize: 12,
          }}
          formatter={(v: number) => [v.toFixed(4), "Price"]}
        />
        {referenceLine !== undefined && (
          <ReferenceLine
            y={referenceLine}
            stroke="#f59e0b"
            strokeDasharray="3 3"
            label={{
              value: `Model: ${referenceLine.toFixed(3)}`,
              fill: "#f59e0b",
              fontSize: 10,
            }}
          />
        )}
        <Line
          type="monotone"
          dataKey="price"
          stroke={color}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 3 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

interface DepthChartProps {
  bids: { price: number; cumSize: number }[];
  asks: { price: number; cumSize: number }[];
  height?: number;
}

export function DepthChart({ bids, asks, height = 180 }: DepthChartProps) {
  const combined = [
    ...bids.map((b) => ({ price: b.price, bidDepth: b.cumSize, askDepth: 0 })),
    ...asks.map((a) => ({ price: a.price, bidDepth: 0, askDepth: a.cumSize })),
  ].sort((a, b) => a.price - b.price);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={combined}>
        <XAxis
          dataKey="price"
          tick={{ fontSize: 10, fill: "#6b7280" }}
          axisLine={false}
          tickFormatter={(v: number) => v.toFixed(2)}
        />
        <YAxis
          tick={{ fontSize: 10, fill: "#6b7280" }}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip
          contentStyle={{
            background: "#1a1a2e",
            border: "1px solid #2a2a4e",
            borderRadius: "8px",
            fontSize: 12,
          }}
        />
        <Line
          type="stepAfter"
          dataKey="bidDepth"
          stroke="#14b8a6"
          strokeWidth={2}
          dot={false}
          name="Bids"
        />
        <Line
          type="stepAfter"
          dataKey="askDepth"
          stroke="#f97316"
          strokeWidth={2}
          dot={false}
          name="Asks"
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
