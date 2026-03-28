"use client";

import { useState, useCallback } from "react";
import { Responsive, WidthProvider, Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

import { useMarkets, usePortfolioStats } from "@/hooks/useSignals";
import { useWebSocket } from "@/hooks/useWebSocket";

import WorldMap from "@/components/panels/WorldMap";
import EntropyHeatmap from "@/components/panels/EntropyHeatmap";
import MarketDetail from "@/components/panels/MarketDetail";
import OrderBook from "@/components/panels/OrderBook";
import JetTracker from "@/components/panels/JetTracker";
import WhaleTracker from "@/components/panels/WhaleTracker";
import CopyTrade from "@/components/panels/CopyTrade";
import AIDebateFloor from "@/components/panels/AIDebateFloor";
import VolumeSpikes from "@/components/panels/VolumeSpikes";
import MarketMaker from "@/components/panels/MarketMaker";
import EquityCurve from "@/components/panels/EquityCurve";
import KellyCalc from "@/components/panels/KellyCalc";
import Portfolio from "@/components/panels/Portfolio";

const ResponsiveGridLayout = WidthProvider(Responsive);

const DEFAULT_LAYOUTS: Record<string, Layout[]> = {
  lg: [
    // Row 1 (h=4): World Map | Entropy Heatmap
    { i: "world-map", x: 0, y: 0, w: 6, h: 4, minW: 3, minH: 3 },
    { i: "entropy-heatmap", x: 6, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
    // Row 2 (h=3): Market Detail | Order Book | Jet Tracker
    { i: "market-detail", x: 0, y: 4, w: 4, h: 4, minW: 3, minH: 3 },
    { i: "order-book", x: 4, y: 4, w: 4, h: 4, minW: 3, minH: 3 },
    { i: "jet-tracker", x: 8, y: 4, w: 4, h: 4, minW: 3, minH: 3 },
    // Row 3 (h=3): Whale | Copy Trade | AI Debate
    { i: "whale-tracker", x: 0, y: 8, w: 3, h: 4, minW: 2, minH: 3 },
    { i: "copy-trade", x: 3, y: 8, w: 3, h: 4, minW: 2, minH: 3 },
    { i: "ai-debate", x: 6, y: 8, w: 6, h: 4, minW: 4, minH: 3 },
    // Row 4 (h=2): Volume | Market Maker | Equity Curve
    { i: "volume-spikes", x: 0, y: 12, w: 3, h: 3, minW: 2, minH: 2 },
    { i: "market-maker", x: 3, y: 12, w: 3, h: 3, minW: 2, minH: 2 },
    { i: "equity-curve", x: 6, y: 12, w: 6, h: 3, minW: 4, minH: 2 },
    // Row 5 (h=2): Kelly | Portfolio
    { i: "kelly-calc", x: 0, y: 15, w: 4, h: 4, minW: 3, minH: 3 },
    { i: "portfolio", x: 4, y: 15, w: 8, h: 4, minW: 4, minH: 3 },
  ],
  md: [
    { i: "world-map", x: 0, y: 0, w: 5, h: 4 },
    { i: "entropy-heatmap", x: 5, y: 0, w: 5, h: 4 },
    { i: "market-detail", x: 0, y: 4, w: 5, h: 4 },
    { i: "order-book", x: 5, y: 4, w: 5, h: 4 },
    { i: "jet-tracker", x: 0, y: 8, w: 5, h: 4 },
    { i: "whale-tracker", x: 5, y: 8, w: 5, h: 4 },
    { i: "copy-trade", x: 0, y: 12, w: 5, h: 4 },
    { i: "ai-debate", x: 5, y: 12, w: 5, h: 4 },
    { i: "volume-spikes", x: 0, y: 16, w: 5, h: 3 },
    { i: "market-maker", x: 5, y: 16, w: 5, h: 3 },
    { i: "equity-curve", x: 0, y: 19, w: 10, h: 3 },
    { i: "kelly-calc", x: 0, y: 22, w: 5, h: 4 },
    { i: "portfolio", x: 5, y: 22, w: 5, h: 4 },
  ],
};

const PANEL_MAP: Record<string, React.FC> = {
  "world-map": WorldMap,
  "entropy-heatmap": EntropyHeatmap,
  "market-detail": MarketDetail,
  "order-book": OrderBook,
  "jet-tracker": JetTracker,
  "whale-tracker": WhaleTracker,
  "copy-trade": CopyTrade,
  "ai-debate": AIDebateFloor,
  "volume-spikes": VolumeSpikes,
  "market-maker": MarketMaker,
  "equity-curve": EquityCurve,
  "kelly-calc": KellyCalc,
  portfolio: Portfolio,
};

export default function DashboardPage() {
  const [layouts, setLayouts] = useState(DEFAULT_LAYOUTS);

  // Data hooks — fetch on mount and auto-refresh
  useMarkets(50);
  usePortfolioStats();
  useWebSocket();

  const handleLayoutChange = useCallback(
    (_: Layout[], allLayouts: Record<string, Layout[]>) => {
      setLayouts(allLayouts);
    },
    []
  );

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b border-border bg-card/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="flex items-center justify-between px-4 py-2">
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-bold tracking-tight">
              POLYMARKET INTELLIGENCE
            </h1>
            <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-poly-amber/20 text-poly-amber border border-poly-amber/40">
              PAPER MODE
            </span>
          </div>
          <div className="flex items-center gap-4 text-[10px] text-muted-foreground">
            <span>
              <span className="w-2 h-2 inline-block rounded-full bg-poly-green animate-pulse-glow mr-1" />
              7 strategies active
            </span>
            <span>13 panels</span>
            <span className="font-mono">v1.0.0</span>
          </div>
        </div>
      </header>

      {/* Dashboard grid */}
      <main className="p-2">
        <ResponsiveGridLayout
          className="layout"
          layouts={layouts}
          breakpoints={{ lg: 1200, md: 768 }}
          cols={{ lg: 12, md: 10 }}
          rowHeight={60}
          onLayoutChange={handleLayoutChange}
          draggableHandle=".react-grid-item"
          isResizable={true}
          isDraggable={true}
          compactType="vertical"
          margin={[8, 8]}
        >
          {Object.entries(PANEL_MAP).map(([key, Component]) => (
            <div key={key}>
              <Component />
            </div>
          ))}
        </ResponsiveGridLayout>
      </main>
    </div>
  );
}
