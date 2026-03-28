"use client";

import { useState, useMemo } from "react";
import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { PriceChart } from "../shared/PriceChart";
import { SideBadge, EdgeBadge } from "../shared/SignalBadge";
import { useMarketStore } from "@/stores/marketStore";
import { cn, formatPct, formatUSD, hoursUntil } from "@/lib/utils";

const TIMEFRAMES = ["24H", "7D", "1M", "ALL"] as const;

export default function MarketDetail() {
  const market = useMarketStore((s) => s.selectedMarket);
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>("7D");

  // Generate synthetic price history for demo
  const chartData = useMemo(() => {
    if (!market) return [];
    const points = timeframe === "24H" ? 24 : timeframe === "7D" ? 168 : timeframe === "1M" ? 720 : 2000;
    const data = [];
    let price = market.yes_price;
    for (let i = points; i >= 0; i--) {
      price += (Math.random() - 0.5) * 0.01;
      price = Math.max(0.01, Math.min(0.99, price));
      data.push({
        time: `${i}`,
        price: i === 0 ? market.yes_price : price,
      });
    }
    return data;
  }, [market, timeframe]);

  if (!market) {
    return (
      <PanelCard>
        <PanelHeader title="MARKET DETAIL" refreshInterval={10} />
        <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
          Click a market to view details
        </div>
      </PanelCard>
    );
  }

  const arbEdge = 1 - market.yes_price - market.no_price;
  const hours = hoursUntil(market.end_date);

  return (
    <PanelCard>
      <PanelHeader title="MARKET DETAIL" refreshInterval={10} status="live" />
      <p className="text-xs font-medium mb-2 leading-tight">{market.question}</p>

      {/* Timeframe selector */}
      <div className="flex gap-1 mb-2">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            onClick={() => setTimeframe(tf)}
            className={cn(
              "px-2 py-0.5 rounded text-[10px] font-medium border",
              timeframe === tf
                ? "bg-accent text-accent-foreground border-poly-blue"
                : "bg-muted/30 text-muted-foreground border-border"
            )}
          >
            {tf}
          </button>
        ))}
      </div>

      {/* Price chart */}
      <PriceChart data={chartData} height={120} />

      {/* Price stats */}
      <div className="grid grid-cols-4 gap-2 mt-2 text-[10px]">
        <div>
          <span className="text-muted-foreground block">YES</span>
          <span className="font-bold text-poly-green">{formatPct(market.yes_price, 1)}</span>
        </div>
        <div>
          <span className="text-muted-foreground block">NO</span>
          <span className="font-bold text-poly-red">{formatPct(market.no_price, 1)}</span>
        </div>
        <div>
          <span className="text-muted-foreground block">Spread</span>
          <span className="font-medium">{(market.spread * 100).toFixed(1)}&cent;</span>
        </div>
        <div>
          <span className="text-muted-foreground block">Liquidity</span>
          <span className="font-medium">{formatUSD(market.liquidity)}</span>
        </div>
      </div>

      {/* Arb indicator */}
      {arbEdge > 0.01 && (
        <div className="mt-2 p-1.5 rounded bg-poly-green/10 border border-poly-green/30 text-[10px] text-poly-green font-bold animate-pulse-glow">
          ARB OPPORTUNITY: {(arbEdge * 100).toFixed(1)}&cent; gap
        </div>
      )}

      {/* Entropy stats */}
      <div className="grid grid-cols-3 gap-2 mt-2 text-[10px]">
        <div>
          <span className="text-muted-foreground block">H(p)</span>
          <span>{market.entropy_bits?.toFixed(3) || "—"} bits</span>
        </div>
        <div>
          <span className="text-muted-foreground block">Volume 24h</span>
          <span>{formatUSD(market.volume_24h)}</span>
        </div>
        <div>
          <span className="text-muted-foreground block">Closes</span>
          <span className={cn(hours !== null && hours < 24 && "text-poly-red")}>
            {hours !== null ? `${hours.toFixed(0)}h` : "—"}
          </span>
        </div>
      </div>
    </PanelCard>
  );
}
