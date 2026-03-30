"use client";

import { useState, useCallback, Component, type ReactNode } from "react";
import { Responsive, WidthProvider, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

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

// ── Error Boundary — catches any panel crash without killing the page ──
interface EBProps { children: ReactNode; name: string }
interface EBState { hasError: boolean; error?: Error }

class PanelErrorBoundary extends Component<EBProps, EBState> {
  constructor(props: EBProps) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }
  componentDidCatch(error: Error) {
    console.error(`[Panel ${this.props.name}] crashed:`, error);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="h-full bg-card rounded-lg border border-border p-3 flex items-center justify-center">
          <div className="text-center text-xs text-muted-foreground">
            <div className="font-bold text-poly-red mb-1">{this.props.name}</div>
            <div>Panel crashed — {this.state.error?.message?.slice(0, 80)}</div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// ── Grid layout — only runs client-side (imported via next/dynamic ssr:false) ──

const ResponsiveGridLayout = WidthProvider(Responsive);

const DEFAULT_LAYOUTS: Record<string, Layout[]> = {
  lg: [
    { i: "world-map", x: 0, y: 0, w: 6, h: 4, minW: 3, minH: 3 },
    { i: "entropy-heatmap", x: 6, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
    { i: "market-detail", x: 0, y: 4, w: 4, h: 4, minW: 3, minH: 3 },
    { i: "order-book", x: 4, y: 4, w: 4, h: 4, minW: 3, minH: 3 },
    { i: "jet-tracker", x: 8, y: 4, w: 4, h: 4, minW: 3, minH: 3 },
    { i: "whale-tracker", x: 0, y: 8, w: 3, h: 4, minW: 2, minH: 3 },
    { i: "copy-trade", x: 3, y: 8, w: 3, h: 4, minW: 2, minH: 3 },
    { i: "ai-debate", x: 6, y: 8, w: 6, h: 4, minW: 4, minH: 3 },
    { i: "volume-spikes", x: 0, y: 12, w: 3, h: 3, minW: 2, minH: 2 },
    { i: "market-maker", x: 3, y: 12, w: 3, h: 3, minW: 2, minH: 2 },
    { i: "equity-curve", x: 6, y: 12, w: 6, h: 3, minW: 4, minH: 2 },
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

const PANELS: { key: string; Component: React.FC }[] = [
  { key: "world-map", Component: WorldMap },
  { key: "entropy-heatmap", Component: EntropyHeatmap },
  { key: "market-detail", Component: MarketDetail },
  { key: "order-book", Component: OrderBook },
  { key: "jet-tracker", Component: JetTracker },
  { key: "whale-tracker", Component: WhaleTracker },
  { key: "copy-trade", Component: CopyTrade },
  { key: "ai-debate", Component: AIDebateFloor },
  { key: "volume-spikes", Component: VolumeSpikes },
  { key: "market-maker", Component: MarketMaker },
  { key: "equity-curve", Component: EquityCurve },
  { key: "kelly-calc", Component: KellyCalc },
  { key: "portfolio", Component: Portfolio },
];

export default function DashboardGrid() {
  const [layouts, setLayouts] = useState(DEFAULT_LAYOUTS);

  const handleLayoutChange = useCallback(
    (_: Layout[], allLayouts: Record<string, Layout[]>) => {
      setLayouts(allLayouts);
    },
    []
  );

  return (
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
      {PANELS.map(({ key, Component }) => (
        <div key={key}>
          <PanelErrorBoundary name={key}>
            <Component />
          </PanelErrorBoundary>
        </div>
      ))}
    </ResponsiveGridLayout>
  );
}
