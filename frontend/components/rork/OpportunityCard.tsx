"use client";

import { Colors, type MarketOpportunity } from "@/lib/rork-types";
import RadialGauge from "./RadialGauge";
import Sparkline from "./Sparkline";
import ClassificationBadge from "./ClassificationBadge";

function getSparklineColor(c: string) {
  if (c === "PAPER TRADE") return Colors.cyan;
  if (c === "WATCHLIST") return Colors.amber;
  return Colors.textTertiary;
}

export default function OpportunityCard({
  market,
  onClick,
}: {
  market: MarketOpportunity;
  onClick?: () => void;
}) {
  const edgeColor = market.edgeEstimate >= 0 ? Colors.cyan : Colors.coral;
  const edgePrefix = market.edgeEstimate >= 0 ? "+" : "";

  return (
    <div
      onClick={onClick}
      className="mb-2.5 cursor-pointer transition-transform hover:scale-[0.98] active:scale-[0.97]"
    >
      <div
        className="rounded-xl p-3.5 space-y-3"
        style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}
      >
        {/* Top row: gauge + title */}
        <div className="flex gap-3 items-start">
          <RadialGauge score={market.opportunityScore} />
          <div className="flex-1 space-y-1.5">
            <p className="text-sm font-semibold leading-tight" style={{ color: Colors.textPrimary }}>
              {market.title}
            </p>
            <div className="flex items-center gap-2">
              <ClassificationBadge classification={market.classification} />
              <span className="text-[11px] font-mono" style={{ color: Colors.textTertiary }}>
                {market.lastUpdated}
              </span>
            </div>
          </div>
        </div>

        {/* Bottom row: stats + sparkline */}
        <div className="flex items-center justify-between pt-1" style={{ borderTop: `1px solid ${Colors.surfaceBorder}` }}>
          <div className="flex gap-4">
            <div>
              <div className="text-[10px] font-medium uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Edge</div>
              <div className="text-[13px] font-bold font-mono" style={{ color: edgeColor }}>
                {edgePrefix}{market.edgeEstimate.toFixed(1)}%
              </div>
            </div>
            <div>
              <div className="text-[10px] font-medium uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Price</div>
              <div className="text-[13px] font-semibold font-mono" style={{ color: Colors.textSecondary }}>
                {market.currentPrice >= 0.01 ? `${(market.currentPrice * 100).toFixed(0)}¢` : `${(market.currentPrice * 100).toFixed(1)}¢`}
              </div>
            </div>
            <div>
              <div className="text-[10px] font-medium uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Vol</div>
              <div className="text-[13px] font-semibold font-mono" style={{ color: Colors.textSecondary }}>
                {market.volume24h}
              </div>
            </div>
          </div>
          <Sparkline data={market.sparkline} width={80} height={30} color={getSparklineColor(market.classification)} />
        </div>
      </div>
    </div>
  );
}
