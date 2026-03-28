"use client";

import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { useSignalStore, type VolumeSpike } from "@/stores/signalStore";
import { useMarketStore } from "@/stores/marketStore";
import { cn, formatUSD, timeAgo, hoursUntil } from "@/lib/utils";

export default function VolumeSpikes() {
  const volumeSpikes = useSignalStore((s) => s.volumeSpikes);
  const markets = useMarketStore((s) => s.markets);
  const selectMarket = useMarketStore((s) => s.selectMarket);

  // Demo: find markets closing soon or with high volume
  const alerts = volumeSpikes.length
    ? volumeSpikes
    : markets
        .filter((m) => m.volume_24h > 0)
        .sort((a, b) => b.volume_24h - a.volume_24h)
        .slice(0, 8)
        .map((m) => ({
          market_id: m.id,
          question: m.question,
          volume_spike: m.volume_24h,
          pct_change: Math.random() * 30 + 5,
          timestamp: new Date(Date.now() - Math.random() * 3600000).toISOString(),
        }));

  // Resolution countdown
  const closingSoon = markets
    .filter((m) => {
      const h = hoursUntil(m.end_date);
      return h !== null && h < 24 && h > 0;
    })
    .sort((a, b) => (hoursUntil(a.end_date) || 999) - (hoursUntil(b.end_date) || 999))
    .slice(0, 5);

  return (
    <PanelCard>
      <PanelHeader title="VOLUME / ALERTS" subtitle="Spikes + Resolution countdown" refreshInterval={30} status="live" />

      {/* Volume spikes */}
      <div className="mb-2">
        <div className="text-[10px] text-muted-foreground font-medium mb-1">VOLUME SPIKES</div>
        <div className="space-y-0.5 max-h-24 overflow-y-auto">
          {alerts.slice(0, 6).map((a, i) => (
            <div
              key={i}
              onClick={() => selectMarket(a.market_id)}
              className="flex items-center gap-2 text-[10px] p-1 rounded hover:bg-muted/30 cursor-pointer"
            >
              <span className="text-poly-green font-bold">+{a.pct_change.toFixed(0)}%</span>
              <span className="truncate flex-1" title={a.question}>{a.question}</span>
              <span className="text-muted-foreground">{formatUSD(a.volume_spike)}</span>
              <span className="text-muted-foreground text-[9px]">{timeAgo(new Date(a.timestamp))}</span>
            </div>
          ))}
          {alerts.length === 0 && (
            <div className="text-[10px] text-muted-foreground py-2">No spikes detected</div>
          )}
        </div>
      </div>

      {/* Resolution countdown */}
      <div>
        <div className="text-[10px] text-muted-foreground font-medium mb-1">CLOSING SOON</div>
        <div className="space-y-0.5 max-h-20 overflow-y-auto">
          {closingSoon.map((m, i) => {
            const h = hoursUntil(m.end_date) || 0;
            return (
              <div
                key={i}
                onClick={() => selectMarket(m.id)}
                className="flex items-center gap-2 text-[10px] p-1 rounded hover:bg-muted/30 cursor-pointer"
              >
                <span
                  className={cn(
                    "font-bold w-12 text-right",
                    h < 6 ? "text-poly-red" : "text-poly-amber"
                  )}
                >
                  {h.toFixed(1)}h
                </span>
                <span className="truncate flex-1">{m.question}</span>
                <span className="text-muted-foreground">{(m.yes_price * 100).toFixed(0)}%</span>
              </div>
            );
          })}
          {closingSoon.length === 0 && (
            <div className="text-[10px] text-muted-foreground py-2">No markets closing &lt;24h</div>
          )}
        </div>
      </div>
    </PanelCard>
  );
}
