"use client";

import { useState, useEffect } from "react";
import { Clock, TrendingUp, TrendingDown, Filter } from "lucide-react";
import { Colors } from "@/lib/rork-types";
import { apiFetch } from "@/lib/utils";

interface Trade {
  ts: string;
  market_id: string;
  question: string;
  side: string;
  price: number;
  size_usdc: number;
  strategy: string;
  paper: boolean;
  pnl: number;
  trade_type: string;
  exit_reason: string;
}

interface TradeStats {
  total_trades: number;
  wins: number;
  losses: number;
  breakeven: number;
  total_pnl: number;
  gross_profit: number;
  gross_loss: number;
  avg_pnl: number;
  best_trade: number;
  worst_trade: number;
  win_rate: number;
  profit_factor: number;
}

export default function HistoryTab() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [filter, setFilter] = useState<"all" | "wins" | "losses">("all");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [logData, statsData] = await Promise.all([
          apiFetch<{ trades: Trade[] }>(`/api/portfolio/trade-log?limit=200&filter=${filter}`).catch(() => ({ trades: [] })),
          apiFetch<TradeStats>("/api/portfolio/trade-stats").catch(() => null),
        ]);
        setTrades(logData.trades || []);
        if (statsData) setStats(statsData);
      } catch {}
      setLoading(false);
    }
    load();
  }, [filter]);

  const fmtPnl = (n: number) => {
    const s = n >= 0 ? `+$${Math.abs(n).toFixed(2)}` : `-$${Math.abs(n).toFixed(2)}`;
    return s;
  };

  return (
    <div className="max-w-2xl mx-auto p-4 pb-8 space-y-3">
      {/* Stats summary */}
      {stats && stats.total_trades > 0 ? (
        <div className="rounded-xl p-4 space-y-3" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
          <span className="text-[10px] font-bold font-mono tracking-widest" style={{ color: Colors.textTertiary }}>TRADE PERFORMANCE</span>
          <div className="flex justify-around items-center py-2" style={{ borderTop: `1px solid ${Colors.surfaceBorder}`, borderBottom: `1px solid ${Colors.surfaceBorder}` }}>
            <div className="text-center">
              <span className="text-xl font-bold font-mono" style={{ color: Colors.green }}>{stats.wins}</span>
              <div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Wins</div>
            </div>
            <div className="w-px h-8" style={{ backgroundColor: Colors.surfaceBorder }} />
            <div className="text-center">
              <span className="text-xl font-bold font-mono" style={{ color: Colors.coral }}>{stats.losses}</span>
              <div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Losses</div>
            </div>
            <div className="w-px h-8" style={{ backgroundColor: Colors.surfaceBorder }} />
            <div className="text-center">
              <span className="text-xl font-bold font-mono" style={{ color: Colors.textPrimary }}>{stats.win_rate?.toFixed(1)}%</span>
              <div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Win Rate</div>
            </div>
            <div className="w-px h-8" style={{ backgroundColor: Colors.surfaceBorder }} />
            <div className="text-center">
              <span className="text-xl font-bold font-mono" style={{ color: stats.total_pnl >= 0 ? Colors.green : Colors.coral }}>
                {fmtPnl(stats.total_pnl)}
              </span>
              <div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Total P&L</div>
            </div>
          </div>
          <div className="grid grid-cols-4 gap-2 text-center">
            <div>
              <span className="text-sm font-bold font-mono" style={{ color: Colors.textPrimary }}>{stats.total_trades}</span>
              <div className="text-[8px] font-semibold uppercase" style={{ color: Colors.textTertiary }}>Total Trades</div>
            </div>
            <div>
              <span className="text-sm font-bold font-mono" style={{ color: Colors.green }}>{fmtPnl(stats.best_trade)}</span>
              <div className="text-[8px] font-semibold uppercase" style={{ color: Colors.textTertiary }}>Best Trade</div>
            </div>
            <div>
              <span className="text-sm font-bold font-mono" style={{ color: Colors.coral }}>{fmtPnl(stats.worst_trade)}</span>
              <div className="text-[8px] font-semibold uppercase" style={{ color: Colors.textTertiary }}>Worst Trade</div>
            </div>
            <div>
              <span className="text-sm font-bold font-mono" style={{ color: Colors.cyan }}>{stats.profit_factor === Infinity ? "∞" : stats.profit_factor?.toFixed(2)}</span>
              <div className="text-[8px] font-semibold uppercase" style={{ color: Colors.textTertiary }}>Profit Factor</div>
            </div>
          </div>
        </div>
      ) : (
        <div className="rounded-xl p-4 text-center" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
          <span className="text-sm" style={{ color: Colors.textTertiary }}>
            {loading ? "Loading trade history..." : "No closed trades yet — stats will appear after positions close"}
          </span>
        </div>
      )}

      {/* Filter buttons */}
      <div className="flex gap-2">
        {(["all", "wins", "losses"] as const).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className="px-3 py-1.5 rounded-lg text-[11px] font-semibold font-mono transition-colors"
            style={{
              backgroundColor: filter === f ? Colors.cyanDim : Colors.card,
              border: `1px solid ${filter === f ? Colors.cyan : Colors.cardBorder}`,
              color: filter === f ? Colors.cyan : Colors.textTertiary,
            }}>
            {f === "all" ? "All Trades" : f === "wins" ? "Wins" : "Losses"}
          </button>
        ))}
      </div>

      {/* Trade list */}
      {trades.length === 0 && !loading && (
        <div className="text-center py-10 text-sm" style={{ color: Colors.textTertiary }}>
          No trades recorded yet
        </div>
      )}

      {trades.map((t, i) => {
        const isWin = t.pnl > 0;
        const isClose = t.trade_type === "close";
        return (
          <div key={`${t.ts}-${i}`} className="rounded-xl p-3 space-y-2" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 flex-1 min-w-0">
                <span className="px-1.5 py-0.5 rounded text-[9px] font-bold font-mono tracking-wider"
                  style={{
                    backgroundColor: isClose ? (isWin ? Colors.greenDim : Colors.coralDim) : Colors.cyanDim,
                    color: isClose ? (isWin ? Colors.green : Colors.coral) : Colors.cyan,
                  }}>
                  {isClose ? (isWin ? "WIN" : "LOSS") : "OPEN"}
                </span>
                <span className="px-1.5 py-0.5 rounded text-[9px] font-bold font-mono"
                  style={{
                    backgroundColor: t.side === "YES" || t.side === "NO" ? (t.side === "YES" ? Colors.greenDim : Colors.coralDim) : Colors.cyanDim,
                    color: t.side === "YES" ? Colors.green : t.side === "NO" ? Colors.coral : Colors.cyan,
                  }}>
                  {t.side}
                </span>
                <span className="text-[12px] font-medium truncate" style={{ color: Colors.textPrimary }}>
                  {t.question || t.market_id?.slice(0, 20) || "Trade"}
                </span>
              </div>
              {t.pnl !== 0 && (
                <span className="text-[13px] font-bold font-mono shrink-0" style={{ color: isWin ? Colors.green : Colors.coral }}>
                  {fmtPnl(t.pnl)}
                </span>
              )}
            </div>
            <div className="flex justify-between text-[10px]" style={{ borderTop: `1px solid ${Colors.surfaceBorder}`, paddingTop: 6 }}>
              <span style={{ color: Colors.textTertiary }}>
                <Clock size={10} className="inline mr-1" style={{ verticalAlign: "middle" }} />
                {t.ts ? new Date(t.ts).toLocaleString() : "—"}
              </span>
              <span className="font-mono" style={{ color: Colors.textSecondary }}>{t.strategy}</span>
              <span className="font-mono" style={{ color: Colors.textSecondary }}>${t.size_usdc?.toFixed(0)} @ {(t.price * 100).toFixed(0)}¢</span>
            </div>
            {t.exit_reason && (
              <div className="text-[9px] font-mono" style={{ color: Colors.textTertiary }}>
                Exit: {t.exit_reason}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
