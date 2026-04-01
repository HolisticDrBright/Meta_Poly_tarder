"use client";

import { useState, useEffect } from "react";
import dynamic from "next/dynamic";
import { LayoutDashboard, BarChart3, Briefcase, BookOpen, Settings } from "lucide-react";

import { useMarkets, usePortfolioStats, usePositions, useSignals } from "@/hooks/useSignals";
import { useWebSocket } from "@/hooks/useWebSocket";
import { usePortfolioStore } from "@/stores/portfolioStore";
import { useMarketStore } from "@/stores/marketStore";
import { Colors } from "@/lib/rork-types";

// Dynamic imports — prevent SSR crashes
const DashboardTab = dynamic(() => import("@/components/rork/tabs/DashboardTab"), { ssr: false });
const MarketsTab = dynamic(() => import("@/components/rork/tabs/MarketsTab"), { ssr: false });
const PortfolioTab = dynamic(() => import("@/components/rork/tabs/PortfolioTab"), { ssr: false });
const JournalTab = dynamic(() => import("@/components/rork/tabs/JournalTab"), { ssr: false });
const SettingsTab = dynamic(() => import("@/components/rork/tabs/SettingsTab"), { ssr: false });

const TABS = [
  { key: "dashboard", label: "Dashboard", Icon: LayoutDashboard },
  { key: "markets", label: "Markets", Icon: BarChart3 },
  { key: "portfolio", label: "Portfolio", Icon: Briefcase },
  { key: "journal", label: "Journal", Icon: BookOpen },
  { key: "settings", label: "Settings", Icon: Settings },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function Page() {
  const [tab, setTab] = useState<TabKey>("dashboard");
  const [mounted, setMounted] = useState(false);
  const stats = usePortfolioStore((s) => s.stats);

  // Data hooks
  useMarkets(50);
  usePortfolioStats();
  usePositions();
  useSignals();
  useWebSocket();

  // Eager hydration
  useEffect(() => {
    setMounted(true);
    const buildTimeUrl = process.env.NEXT_PUBLIC_API_URL;
    const runtimeUrl = `http://${window.location.hostname}:8000`;
    const API = buildTimeUrl || runtimeUrl;

    Promise.all([
      fetch(`${API}/api/portfolio/stats`, { signal: AbortSignal.timeout(8000) }).then((r) => r.ok ? r.json() : null).catch(() => null),
      fetch(`${API}/api/portfolio/positions`, { signal: AbortSignal.timeout(8000) }).then((r) => r.ok ? r.json() : null).catch(() => null),
      fetch(`${API}/api/markets?limit=50`, { signal: AbortSignal.timeout(8000) }).then((r) => r.ok ? r.json() : null).catch(() => null),
    ]).then(([s, p, m]) => {
      if (s) usePortfolioStore.setState({ stats: s });
      if (p?.positions) usePortfolioStore.setState({ positions: p.positions });
      if (m && Array.isArray(m)) useMarketStore.setState({ markets: m });
    });
  }, []);

  const totalBalance = stats.balance + (stats.unrealized_pnl || 0);
  const roi = stats.balance > 0 ? ((totalBalance - 10000) / 10000) * 100 : 0;

  return (
    <div className="min-h-screen flex flex-col" style={{ backgroundColor: Colors.background }}>
      {/* Header */}
      <header className="sticky top-0 z-50 px-4 py-2 flex items-center justify-between"
        style={{ backgroundColor: Colors.background, borderBottom: `1px solid ${Colors.tabBarBorder}` }}>
        <span className="text-lg font-extrabold tracking-[0.15em]" style={{ color: Colors.cyan }}>
          METAPOLY
        </span>
        <div className="flex items-center gap-5">
          <div className="text-right">
            <div className="text-[9px] font-mono uppercase tracking-wider" style={{ color: Colors.textTertiary }}>Balance</div>
            <div className="text-sm font-bold font-mono" style={{ color: Colors.textPrimary }}>
              ${totalBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
          </div>
          <div className="text-right">
            <div className="text-[9px] font-mono uppercase tracking-wider" style={{ color: Colors.textTertiary }}>ROI</div>
            <div className="text-sm font-bold font-mono" style={{ color: roi >= 0 ? Colors.green : Colors.coral }}>
              {roi >= 0 ? "+" : ""}{roi.toFixed(1)}%
            </div>
          </div>
          <span className="text-[9px] font-bold font-mono tracking-wider px-2 py-0.5 rounded"
            style={{ backgroundColor: stats.paper_trading ? Colors.amberDim : Colors.coralDim, color: stats.paper_trading ? Colors.amber : Colors.coral }}>
            {stats.paper_trading ? "PAPER" : "LIVE"}
          </span>
        </div>
      </header>

      {/* Tab content */}
      <main className="flex-1 overflow-y-auto px-4 pt-3">
        {!mounted ? (
          <div className="flex items-center justify-center h-[60vh]" style={{ color: Colors.textTertiary }}>
            Loading...
          </div>
        ) : (
          <>
            {tab === "dashboard" && <DashboardTab />}
            {tab === "markets" && <MarketsTab />}
            {tab === "portfolio" && <PortfolioTab />}
            {tab === "journal" && <JournalTab />}
            {tab === "settings" && <SettingsTab />}
          </>
        )}
      </main>

      {/* Tab Bar */}
      <nav className="sticky bottom-0 z-50 flex justify-around py-2"
        style={{ backgroundColor: Colors.tabBar, borderTop: `1px solid ${Colors.tabBarBorder}` }}>
        {TABS.map((t) => {
          const active = tab === t.key;
          return (
            <button key={t.key} onClick={() => setTab(t.key)}
              className="flex flex-col items-center gap-0.5 py-1 px-3 transition-colors">
              <t.Icon size={20} color={active ? Colors.cyan : Colors.tabInactive} />
              <span className="text-[10px] font-semibold tracking-wide"
                style={{ color: active ? Colors.cyan : Colors.tabInactive }}>
                {t.label}
              </span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
