"use client";

import { useState, useCallback, useEffect, Suspense } from "react";
import dynamic from "next/dynamic";

import { useMarkets, usePortfolioStats, usePositions, useSignals } from "@/hooks/useSignals";
import { usePortfolioStore } from "@/stores/portfolioStore";
import { useMarketStore } from "@/stores/marketStore";

// Dynamic imports — these all access `window` and crash during SSR
const DashboardGrid = dynamic(() => import("@/components/DashboardGrid"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center h-[80vh] text-muted-foreground">
      Loading dashboard panels...
    </div>
  ),
});

export default function DashboardPage() {
  const [mounted, setMounted] = useState(false);
  const stats = usePortfolioStore((s) => s.stats);
  const positions = usePortfolioStore((s) => s.positions);

  // Data hooks — fetch on mount and auto-refresh
  useMarkets(50);
  usePortfolioStats();
  usePositions();
  useSignals();

  // Ensure we're client-side before rendering anything that touches window
  useEffect(() => {
    setMounted(true);
  }, []);

  // Eager hydration — populate stores immediately on mount
  useEffect(() => {
    if (!mounted) return;
    const buildTimeUrl = process.env.NEXT_PUBLIC_API_URL;
    const runtimeUrl = `http://${window.location.hostname}:8000`;
    const API = buildTimeUrl || runtimeUrl;

    console.log("[Hydration] Fetching from:", API);

    Promise.all([
      fetch(`${API}/api/portfolio/stats`, { signal: AbortSignal.timeout(8000) })
        .then((r) => r.ok ? r.json() : null)
        .catch(() => null),
      fetch(`${API}/api/portfolio/positions`, { signal: AbortSignal.timeout(8000) })
        .then((r) => r.ok ? r.json() : null)
        .catch(() => null),
      fetch(`${API}/api/markets?limit=50`, { signal: AbortSignal.timeout(8000) })
        .then((r) => r.ok ? r.json() : null)
        .catch(() => null),
    ]).then(([statsData, posData, marketsData]) => {
      console.log("[Hydration] Results:", {
        stats: !!statsData,
        positions: !!posData?.positions,
        markets: Array.isArray(marketsData) ? marketsData.length : "not array",
      });
      if (statsData) usePortfolioStore.setState({ stats: statsData });
      if (posData?.positions) usePortfolioStore.setState({ positions: posData.positions });
      if (marketsData && Array.isArray(marketsData)) useMarketStore.setState({ markets: marketsData });
    });
  }, [mounted]);

  const totalBalance = stats.balance + (stats.unrealized_pnl || 0);
  const totalROI = stats.balance > 0 ? ((totalBalance - 10000) / 10000) * 100 : 0;
  const activeTradeCount = positions.length;

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b border-border bg-card/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="flex items-center justify-between px-4 py-2">
          {/* Left: brand + mode */}
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-bold tracking-tight">
              POLYMARKET INTELLIGENCE
            </h1>
            <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-poly-amber/20 text-poly-amber border border-poly-amber/40">
              {stats.paper_trading ? "PAPER" : "LIVE"}
            </span>
          </div>

          {/* Center: key stats */}
          <div className="flex items-center gap-6">
            <div className="text-center">
              <div className="text-[9px] text-muted-foreground uppercase tracking-wider">Balance</div>
              <div className="text-sm font-semibold font-mono">
                ${totalBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </div>
            </div>
            <div className="text-center">
              <div className="text-[9px] text-muted-foreground uppercase tracking-wider">ROI</div>
              <div className={`text-sm font-semibold font-mono ${totalROI >= 0 ? "text-poly-green" : "text-poly-red"}`}>
                {totalROI >= 0 ? "+" : ""}{totalROI.toFixed(2)}%
              </div>
            </div>
            <div className="text-center">
              <div className="text-[9px] text-muted-foreground uppercase tracking-wider">Active Trades</div>
              <div className="text-sm font-semibold font-mono">
                {activeTradeCount}
              </div>
            </div>
            <div className="text-center">
              <div className="text-[9px] text-muted-foreground uppercase tracking-wider">Today P&L</div>
              <div className={`text-sm font-semibold font-mono ${(stats.realized_pnl || 0) >= 0 ? "text-poly-green" : "text-poly-red"}`}>
                {(stats.realized_pnl || 0) >= 0 ? "+" : ""}${(stats.realized_pnl || 0).toFixed(2)}
              </div>
            </div>
          </div>

          {/* Right: system status */}
          <div className="flex items-center gap-4 text-[10px] text-muted-foreground">
            <span>
              <span className="w-2 h-2 inline-block rounded-full bg-poly-green animate-pulse-glow mr-1" />
              7 strategies
            </span>
            <span>{activeTradeCount} open</span>
            <span className="font-mono">v1.0.0</span>
          </div>
        </div>
      </header>

      {/* Dashboard grid — only renders client-side */}
      <main className="p-2">
        {mounted ? <DashboardGrid /> : null}
      </main>
    </div>
  );
}
