"use client";

import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from "recharts";
import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { usePortfolioStore } from "@/stores/portfolioStore";
import { cn, formatUSD } from "@/lib/utils";
import { useMemo } from "react";

export default function EquityCurve() {
  const stats = usePortfolioStore((s) => s.stats);

  // Demo equity curve
  const equityData = useMemo(() => {
    const data = [];
    let balance = 10000;
    for (let i = 30; i >= 0; i--) {
      balance += (Math.random() - 0.45) * 100;
      data.push({
        day: `D-${i}`,
        balance: Math.round(balance * 100) / 100,
        unrealized: (Math.random() - 0.5) * 200,
      });
    }
    return data;
  }, []);

  // Demo daily PnL
  const dailyPnl = useMemo(() => {
    return Array.from({ length: 30 }, (_, i) => ({
      day: `D-${30 - i}`,
      pnl: (Math.random() - 0.45) * 150,
    }));
  }, []);

  return (
    <PanelCard>
      <PanelHeader title="EQUITY CURVE" subtitle="Cumulative PnL" refreshInterval={60} status="live" />

      {/* Stats row */}
      <div className="grid grid-cols-4 gap-1 mb-2 text-[10px]">
        <div className="text-center p-1 rounded bg-muted/30">
          <div className="text-muted-foreground">Balance</div>
          <div className="font-bold">{formatUSD(stats.balance)}</div>
        </div>
        <div className="text-center p-1 rounded bg-muted/30">
          <div className="text-muted-foreground">Win Rate</div>
          <div className="font-bold">{(stats.win_rate * 100).toFixed(1)}%</div>
        </div>
        <div className="text-center p-1 rounded bg-muted/30">
          <div className="text-muted-foreground">Sharpe</div>
          <div className="font-bold">{stats.sharpe_ratio.toFixed(2)}</div>
        </div>
        <div className="text-center p-1 rounded bg-muted/30">
          <div className="text-muted-foreground">Max DD</div>
          <div className="font-bold text-poly-red">{(stats.max_drawdown * 100).toFixed(1)}%</div>
        </div>
      </div>

      {/* Equity line chart */}
      <ResponsiveContainer width="100%" height={100}>
        <LineChart data={equityData}>
          <XAxis dataKey="day" tick={false} axisLine={false} />
          <YAxis tick={{ fontSize: 9, fill: "#6b7280" }} axisLine={false} tickLine={false} tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`} />
          <Tooltip contentStyle={{ background: "#1a1a2e", border: "1px solid #2a2a4e", borderRadius: "8px", fontSize: 11 }} formatter={(v: number) => [formatUSD(v)]} />
          <Line type="monotone" dataKey="balance" stroke="#14b8a6" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>

      {/* Daily PnL bar chart */}
      <div className="mt-1 text-[10px] text-muted-foreground mb-0.5">Daily P&L (30d)</div>
      <ResponsiveContainer width="100%" height={60}>
        <BarChart data={dailyPnl}>
          <XAxis dataKey="day" tick={false} axisLine={false} />
          <YAxis tick={false} axisLine={false} />
          <Tooltip contentStyle={{ background: "#1a1a2e", border: "1px solid #2a2a4e", borderRadius: "8px", fontSize: 11 }} formatter={(v: number) => [formatUSD(v), "PnL"]} />
          <Bar dataKey="pnl" fill="#14b8a6" radius={[2, 2, 0, 0]}>
            {dailyPnl.map((entry, index) => (
              <rect key={index} fill={entry.pnl >= 0 ? "#22c55e" : "#ef4444"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </PanelCard>
  );
}
