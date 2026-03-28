"use client";

import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { DepthChart } from "../shared/PriceChart";
import { useMarketStore } from "@/stores/marketStore";
import { cn } from "@/lib/utils";
import { useMemo } from "react";

export default function OrderBook() {
  const market = useMarketStore((s) => s.selectedMarket);
  const orderBook = useMarketStore((s) => s.orderBook);

  // Generate demo order book data if none from WS
  const { bids, asks, ofi, vpinVal } = useMemo(() => {
    if (!market) return { bids: [], asks: [], ofi: 0, vpinVal: 0 };

    const mid = market.yes_price;
    const demoLevels = 10;
    const bids = Array.from({ length: demoLevels }, (_, i) => ({
      price: Math.max(0.01, mid - (i + 1) * 0.005),
      size: 50 + Math.random() * 200,
    }));
    const asks = Array.from({ length: demoLevels }, (_, i) => ({
      price: Math.min(0.99, mid + (i + 1) * 0.005),
      size: 50 + Math.random() * 200,
    }));

    const bidTotal = bids.reduce((s, b) => s + b.size, 0);
    const askTotal = asks.reduce((s, a) => s + a.size, 0);
    const ofi = (bidTotal - askTotal) / (bidTotal + askTotal);
    const vpinVal = Math.random() * 0.4 + 0.2;

    return { bids, asks, ofi, vpinVal };
  }, [market]);

  const depthBids = useMemo(() => {
    let cum = 0;
    return bids.map((b) => {
      cum += b.size;
      return { price: b.price, cumSize: cum };
    });
  }, [bids]);

  const depthAsks = useMemo(() => {
    let cum = 0;
    return asks.map((a) => {
      cum += a.size;
      return { price: a.price, cumSize: cum };
    });
  }, [asks]);

  if (!market) {
    return (
      <PanelCard>
        <PanelHeader title="ORDER BOOK" refreshInterval={5} />
        <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
          Select a market
        </div>
      </PanelCard>
    );
  }

  const maxSize = Math.max(...bids.map((b) => b.size), ...asks.map((a) => a.size), 1);

  return (
    <PanelCard>
      <PanelHeader title="ORDER BOOK" subtitle={`L2 Depth`} refreshInterval={5} status="live" />

      {/* Depth chart */}
      <DepthChart bids={depthBids} asks={depthAsks} height={100} />

      {/* OFI + VPIN gauges */}
      <div className="flex gap-4 my-2 text-[10px]">
        <div className="flex-1">
          <span className="text-muted-foreground">OFI</span>
          <div className="flex items-center gap-1 mt-0.5">
            <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
              <div
                className={cn("h-full rounded-full", ofi > 0 ? "bg-poly-teal" : "bg-poly-coral")}
                style={{
                  width: `${Math.abs(ofi) * 50 + 50}%`,
                  marginLeft: ofi < 0 ? `${50 + ofi * 50}%` : "50%",
                }}
              />
            </div>
            <span className="font-medium w-10 text-right">{ofi.toFixed(2)}</span>
          </div>
        </div>
        <div className="flex-1">
          <span className="text-muted-foreground">VPIN</span>
          <div className="flex items-center gap-1 mt-0.5">
            <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
              <div
                className={cn(
                  "h-full rounded-full",
                  vpinVal > 0.7 ? "bg-poly-red" : vpinVal > 0.4 ? "bg-poly-amber" : "bg-poly-green"
                )}
                style={{ width: `${vpinVal * 100}%` }}
              />
            </div>
            <span className="font-medium w-10 text-right">{vpinVal.toFixed(2)}</span>
          </div>
        </div>
      </div>

      {/* Book levels */}
      <div className="grid grid-cols-2 gap-1 text-[10px] max-h-32 overflow-y-auto">
        <div>
          <div className="text-muted-foreground mb-0.5 font-medium">BIDS</div>
          {bids.map((b, i) => (
            <div key={i} className="flex justify-between relative">
              <div
                className="absolute inset-y-0 left-0 bg-poly-teal/10 rounded-sm"
                style={{ width: `${(b.size / maxSize) * 100}%` }}
              />
              <span className="text-poly-teal relative">{b.price.toFixed(3)}</span>
              <span className="text-muted-foreground relative">{b.size.toFixed(0)}</span>
            </div>
          ))}
        </div>
        <div>
          <div className="text-muted-foreground mb-0.5 font-medium">ASKS</div>
          {asks.map((a, i) => (
            <div key={i} className="flex justify-between relative">
              <div
                className="absolute inset-y-0 right-0 bg-poly-coral/10 rounded-sm"
                style={{ width: `${(a.size / maxSize) * 100}%` }}
              />
              <span className="text-poly-coral relative">{a.price.toFixed(3)}</span>
              <span className="text-muted-foreground relative">{a.size.toFixed(0)}</span>
            </div>
          ))}
        </div>
      </div>
    </PanelCard>
  );
}
