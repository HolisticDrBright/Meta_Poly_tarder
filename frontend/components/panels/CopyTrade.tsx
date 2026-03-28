"use client";

import { useState } from "react";
import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { SideBadge, ConfluenceBadge } from "../shared/SignalBadge";
import { useSignalStore, type CopyTradeIntent } from "@/stores/signalStore";
import { cn, formatUSD } from "@/lib/utils";

export default function CopyTrade() {
  const copyQueue = useSignalStore((s) => s.copyQueue);
  const [autoTargets, setAutoTargets] = useState<Record<string, boolean>>({ "@RN1": false });

  const demo: CopyTradeIntent[] = copyQueue.length
    ? copyQueue
    : [
        { target_name: "@RN1", market_id: "1", question: "Will Fed cut rates by 50bps?", side: "YES", size_usdc: 250, price: 0.35, confluence_count: 2, status: "pending" },
        { target_name: "AceTrader", market_id: "2", question: "Will BTC exceed $100k?", side: "YES", size_usdc: 180, price: 0.42, confluence_count: 1, status: "pending" },
      ];

  const stats = {
    winRate: 62.5,
    streak: 3,
    roi: 14.2,
    followed: 24,
  };

  return (
    <PanelCard>
      <PanelHeader title="COPY TRADE" subtitle="@RN1 + Leaderboard" refreshInterval={30} status="live" />

      {/* Stats row */}
      <div className="grid grid-cols-4 gap-1 mb-2 text-[10px]">
        <div className="text-center p-1 rounded bg-muted/30">
          <div className="text-muted-foreground">Win Rate</div>
          <div className="font-bold text-poly-green">{stats.winRate}%</div>
        </div>
        <div className="text-center p-1 rounded bg-muted/30">
          <div className="text-muted-foreground">Streak</div>
          <div className="font-bold">{stats.streak}W</div>
        </div>
        <div className="text-center p-1 rounded bg-muted/30">
          <div className="text-muted-foreground">ROI</div>
          <div className="font-bold text-poly-green">+{stats.roi}%</div>
        </div>
        <div className="text-center p-1 rounded bg-muted/30">
          <div className="text-muted-foreground">Trades</div>
          <div className="font-bold">{stats.followed}</div>
        </div>
      </div>

      {/* Auto/Manual toggle */}
      <div className="flex items-center gap-2 mb-2 text-[10px]">
        <span className="text-muted-foreground">@RN1 Mode:</span>
        <button
          onClick={() => setAutoTargets((p) => ({ ...p, "@RN1": !p["@RN1"] }))}
          className={cn(
            "px-2 py-0.5 rounded border text-[10px] font-medium",
            autoTargets["@RN1"]
              ? "bg-poly-green/20 text-poly-green border-poly-green/40"
              : "bg-poly-amber/20 text-poly-amber border-poly-amber/40"
          )}
        >
          {autoTargets["@RN1"] ? "AUTO-COPY" : "MANUAL"}
        </button>
      </div>

      {/* Copy queue */}
      <div className="space-y-1 overflow-auto max-h-[calc(100%-130px)]">
        {demo.map((t, i) => (
          <div key={i} className="p-2 rounded bg-muted/20 border border-border/30">
            <div className="flex items-center gap-2 text-[10px] mb-1">
              <span className="font-bold text-poly-coral">{t.target_name}</span>
              <SideBadge side={t.side} />
              <span className="font-medium">{formatUSD(t.size_usdc)}</span>
              <span className="text-muted-foreground">@ {t.price.toFixed(3)}</span>
              <ConfluenceBadge count={t.confluence_count} />
            </div>
            <p className="text-[10px] text-muted-foreground truncate">{t.question}</p>
            <div className="flex gap-1 mt-1">
              <button className="px-2 py-0.5 rounded text-[10px] font-medium bg-poly-green/20 text-poly-green border border-poly-green/40 hover:bg-poly-green/30">
                COPY
              </button>
              <button className="px-2 py-0.5 rounded text-[10px] font-medium bg-poly-red/20 text-poly-red border border-poly-red/40 hover:bg-poly-red/30">
                SKIP
              </button>
            </div>
          </div>
        ))}
      </div>
    </PanelCard>
  );
}
