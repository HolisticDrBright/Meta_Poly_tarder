"use client";

import { useState } from "react";
import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { usePortfolioStore, type MMStatus } from "@/stores/portfolioStore";
import { cn, formatUSD } from "@/lib/utils";

export default function MarketMaker() {
  const mmStatuses = usePortfolioStore((s) => s.mmStatuses);

  const demo: MMStatus[] = mmStatuses.length
    ? mmStatuses
    : [
        {
          market_id: "1", question: "Will Fed cut rates?", status: "active",
          reservation_price: 0.348, bid: 0.335, ask: 0.362, spread_bps: 27,
          inventory: 3.2, gamma: 0.1, pnl: 12.50, rewards_today: 0.85,
        },
        {
          market_id: "2", question: "Will BTC exceed $100k?", status: "active",
          reservation_price: 0.418, bid: 0.408, ask: 0.430, spread_bps: 22,
          inventory: -1.5, gamma: 0.1, pnl: 8.30, rewards_today: 0.62,
        },
        {
          market_id: "3", question: "Will SpaceX reach orbit?", status: "paused",
          reservation_price: 0.275, bid: 0, ask: 0, spread_bps: 0,
          inventory: 0, gamma: 0.1, pnl: -2.10, rewards_today: 0,
        },
      ];

  const statusColors = {
    active: "bg-poly-green/20 text-poly-green border-poly-green/40",
    paused: "bg-poly-amber/20 text-poly-amber border-poly-amber/40",
    stopped: "bg-poly-red/20 text-poly-red border-poly-red/40",
  };

  const totalPnl = demo.reduce((s, m) => s + m.pnl, 0);
  const totalRewards = demo.reduce((s, m) => s + m.rewards_today, 0);

  return (
    <PanelCard>
      <PanelHeader title="A-S MARKET MAKER" subtitle="Avellaneda-Stoikov" refreshInterval={10} status="live" />

      {/* Summary */}
      <div className="flex gap-3 mb-2 text-[10px]">
        <div>
          <span className="text-muted-foreground">MM PnL: </span>
          <span className={cn("font-bold", totalPnl >= 0 ? "text-poly-green" : "text-poly-red")}>
            {totalPnl >= 0 ? "+" : ""}{formatUSD(totalPnl)}
          </span>
        </div>
        <div>
          <span className="text-muted-foreground">Rewards: </span>
          <span className="font-bold text-poly-amber">{formatUSD(totalRewards)}</span>
        </div>
        <div>
          <span className="text-muted-foreground">Active: </span>
          <span className="font-bold">{demo.filter((m) => m.status === "active").length}</span>
        </div>
      </div>

      {/* Per-market status */}
      <div className="space-y-1 overflow-auto max-h-[calc(100%-70px)]">
        {demo.map((m, i) => (
          <div key={i} className="p-2 rounded bg-muted/20 border border-border/30">
            <div className="flex items-center gap-2 mb-1 text-[10px]">
              <span
                className={cn(
                  "px-1.5 py-0.5 rounded text-[9px] font-medium border",
                  statusColors[m.status]
                )}
              >
                {m.status.toUpperCase()}
              </span>
              <span className="truncate flex-1 font-medium">{m.question}</span>
            </div>
            {m.status === "active" && (
              <div className="grid grid-cols-4 gap-1 text-[9px]">
                <div>
                  <span className="text-muted-foreground block">Reserve</span>
                  <span>{m.reservation_price.toFixed(4)}</span>
                </div>
                <div>
                  <span className="text-muted-foreground block">Bid/Ask</span>
                  <span className="text-poly-teal">{m.bid.toFixed(3)}</span>
                  <span className="text-muted-foreground">/</span>
                  <span className="text-poly-coral">{m.ask.toFixed(3)}</span>
                </div>
                <div>
                  <span className="text-muted-foreground block">Spread</span>
                  <span>{m.spread_bps}bps</span>
                </div>
                <div>
                  <span className="text-muted-foreground block">Inv</span>
                  <span className={cn(m.inventory > 0 ? "text-poly-green" : m.inventory < 0 ? "text-poly-red" : "")}>
                    {m.inventory > 0 ? "+" : ""}{m.inventory.toFixed(1)}
                  </span>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </PanelCard>
  );
}
