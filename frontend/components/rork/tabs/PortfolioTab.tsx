"use client";

import { useMemo } from "react";
import { TrendingUp, TrendingDown, Target, Award, BarChart3 } from "lucide-react";
import { Colors } from "@/lib/rork-types";
import { usePortfolioStore } from "@/stores/portfolioStore";
import { closePosition } from "@/lib/api";

export default function PortfolioTab() {
  const stats = usePortfolioStore((s) => s.stats);
  const positions = usePortfolioStore((s) => s.positions);

  const totalPnL = stats.realized_pnl + (stats.unrealized_pnl || 0);
  const pnlColor = totalPnL >= 0 ? Colors.green : Colors.coral;
  const PnlIcon = totalPnL >= 0 ? TrendingUp : TrendingDown;

  return (
    <div className="max-w-2xl mx-auto p-4 pb-8 space-y-3">
      {/* Hero P&L */}
      <div className="rounded-xl p-5 flex flex-col items-center gap-1.5" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <span className="text-[11px] font-bold font-mono tracking-widest" style={{ color: Colors.textTertiary }}>TOTAL PAPER P&L</span>
        <div className="flex items-center gap-2">
          <PnlIcon size={28} color={pnlColor} />
          <span className="text-3xl font-extrabold font-mono" style={{ color: pnlColor }}>
            {totalPnL >= 0 ? "+" : ""}${Math.abs(totalPnL).toLocaleString("en-US", { minimumFractionDigits: 2 })}
          </span>
        </div>
        <span className="text-[11px] mt-0.5" style={{ color: Colors.textTertiary }}>
          {stats.paper_trading ? "Research mode only" : "LIVE TRADING"}
        </span>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-3 gap-2">
        <div className="flex flex-col items-center rounded-xl p-3 gap-1.5" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
          <div className="w-8 h-8 rounded-full flex items-center justify-center" style={{ backgroundColor: Colors.cyanDim }}><Target size={16} color={Colors.cyan} /></div>
          <span className="text-lg font-bold font-mono" style={{ color: Colors.textPrimary }}>{positions.length}</span>
          <span className="text-[9px] font-semibold uppercase tracking-wider text-center" style={{ color: Colors.textTertiary }}>Active Positions</span>
        </div>
        <div className="flex flex-col items-center rounded-xl p-3 gap-1.5" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
          <div className="w-8 h-8 rounded-full flex items-center justify-center" style={{ backgroundColor: Colors.amberDim }}><BarChart3 size={16} color={Colors.amber} /></div>
          <span className="text-lg font-bold font-mono" style={{ color: Colors.textPrimary }}>{stats.sharpe_ratio?.toFixed(2) || "—"}</span>
          <span className="text-[9px] font-semibold uppercase tracking-wider text-center" style={{ color: Colors.textTertiary }}>Sharpe Ratio</span>
        </div>
        <div className="flex flex-col items-center rounded-xl p-3 gap-1.5" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
          <div className="w-8 h-8 rounded-full flex items-center justify-center" style={{ backgroundColor: Colors.greenDim }}><Award size={16} color={Colors.green} /></div>
          <span className="text-lg font-bold font-mono" style={{ color: Colors.cyan }}>{stats.win_rate ? `${(stats.win_rate * 100).toFixed(0)}%` : "—"}</span>
          <span className="text-[9px] font-semibold uppercase tracking-wider text-center" style={{ color: Colors.textTertiary }}>Win Rate</span>
        </div>
      </div>

      {/* Active Trades */}
      <span className="text-[11px] font-bold font-mono tracking-widest block mt-2" style={{ color: Colors.textSecondary }}>ACTIVE POSITIONS</span>

      {positions.length === 0 ? (
        <div className="text-center py-10 text-sm" style={{ color: Colors.textTertiary }}>No active positions</div>
      ) : (
        positions.map((p: any) => (
          <div key={p.id || p.market_id} className="rounded-xl p-3.5 space-y-2.5" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
            <div className="flex justify-between items-start gap-3">
              <span className="text-[13px] font-semibold flex-1 leading-tight" style={{ color: Colors.textPrimary }}>{p.question || "Position"}</span>
              <span className="text-[15px] font-bold font-mono" style={{ color: (p.pnl || 0) >= 0 ? Colors.cyan : Colors.coral }}>
                {(p.pnl || 0) >= 0 ? "+" : ""}{((p.pnl || 0)).toFixed(1)}%
              </span>
            </div>
            <div className="flex justify-between pt-2" style={{ borderTop: `1px solid ${Colors.surfaceBorder}` }}>
              <div className="text-center"><div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Entry</div><div className="text-[13px] font-semibold font-mono" style={{ color: Colors.textSecondary }}>{((p.entry_price || 0) * 100).toFixed(0)}&cent;</div></div>
              <div className="text-center"><div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Fair</div><div className="text-[13px] font-semibold font-mono" style={{ color: Colors.textSecondary }}>{((p.current_price || 0) * 100).toFixed(1)}%</div></div>
              <div className="text-center"><div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Size</div><div className="text-[13px] font-semibold font-mono" style={{ color: Colors.green }}>${(p.size_usdc || 0).toFixed(0)}</div></div>
              <div className="text-center"><div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Side</div><div className="text-[13px] font-semibold font-mono" style={{ color: p.side === "YES" ? Colors.green : Colors.coral }}>{p.side}</div></div>
            </div>
            <button
              onClick={async () => { try { await closePosition(p.market_id); } catch {} }}
              className="w-full py-1.5 rounded-lg text-[11px] font-bold font-mono tracking-wider transition-colors"
              style={{ backgroundColor: Colors.coralDim, color: Colors.coral, border: `1px solid rgba(255,59,92,0.2)` }}
            >
              CLOSE POSITION
            </button>
          </div>
        ))
      )}

      {/* Disclaimer */}
      <div className="rounded-lg p-3 mt-4" style={{ backgroundColor: Colors.amberDim, border: `1px solid rgba(255,184,0,0.2)` }}>
        <p className="text-[11px] text-center leading-4 font-medium" style={{ color: Colors.amber }}>
          {stats.paper_trading ? "All positions are paper trades for research purposes only. No real money is at risk." : "LIVE TRADING MODE — Real funds at risk."}
        </p>
      </div>
    </div>
  );
}
