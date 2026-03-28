"use client";

import { useEffect, useRef, useState } from "react";
import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { useMarketStore, type Market } from "@/stores/marketStore";

const CATEGORY_COLORS: Record<string, string> = {
  politics: "#3b82f6",
  sports: "#22c55e",
  crypto: "#f59e0b",
  pharma: "#f97316",
  science: "#a855f7",
  finance: "#14b8a6",
  default: "#6b7280",
};

// Rough category → coordinate mapping for demo visualization
function marketToCoords(market: Market): { lng: number; lat: number } | null {
  const q = market.question.toLowerCase();
  if (q.includes("us ") || q.includes("fed") || q.includes("trump") || q.includes("congress"))
    return { lng: -98 + Math.random() * 20, lat: 38 + Math.random() * 8 };
  if (q.includes("uk") || q.includes("brexit"))
    return { lng: -1 + Math.random() * 3, lat: 52 + Math.random() * 3 };
  if (q.includes("china") || q.includes("taiwan"))
    return { lng: 116 + Math.random() * 10, lat: 30 + Math.random() * 10 };
  if (q.includes("ukraine") || q.includes("russia"))
    return { lng: 32 + Math.random() * 10, lat: 48 + Math.random() * 5 };
  if (q.includes("btc") || q.includes("eth") || q.includes("crypto") || q.includes("doge"))
    return { lng: -122 + Math.random() * 5, lat: 37 + Math.random() * 3 };
  // Random world placement
  return { lng: -180 + Math.random() * 360, lat: -60 + Math.random() * 120 };
}

function getCategoryColor(market: Market): string {
  const q = market.question.toLowerCase();
  const cat = market.category?.toLowerCase() || "";
  if (cat.includes("politic") || q.includes("trump") || q.includes("election"))
    return CATEGORY_COLORS.politics;
  if (cat.includes("sport")) return CATEGORY_COLORS.sports;
  if (cat.includes("crypto") || q.includes("btc") || q.includes("eth"))
    return CATEGORY_COLORS.crypto;
  if (cat.includes("pharma") || q.includes("fda") || q.includes("drug"))
    return CATEGORY_COLORS.pharma;
  if (cat.includes("science") || q.includes("ai ") || q.includes("fusion"))
    return CATEGORY_COLORS.science;
  return CATEGORY_COLORS.default;
}

interface MapDot {
  market: Market;
  lng: number;
  lat: number;
  color: string;
}

export default function WorldMap() {
  const markets = useMarketStore((s) => s.markets);
  const selectMarket = useMarketStore((s) => s.selectMarket);
  const [dots, setDots] = useState<MapDot[]>([]);
  const [filter, setFilter] = useState<string>("all");

  useEffect(() => {
    const mapped = markets
      .map((m) => {
        const coords = marketToCoords(m);
        if (!coords) return null;
        return { market: m, ...coords, color: getCategoryColor(m) };
      })
      .filter(Boolean) as MapDot[];
    setDots(mapped);
  }, [markets]);

  const filtered =
    filter === "all"
      ? dots
      : dots.filter((d) => {
          if (filter === "politics") return d.color === CATEGORY_COLORS.politics;
          if (filter === "crypto") return d.color === CATEGORY_COLORS.crypto;
          if (filter === "pharma") return d.color === CATEGORY_COLORS.pharma;
          if (filter === "sports") return d.color === CATEGORY_COLORS.sports;
          return true;
        });

  // Simple SVG world map projection (Mercator-like)
  const project = (lng: number, lat: number) => ({
    x: ((lng + 180) / 360) * 100,
    y: ((90 - lat) / 180) * 100,
  });

  return (
    <PanelCard>
      <PanelHeader title="WORLD MAP" subtitle="Markets by geography" refreshInterval={45} status="live" />
      <div className="flex gap-1 mb-2 flex-wrap">
        {["all", "politics", "crypto", "pharma", "sports"].map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-2 py-0.5 rounded text-[10px] font-medium border ${
              filter === f
                ? "bg-accent text-accent-foreground border-poly-blue"
                : "bg-muted/30 text-muted-foreground border-border hover:bg-muted/50"
            }`}
          >
            {f.toUpperCase()}
          </button>
        ))}
      </div>
      <div className="relative w-full aspect-[2/1] bg-muted/20 rounded overflow-hidden border border-border">
        {/* Simple world outline */}
        <svg viewBox="0 0 100 100" className="w-full h-full" preserveAspectRatio="none">
          {/* Grid lines */}
          {[20, 40, 60, 80].map((x) => (
            <line key={`v${x}`} x1={x} y1={0} x2={x} y2={100} stroke="#1e293b" strokeWidth="0.2" />
          ))}
          {[20, 40, 60, 80].map((y) => (
            <line key={`h${y}`} x1={0} y1={y} x2={100} y2={y} stroke="#1e293b" strokeWidth="0.2" />
          ))}
          {/* Market dots */}
          {filtered.map((dot, i) => {
            const { x, y } = project(dot.lng, dot.lat);
            const size = Math.max(0.5, Math.min(2, dot.market.liquidity / 100000));
            return (
              <g key={i} onClick={() => selectMarket(dot.market.id)} className="cursor-pointer">
                <circle cx={x} cy={y} r={size + 0.5} fill={dot.color} opacity={0.3} />
                <circle cx={x} cy={y} r={size * 0.6} fill={dot.color} opacity={0.9} />
                <title>
                  {dot.market.question} ({(dot.market.yes_price * 100).toFixed(0)}%)
                </title>
              </g>
            );
          })}
        </svg>
        <div className="absolute bottom-1 right-1 text-[9px] text-muted-foreground">
          {filtered.length} markets
        </div>
      </div>
      <div className="flex gap-3 mt-2 text-[9px] text-muted-foreground">
        <span><span className="inline-block w-2 h-2 rounded-full bg-poly-blue mr-1" />Politics</span>
        <span><span className="inline-block w-2 h-2 rounded-full bg-poly-green mr-1" />Sports</span>
        <span><span className="inline-block w-2 h-2 rounded-full bg-poly-amber mr-1" />Crypto</span>
        <span><span className="inline-block w-2 h-2 rounded-full bg-poly-coral mr-1" />Pharma</span>
      </div>
    </PanelCard>
  );
}
