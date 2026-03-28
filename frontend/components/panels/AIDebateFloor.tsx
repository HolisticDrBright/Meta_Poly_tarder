"use client";

import { useState } from "react";
import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { SideBadge, EdgeBadge } from "../shared/SignalBadge";
import { useMarketStore } from "@/stores/marketStore";
import { cn, formatPct } from "@/lib/utils";

interface AgentResult {
  role: string;
  probability: number;
  reasoning: string;
  icon: string;
}

const AGENT_ICONS: Record<string, string> = {
  "Statistics Expert": "S",
  "Time Decay Analyst": "T",
  "Generalist Expert": "G",
  "Crypto/Macro Analyst": "C",
  "Devil's Advocate": "D",
  "Jet Signal Analyst": "J",
  "Moderator": "M",
};

const AGENT_COLORS: Record<string, string> = {
  "Statistics Expert": "border-poly-blue",
  "Time Decay Analyst": "border-poly-amber",
  "Generalist Expert": "border-gray-500",
  "Crypto/Macro Analyst": "border-poly-teal",
  "Devil's Advocate": "border-poly-red",
  "Jet Signal Analyst": "border-poly-coral",
  "Moderator": "border-poly-purple",
};

export default function AIDebateFloor() {
  const market = useMarketStore((s) => s.selectedMarket);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<AgentResult[] | null>(null);
  const [finalP, setFinalP] = useState<number | null>(null);

  const runDebate = () => {
    if (!market) return;
    setRunning(true);
    // Simulate debate results (in production, calls Claude/GPT-4o API)
    setTimeout(() => {
      const base = market.yes_price;
      const agents: AgentResult[] = [
        { role: "Statistics Expert", probability: base + (Math.random() - 0.5) * 0.15, reasoning: "Base rates suggest moderate likelihood given historical precedent.", icon: "S" },
        { role: "Time Decay Analyst", probability: base + (Math.random() - 0.5) * 0.1, reasoning: `${((market.end_date ? (new Date(market.end_date).getTime() - Date.now()) / 3600000 : 999).toFixed(0))}h remaining. Theta factor moderate.`, icon: "T" },
        { role: "Generalist Expert", probability: base + (Math.random() - 0.5) * 0.08, reasoning: "Balanced view considering multiple factors. Slight edge detected.", icon: "G" },
        { role: "Crypto/Macro Analyst", probability: base + (Math.random() - 0.5) * 0.12, reasoning: "Macro conditions and correlation analysis point to this range.", icon: "C" },
        { role: "Devil's Advocate", probability: 1 - base + (Math.random() - 0.5) * 0.1, reasoning: "Contrarian position: the market may be underpricing tail risks.", icon: "D" },
        { role: "Jet Signal Analyst", probability: base + Math.random() * 0.05, reasoning: "No active jet signals for this market currently.", icon: "J" },
        { role: "Moderator", probability: base + (Math.random() - 0.3) * 0.1, reasoning: "Synthesizing all views. Moderate confidence in slight edge.", icon: "M" },
      ].map((a) => ({ ...a, probability: Math.max(0.05, Math.min(0.95, a.probability)) }));
      const final = agents[agents.length - 1].probability;
      setResults(agents);
      setFinalP(final);
      setRunning(false);
    }, 2000);
  };

  return (
    <PanelCard>
      <PanelHeader title="AI DEBATE FLOOR" subtitle="7-agent deliberation" status={running ? "paused" : "live"} />

      {!market ? (
        <div className="text-center text-muted-foreground text-sm py-8">
          Select a market to run debate
        </div>
      ) : (
        <>
          <p className="text-[10px] text-muted-foreground mb-2 truncate">{market.question}</p>

          <button
            onClick={runDebate}
            disabled={running}
            className={cn(
              "w-full py-1.5 rounded text-xs font-bold border mb-2",
              running
                ? "bg-muted text-muted-foreground border-border animate-pulse"
                : "bg-poly-purple/20 text-poly-purple border-poly-purple/40 hover:bg-poly-purple/30"
            )}
          >
            {running ? "DELIBERATING..." : "RUN DEBATE"}
          </button>

          {results && (
            <>
              <div className="space-y-1 max-h-48 overflow-y-auto mb-2">
                {results.map((a, i) => (
                  <div
                    key={i}
                    className={cn(
                      "flex items-start gap-2 p-1.5 rounded bg-muted/20 border-l-2",
                      AGENT_COLORS[a.role] || "border-gray-500"
                    )}
                  >
                    <div className="w-5 h-5 rounded-full bg-muted flex items-center justify-center text-[9px] font-bold shrink-0">
                      {AGENT_ICONS[a.role]}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 text-[10px]">
                        <span className="font-medium">{a.role}</span>
                        <span className="ml-auto font-bold">{formatPct(a.probability, 1)}</span>
                      </div>
                      <p className="text-[9px] text-muted-foreground">{a.reasoning}</p>
                    </div>
                  </div>
                ))}
              </div>

              {/* Final verdict */}
              {finalP !== null && (
                <div className="p-2 rounded bg-poly-purple/10 border border-poly-purple/30">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-bold text-poly-purple">FINAL VERDICT</span>
                    <span className="font-bold text-lg">{formatPct(finalP, 1)}</span>
                  </div>
                  <div className="flex items-center gap-2 mt-1 text-[10px]">
                    <span className="text-muted-foreground">vs Market {formatPct(market.yes_price, 1)}</span>
                    <span className="text-muted-foreground">Edge: {((finalP - market.yes_price) * 100).toFixed(1)}&cent;</span>
                    <SideBadge side={finalP > market.yes_price ? "YES" : "NO"} />
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}
    </PanelCard>
  );
}
