"use client";

import { useEffect, useState, useMemo } from "react";
import { Activity, Zap, Target, TrendingUp, TrendingDown, CheckCircle, XCircle, ArrowUpRight, ArrowDownRight } from "lucide-react";
import { Colors, type ActiveTrade, type RegimeInfo, type PortfolioGrowthPoint } from "@/lib/rork-types";
import { usePortfolioStore } from "@/stores/portfolioStore";
import { useMarketStore } from "@/stores/marketStore";
import OpportunityCard from "../OpportunityCard";
import PortfolioChart from "../PortfolioChart";

function getConfidenceColor(c: string) {
  if (c === "high") return Colors.green;
  if (c === "medium") return Colors.amber;
  return Colors.coral;
}

interface TradeStats {
  total_trades: number;
  wins: number;
  losses: number;
  total_pnl: number;
  win_rate: number;
}

export default function DashboardTab() {
  const stats = usePortfolioStore((s) => s.stats);
  const positions = usePortfolioStore((s) => s.positions);
  const equityCurve = usePortfolioStore((s) => s.equityCurve);
  const markets = useMarketStore((s) => s.markets);
  const selectMarket = useMarketStore((s) => s.selectMarket);
  const [chartWidth, setChartWidth] = useState(500);
  const [tradeStats, setTradeStats] = useState<TradeStats | null>(null);

  useEffect(() => {
    setChartWidth(Math.min(window.innerWidth - 80, 600));
    const h = () => setChartWidth(Math.min(window.innerWidth - 80, 600));
    window.addEventListener("resize", h);
    return () => window.removeEventListener("resize", h);
  }, []);

  // Pull the same authoritative stats the History tab uses, so Dashboard
  // and History always agree. Refresh every 30s.
  // The backend can sometimes return non-strict JSON (e.g. profit_factor =
  // Infinity) which breaks JSON.parse in some browsers. We fetch as text
  // and sanitise before parsing so the dashboard never crashes on that.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const r = await fetch("/api/portfolio/trade-stats");
        if (!r.ok) return;
        const raw = await r.text();
        const cleaned = raw
          .replace(/:\s*Infinity/g, ": null")
          .replace(/:\s*-Infinity/g, ": null")
          .replace(/:\s*NaN/g, ": null");
        let d: any = null;
        try { d = JSON.parse(cleaned); } catch { return; }
        if (cancelled || !d || typeof d.total_trades !== "number") return;
        // Coerce all numeric fields we might touch so nothing is undefined
        // when we reach the render.
        const safe: TradeStats = {
          total_trades: Number(d.total_trades) || 0,
          wins: Number(d.wins) || 0,
          losses: Number(d.losses) || 0,
          total_pnl: Number(d.total_pnl) || 0,
          win_rate: Number(d.win_rate) || 0,
        };
        setTradeStats(safe);
      } catch {}
    }
    load();
    const id = setInterval(load, 30000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const totalPnL = (stats.realized_pnl || 0) + (stats.unrealized_pnl || 0);
  // Real starting capital from backend, not hardcoded $10k
  const startingCapital = (stats as any).starting_capital || stats.balance || 0;
  const totalBalance = startingCapital + totalPnL;
  const pnlColor = totalPnL >= 0 ? Colors.green : Colors.coral;
  const todayColor = (stats.realized_pnl || 0) >= 0 ? Colors.green : Colors.coral;

  // Real wins/losses from DuckDB (same source as History tab). Fall back to
  // open-position counts only while trade-stats hasn't loaded yet.
  const wins = tradeStats
    ? tradeStats.wins
    : positions.filter((p: any) => (p.pnl || 0) > 0).length;
  const losses = tradeStats
    ? tradeStats.losses
    : positions.filter((p: any) => (p.pnl || 0) < 0).length;
  const totalTrades = tradeStats ? tradeStats.total_trades : wins + losses;
  const winRate = tradeStats
    ? (tradeStats.win_rate || 0).toFixed(1)
    : (wins + losses > 0 ? ((wins / (wins + losses)) * 100).toFixed(1) : "0");
  const roi = startingCapital > 0 && totalPnL !== 0
    ? ((totalPnL / startingCapital) * 100).toFixed(1)
    : "0";

  const regime: RegimeInfo = useMemo(() => {
    if (markets.length === 0) return { label: "Scanning...", confidence: "low" as const };
    const avgVol = markets.reduce((s: number, m: any) => s + (m.volume_24h || 0), 0) / markets.length;
    if (avgVol > 50000) return { label: "Information-Driven", confidence: "high" as const };
    if (avgVol > 10000) return { label: "Consensus-Grind", confidence: "medium" as const };
    return { label: "Low-Activity", confidence: "low" as const };
  }, [markets]);

  const growthData: PortfolioGrowthPoint[] = useMemo(() => {
    // Real equity curve points only. No synthetic fill-in.
    if (equityCurve.length >= 2) {
      return equityCurve.slice(-16).map((p: any) => ({
        day: new Date(p.timestamp).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
        value: p.balance,
      }));
    }
    return [];
  }, [equityCurve]);

  const marketOpps = useMemo(() => {
    // Only surface markets where the backend has actually computed a model
    // probability (from the AI ensemble / entropy screener). No heuristic
    // invention of edges on the client.
    return markets
      .filter((m: any) =>
        m.yes_price > 0.02 &&
        m.yes_price < 0.98 &&
        typeof m.model_probability === "number" &&
        m.model_probability > 0 &&
        m.model_probability !== m.yes_price
      )
      .slice(0, 12)
      .map((m: any) => {
        const mp = m.yes_price;
        const modelP = m.model_probability;
        const edge = (modelP - mp) * 100;
        const klDiv = m.kl_divergence || 0;
        const score = Math.min(100, Math.max(0, Math.round(
          Math.abs(edge) * 8 + klDiv * 50 + Math.min((m.liquidity || 0) / 50000, 30)
        )));
        let classification: "PAPER TRADE" | "WATCHLIST" | "NO-TRADE" = "NO-TRADE";
        if (score >= 60) classification = "PAPER TRADE";
        else if (score >= 40) classification = "WATCHLIST";
        // Real price history from the backend if available; otherwise empty
        // array (OpportunityCard renders no sparkline rather than synthetic).
        const history = Array.isArray(m.price_history) ? m.price_history : [];
        const sparkline = history.slice(-20).map((v: number) => ({ value: v * 100 }));
        return {
          id: m.id, title: m.question || m.title || "Market", category: (m.category || "economics") as any,
          opportunityScore: score, edgeEstimate: +edge.toFixed(1), classification,
          sparkline, currentPrice: mp,
          volume24h: m.volume_24h ? (m.volume_24h >= 1000000 ? `$${(m.volume_24h / 1000000).toFixed(1)}M` : `$${(m.volume_24h / 1000).toFixed(0)}K`) : "$0",
          lastUpdated: "now", aiSummary: "", fairProbability: modelP, marketProbability: mp,
        };
      });
  }, [markets]);

  const activeTrades = useMemo(() => {
    return positions.slice(0, 6).map((p: any) => {
      const entry = p.entry_price || 0;
      const current = p.current_price || entry;
      const edgePct = entry > 0 ? ((current - entry) / entry * 100) : 0;
      return {
        id: p.id?.toString() || p.market_id,
        title: p.question || "Position",
        direction: (p.side || "YES") as "YES" | "NO",
        entryPrice: entry,
        currentPrice: current,
        pnl: p.pnl || 0,
        size: Math.round(p.size_usdc || 0),
        enteredAt: p.opened_at ? new Date(p.opened_at).toLocaleDateString() : "",
        edge: +edgePct.toFixed(1),
      };
    });
  }, [positions]);

  const tradesToday = stats.trades_today || 0;
  const quickStats = [
    { label: "Markets Scanned", value: (stats.markets_count || markets.length).toString(), Icon: Activity },
    { label: "Total Trades", value: (totalTrades > 0 ? totalTrades : tradesToday).toLocaleString(), Icon: Zap },
    { label: "Win Rate", value: `${winRate}%`, Icon: Target },
  ];

  return (
    <div className="max-w-2xl mx-auto space-y-3 pb-8">
      {/* Regime Banner */}
      <div className="flex items-center justify-between rounded-xl px-3.5 py-2.5" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: getConfidenceColor(regime.confidence) }} />
          <span className="text-[11px] font-semibold font-mono tracking-wider" style={{ color: Colors.textTertiary }}>REGIME:</span>
          <span className="text-[13px] font-bold font-mono" style={{ color: Colors.textPrimary }}>{regime.label}</span>
        </div>
        <span className="text-[9px] font-bold font-mono tracking-wider px-2 py-0.5 rounded" style={{ color: getConfidenceColor(regime.confidence), backgroundColor: getConfidenceColor(regime.confidence) + "1A" }}>
          {regime.confidence.toUpperCase()}
        </span>
      </div>

      {/* Portfolio Strip */}
      <div className="rounded-xl p-3.5 space-y-3" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <div className="flex justify-between items-start">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wider mb-0.5" style={{ color: Colors.textTertiary }}>Paper P&L</div>
            <div className="text-2xl font-extrabold font-mono" style={{ color: pnlColor }}>
              {totalPnL >= 0 ? "+" : ""}${Math.abs(totalPnL).toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </div>
          </div>
          <div className="text-right">
            <div className="text-[9px] font-bold font-mono tracking-wider" style={{ color: Colors.textTertiary }}>TODAY</div>
            <div className="flex items-center gap-1 justify-end">
              {(stats.realized_pnl || 0) >= 0 ? <ArrowUpRight size={14} color={todayColor} /> : <ArrowDownRight size={14} color={todayColor} />}
              <span className="text-[15px] font-bold font-mono" style={{ color: todayColor }}>
                {(stats.realized_pnl || 0) >= 0 ? "+" : ""}${Math.abs(stats.realized_pnl || 0).toFixed(2)}
              </span>
            </div>
          </div>
        </div>

        {/* Metrics grid */}
        <div className="flex justify-around items-center py-3" style={{ borderTop: `1px solid ${Colors.surfaceBorder}`, borderBottom: `1px solid ${Colors.surfaceBorder}` }}>
          <div className="text-center">
            <div className="flex items-center gap-1 justify-center"><CheckCircle size={12} color={Colors.green} /><span className="text-base font-bold font-mono" style={{ color: Colors.green }}>{wins}</span></div>
            <div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Wins</div>
          </div>
          <div className="w-px h-7" style={{ backgroundColor: Colors.surfaceBorder }} />
          <div className="text-center">
            <div className="flex items-center gap-1 justify-center"><XCircle size={12} color={Colors.coral} /><span className="text-base font-bold font-mono" style={{ color: Colors.coral }}>{losses}</span></div>
            <div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Losses</div>
          </div>
          <div className="w-px h-7" style={{ backgroundColor: Colors.surfaceBorder }} />
          <div className="text-center">
            <span className="text-base font-bold font-mono" style={{ color: Colors.textPrimary }}>{winRate}%</span>
            <div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Win Rate</div>
          </div>
          <div className="w-px h-7" style={{ backgroundColor: Colors.surfaceBorder }} />
          <div className="text-center">
            <span className="text-base font-bold font-mono" style={{ color: Colors.cyan }}>{+roi >= 0 ? "+" : ""}{roi}%</span>
            <div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>ROI</div>
          </div>
        </div>

        {/* Bottom stats */}
        <div className="flex justify-around items-center">
          <div className="text-center"><span className="text-base font-bold font-mono" style={{ color: Colors.textPrimary }}>{stats.positions_count || positions.length}</span><div className="text-[10px] font-medium uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Positions</div></div>
          <div className="w-px h-6" style={{ backgroundColor: Colors.surfaceBorder }} />
          <div className="text-center"><span className="text-base font-bold font-mono" style={{ color: Colors.textPrimary }}>{stats.sharpe_ratio?.toFixed(3) || "—"}</span><div className="text-[10px] font-medium uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Sharpe</div></div>
          <div className="w-px h-6" style={{ backgroundColor: Colors.surfaceBorder }} />
          <div className="text-center"><span className="text-base font-bold font-mono" style={{ color: Colors.cyan }}>{tradeStats && typeof tradeStats.win_rate === "number" ? `${tradeStats.win_rate.toFixed(0)}%` : "—"}</span><div className="text-[10px] font-medium uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Accuracy</div></div>
        </div>
      </div>

      {/* Portfolio Chart */}
      <PortfolioChart data={growthData} width={chartWidth} height={160} />

      {/* Quick Stats */}
      <div className="grid grid-cols-3 gap-2">
        {quickStats.map((s) => (
          <div key={s.label} className="flex flex-col items-center rounded-xl p-3 gap-1" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
            <div className="w-7 h-7 rounded-full flex items-center justify-center" style={{ backgroundColor: Colors.cyanDim }}>
              <s.Icon size={14} color={Colors.cyan} />
            </div>
            <span className="text-lg font-bold font-mono" style={{ color: Colors.textPrimary }}>{s.value}</span>
            <span className="text-[9px] font-semibold uppercase tracking-wider text-center" style={{ color: Colors.textTertiary }}>{s.label}</span>
          </div>
        ))}
      </div>

      {/* Active Trades */}
      <div className="flex justify-between items-center mt-1">
        <span className="text-[11px] font-bold font-mono tracking-widest" style={{ color: Colors.textSecondary }}>ACTIVE TRADES</span>
        <span className="text-[11px] font-mono" style={{ color: Colors.textTertiary }}>{activeTrades.length} positions</span>
      </div>
      {activeTrades.length === 0 && (
        <div className="rounded-xl p-4 text-center" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
          <span className="text-[11px] font-mono" style={{ color: Colors.textTertiary }}>No open positions — bot is scanning for signals</span>
        </div>
      )}
      {activeTrades.length > 0 && (
        <>
          {activeTrades.map((t) => {
            const tColor = t.pnl >= 0 ? Colors.green : Colors.coral;
            const isUp = t.currentPrice >= t.entryPrice;
            return (
              <div key={t.id} className="rounded-xl p-3 space-y-2.5" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-bold font-mono tracking-wider" style={{ backgroundColor: t.direction === "YES" ? Colors.greenDim : Colors.coralDim, color: t.direction === "YES" ? Colors.green : Colors.coral }}>{t.direction}</span>
                    <span className="text-[13px] font-semibold truncate" style={{ color: Colors.textPrimary }}>{t.title}</span>
                  </div>
                  <span className="text-sm font-bold font-mono" style={{ color: tColor }}>{t.pnl >= 0 ? "+" : ""}${Math.abs(t.pnl).toFixed(0)}</span>
                </div>
                <div className="flex justify-between pt-2" style={{ borderTop: `1px solid ${Colors.surfaceBorder}` }}>
                  <div><div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Entry</div><div className="text-xs font-semibold font-mono" style={{ color: Colors.textSecondary }}>{(t.entryPrice * 100).toFixed(0)}&cent;</div></div>
                  <div><div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Now</div><div className="flex items-center gap-1"><span className="text-xs font-semibold font-mono" style={{ color: isUp ? Colors.green : Colors.coral }}>{(t.currentPrice * 100).toFixed(0)}&cent;</span></div></div>
                  <div><div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Edge</div><div className="text-xs font-bold font-mono" style={{ color: t.edge >= 0 ? Colors.cyan : Colors.coral }}>{t.edge >= 0 ? "+" : ""}{t.edge}%</div></div>
                  <div><div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Size</div><div className="text-xs font-semibold font-mono" style={{ color: Colors.textSecondary }}>${t.size.toLocaleString()}</div></div>
                  <div><div className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Opened</div><div className="text-xs font-semibold font-mono" style={{ color: Colors.textSecondary }}>{t.enteredAt}</div></div>
                </div>
              </div>
            );
          })}
        </>
      )}

      {/* Opportunity Feed */}
      {marketOpps.length > 0 && (
        <>
          <div className="flex justify-between items-center mt-1">
            <span className="text-[11px] font-bold font-mono tracking-widest" style={{ color: Colors.textSecondary }}>OPPORTUNITY FEED</span>
            <span className="text-[11px] font-mono" style={{ color: Colors.textTertiary }}>{marketOpps.length} markets</span>
          </div>
          {marketOpps.map((m) => (
            <OpportunityCard key={m.id} market={m} onClick={() => selectMarket(m.id)} />
          ))}
        </>
      )}
    </div>
  );
}
