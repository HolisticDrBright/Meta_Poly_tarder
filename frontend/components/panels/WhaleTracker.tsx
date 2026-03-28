"use client";

import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { SideBadge, TierBadge } from "../shared/SignalBadge";
import { useSignalStore } from "@/stores/signalStore";
import { cn, formatUSD, timeAgo } from "@/lib/utils";

export default function WhaleTracker() {
  const whaleTrades = useSignalStore((s) => s.whaleTrades);
  const smi = useSignalStore((s) => s.smartMoneyIndex);

  const smiBias = smi > 60 ? "BULLISH" : smi < 40 ? "BEARISH" : "NEUTRAL";
  const smiColor = smi > 60 ? "text-poly-green" : smi < 40 ? "text-poly-red" : "text-poly-amber";

  const demo = whaleTrades.length
    ? whaleTrades
    : [
        { wallet: "0xRN1...abc", display_name: "@RN1", tier: "legendary", market_id: "1", question: "Will Fed cut rates by 50bps?", side: "YES" as const, size_usdc: 2500, price: 0.35, timestamp: new Date(Date.now() - 120000).toISOString() },
        { wallet: "0xAce...def", display_name: "AceTrader", tier: "elite", market_id: "2", question: "Will BTC exceed $100k by Q2?", side: "YES" as const, size_usdc: 1800, price: 0.42, timestamp: new Date(Date.now() - 300000).toISOString() },
        { wallet: "0xAlpha...789", display_name: "AlphaWhale", tier: "pro", market_id: "3", question: "Will SpaceX reach orbit?", side: "NO" as const, size_usdc: 900, price: 0.72, timestamp: new Date(Date.now() - 600000).toISOString() },
      ];

  return (
    <PanelCard>
      <PanelHeader title="WHALE TRACKER" subtitle="Smart money flow" refreshInterval={30} status="live" />

      {/* SMI Gauge */}
      <div className="flex items-center gap-3 mb-2 p-2 rounded bg-muted/30">
        <div className="text-[10px] text-muted-foreground">SMI</div>
        <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
          <div className="h-full bg-gradient-to-r from-poly-red via-poly-amber to-poly-green rounded-full" style={{ width: `${smi}%` }} />
        </div>
        <span className={cn("text-xs font-bold", smiColor)}>{smi}</span>
        <span className={cn("text-[10px] font-medium", smiColor)}>{smiBias}</span>
      </div>

      {/* Trade feed */}
      <div className="space-y-1 overflow-auto max-h-[calc(100%-80px)]">
        {demo.map((t, i) => (
          <div key={i} className="flex items-center gap-2 p-1.5 rounded bg-muted/20 hover:bg-muted/40 text-[10px]">
            <TierBadge tier={t.tier} />
            <span className="font-medium truncate max-w-[80px]">{t.display_name}</span>
            <SideBadge side={t.side} />
            <span className="font-bold">{formatUSD(t.size_usdc)}</span>
            <span className="text-muted-foreground truncate flex-1" title={t.question}>{t.question}</span>
            <span className="text-muted-foreground">{timeAgo(new Date(t.timestamp))}</span>
          </div>
        ))}
      </div>
    </PanelCard>
  );
}
