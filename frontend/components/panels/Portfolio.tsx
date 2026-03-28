"use client";

import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { StrategyBadge, SideBadge } from "../shared/SignalBadge";
import { usePortfolioStore, type Position } from "@/stores/portfolioStore";
import { cn, formatUSD, hoursUntil } from "@/lib/utils";

export default function Portfolio() {
  const positions = usePortfolioStore((s) => s.positions);
  const stats = usePortfolioStore((s) => s.stats);

  const demo: Position[] = positions.length
    ? positions
    : [
        { id: 1, market_id: "1", question: "Will Fed cut rates by 50bps?", side: "YES", entry_price: 0.32, size_usdc: 89, current_price: 0.35, strategy: "entropy", opened_at: new Date(Date.now() - 86400000).toISOString(), pnl: 8.34, pnl_pct: 9.4, hours_to_close: 168 },
        { id: 2, market_id: "2", question: "Will BTC exceed $100k by Q2?", side: "YES", entry_price: 0.40, size_usdc: 50, current_price: 0.42, strategy: "copy", opened_at: new Date(Date.now() - 43200000).toISOString(), pnl: 2.50, pnl_pct: 5.0, hours_to_close: 720 },
        { id: 3, market_id: "3", question: "BTC 15min Up?", side: "YES", entry_price: 0.48, size_usdc: 25, current_price: 0.52, strategy: "arb", opened_at: new Date(Date.now() - 600000).toISOString(), pnl: 2.08, pnl_pct: 8.3, hours_to_close: 0.2 },
      ];

  const totalPnl = demo.reduce((s, p) => s + p.pnl, 0);
  const totalExposure = demo.reduce((s, p) => s + p.size_usdc, 0);

  return (
    <PanelCard>
      <PanelHeader title="PORTFOLIO" subtitle={`${demo.length} open positions`} refreshInterval={15} status="live" />

      {/* Summary */}
      <div className="flex gap-4 mb-2 text-[10px]">
        <div>
          <span className="text-muted-foreground">Exposure: </span>
          <span className="font-bold">{formatUSD(totalExposure)}</span>
        </div>
        <div>
          <span className="text-muted-foreground">PnL: </span>
          <span className={cn("font-bold", totalPnl >= 0 ? "text-poly-green" : "text-poly-red")}>
            {totalPnl >= 0 ? "+" : ""}{formatUSD(totalPnl)}
          </span>
        </div>
        <div>
          <span className="text-muted-foreground">Mode: </span>
          <span className={cn("font-bold", stats.paper_trading ? "text-poly-amber" : "text-poly-red")}>
            {stats.paper_trading ? "PAPER" : "LIVE"}
          </span>
        </div>
      </div>

      {/* Positions table */}
      <div className="overflow-auto max-h-[calc(100%-60px)]">
        <table className="w-full text-[10px]">
          <thead className="sticky top-0 bg-card">
            <tr className="text-muted-foreground text-left">
              <th className="py-1">Market</th>
              <th className="py-1">Strategy</th>
              <th className="py-1">Side</th>
              <th className="py-1 text-right">Entry</th>
              <th className="py-1 text-right">Current</th>
              <th className="py-1 text-right">Size</th>
              <th className="py-1 text-right">PnL</th>
              <th className="py-1 text-right">Closes</th>
              <th className="py-1"></th>
            </tr>
          </thead>
          <tbody>
            {demo.map((p) => (
              <tr key={p.id} className="border-b border-border/30 hover:bg-muted/30">
                <td className="py-1 max-w-[120px] truncate" title={p.question}>
                  {p.question}
                </td>
                <td className="py-1">
                  <StrategyBadge strategy={p.strategy} />
                </td>
                <td className="py-1">
                  <SideBadge side={p.side} />
                </td>
                <td className="py-1 text-right">{p.entry_price.toFixed(3)}</td>
                <td className="py-1 text-right font-medium">{p.current_price.toFixed(3)}</td>
                <td className="py-1 text-right">{formatUSD(p.size_usdc)}</td>
                <td className={cn("py-1 text-right font-bold", p.pnl >= 0 ? "text-poly-green" : "text-poly-red")}>
                  {p.pnl >= 0 ? "+" : ""}{formatUSD(p.pnl)}
                </td>
                <td className={cn("py-1 text-right", p.hours_to_close !== null && p.hours_to_close < 6 && "text-poly-red")}>
                  {p.hours_to_close !== null ? `${p.hours_to_close.toFixed(0)}h` : "—"}
                </td>
                <td className="py-1">
                  <button className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-poly-red/20 text-poly-red border border-poly-red/40 hover:bg-poly-red/30">
                    CLOSE
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {demo.length === 0 && (
          <div className="text-center text-muted-foreground text-sm py-6">No open positions</div>
        )}
      </div>
    </PanelCard>
  );
}
