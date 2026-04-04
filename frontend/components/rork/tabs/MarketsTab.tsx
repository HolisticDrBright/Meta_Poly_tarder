"use client";

import { useState, useMemo } from "react";
import { Search, SlidersHorizontal } from "lucide-react";
import { Colors, type Classification, type MarketCategory } from "@/lib/rork-types";
import { useMarketStore } from "@/stores/marketStore";
import OpportunityCard from "../OpportunityCard";

const FILTERS: { label: string; value: Classification | "ALL" }[] = [
  { label: "All", value: "ALL" },
  { label: "Paper Trade", value: "PAPER TRADE" },
  { label: "Watchlist", value: "WATCHLIST" },
  { label: "No-Trade", value: "NO-TRADE" },
];

const CATEGORIES: { label: string; value: MarketCategory | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Politics", value: "politics" },
  { label: "Crypto", value: "crypto" },
  { label: "Sports", value: "sports" },
  { label: "Tech", value: "tech" },
  { label: "Economics", value: "economics" },
];

export default function MarketsTab() {
  const markets = useMarketStore((s) => s.markets);
  const selectMarket = useMarketStore((s) => s.selectMarket);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Classification | "ALL">("ALL");
  const [category, setCategory] = useState<MarketCategory | "all">("all");

  const opps = useMemo(() => {
    return markets.map((m: any) => {
      // Only compute an edge when the backend has produced a real model
      // probability. Otherwise leave edge/score at zero so nothing is
      // fabricated on the client.
      const hasModel =
        typeof m.model_probability === "number" &&
        m.model_probability > 0 &&
        m.model_probability !== m.yes_price;
      const modelP = hasModel ? m.model_probability : m.yes_price;
      const edge = hasModel ? (modelP - m.yes_price) * 100 : 0;
      const score = hasModel
        ? Math.min(100, Math.max(0, Math.round(Math.abs(edge) * 10 + (m.liquidity || 0) / 10000)))
        : 0;
      let classification: Classification = "NO-TRADE";
      if (score >= 60) classification = "PAPER TRADE";
      else if (score >= 40) classification = "WATCHLIST";
      const cat = (m.category || "economics").toLowerCase();
      // Real price history only. Empty when backend hasn't provided it.
      const history = Array.isArray(m.price_history) ? m.price_history : [];
      const sparkline = history.slice(-20).map((v: number) => ({ value: v * 100 }));
      return {
        id: m.id, title: m.question || m.title || "Market",
        category: cat as MarketCategory,
        opportunityScore: score, edgeEstimate: +edge.toFixed(1), classification,
        sparkline, currentPrice: m.yes_price || 0,
        volume24h: m.volume_24h ? `$${(m.volume_24h / 1000).toFixed(0)}K` : "$0",
        lastUpdated: "now", aiSummary: "", fairProbability: modelP, marketProbability: m.yes_price || 0,
      };
    });
  }, [markets]);

  const filtered = useMemo(() => {
    return opps.filter((m) => {
      if (filter !== "ALL" && m.classification !== filter) return false;
      if (category !== "all" && m.category !== category) return false;
      if (search && !m.title.toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    });
  }, [opps, filter, category, search]);

  return (
    <div className="max-w-2xl mx-auto pb-8 space-y-3">
      {/* Search */}
      <div className="flex items-center gap-2 rounded-xl px-3 py-2.5" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <Search size={16} color={Colors.textTertiary} />
        <input
          type="text" placeholder="Search markets..." value={search} onChange={(e) => setSearch(e.target.value)}
          className="flex-1 bg-transparent text-sm font-mono outline-none" style={{ color: Colors.textPrimary }}
        />
      </div>

      {/* Filters */}
      <div className="flex gap-1.5 flex-wrap">
        {FILTERS.map((f) => (
          <button key={f.value} onClick={() => setFilter(f.value)}
            className="px-3 py-1.5 rounded-md text-[11px] font-semibold font-mono transition-colors"
            style={{
              backgroundColor: filter === f.value ? Colors.cyanDim : Colors.card,
              border: `1px solid ${filter === f.value ? Colors.cyan : Colors.cardBorder}`,
              color: filter === f.value ? Colors.cyan : Colors.textTertiary,
            }}>
            {f.label}
          </button>
        ))}
      </div>
      <div className="flex gap-1.5 flex-wrap">
        {CATEGORIES.map((c) => (
          <button key={c.value} onClick={() => setCategory(c.value)}
            className="px-2.5 py-1 rounded-md text-[10px] font-semibold transition-colors"
            style={{
              backgroundColor: category === c.value ? "rgba(255,255,255,0.04)" : "transparent",
              border: `1px solid ${category === c.value ? Colors.textSecondary : Colors.surfaceBorder}`,
              color: category === c.value ? Colors.textPrimary : Colors.textTertiary,
            }}>
            {c.label}
          </button>
        ))}
      </div>

      {/* Market list */}
      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center pt-16 gap-3">
          <SlidersHorizontal size={32} color={Colors.textTertiary} />
          <span className="text-sm font-mono" style={{ color: Colors.textTertiary }}>No markets match your filters</span>
        </div>
      ) : (
        filtered.map((m) => <OpportunityCard key={m.id} market={m} onClick={() => selectMarket(m.id)} />)
      )}
    </div>
  );
}
