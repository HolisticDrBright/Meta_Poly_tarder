"use client";

import { useState, useMemo } from "react";
import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { useMarketStore } from "@/stores/marketStore";
import { cn, formatUSD, formatPct } from "@/lib/utils";

function entropy(p: number): number {
  if (p <= 0 || p >= 1) return 0;
  return -(p * Math.log2(p) + (1 - p) * Math.log2(1 - p));
}

function klDiv(modelP: number, marketP: number): number {
  const p = Math.max(1e-12, Math.min(1 - 1e-12, modelP));
  const m = Math.max(1e-12, Math.min(1 - 1e-12, marketP));
  return p * Math.log2(p / m) + (1 - p) * Math.log2((1 - p) / (1 - m));
}

function kellyF(modelP: number, marketP: number): number {
  const b = 1 / marketP - 1;
  if (b <= 0) return 0;
  return (modelP * b - (1 - modelP)) / b;
}

export default function KellyCalc() {
  const market = useMarketStore((s) => s.selectedMarket);
  const [modelP, setModelP] = useState(0.58);
  const [bankroll, setBankroll] = useState(10000);
  const marketPrice = market?.yes_price ?? 0.35;

  const calc = useMemo(() => {
    const h = entropy(marketPrice);
    const kl = klDiv(modelP, marketPrice);
    const f = kellyF(modelP, marketPrice);
    const fq = f * 0.25;
    const bet = Math.abs(fq) * bankroll;
    const b = 1 / marketPrice - 1;
    return { h, kl, f, fq, bet, b };
  }, [modelP, marketPrice, bankroll]);

  return (
    <PanelCard>
      <PanelHeader title="KELLY CALCULATOR" subtitle="Interactive sizing" />

      {/* Inputs */}
      <div className="space-y-2 mb-3">
        <div>
          <label className="text-[10px] text-muted-foreground flex items-center justify-between">
            <span>Your probability estimate</span>
            <span className="font-bold text-poly-blue">{formatPct(modelP, 1)}</span>
          </label>
          <input
            type="range"
            min={0.01}
            max={0.99}
            step={0.01}
            value={modelP}
            onChange={(e) => setModelP(parseFloat(e.target.value))}
            className="w-full h-1 bg-muted rounded-full appearance-none cursor-pointer accent-poly-blue"
          />
        </div>
        <div>
          <label className="text-[10px] text-muted-foreground flex items-center justify-between">
            <span>Bankroll</span>
            <span className="font-bold">{formatUSD(bankroll)}</span>
          </label>
          <input
            type="range"
            min={100}
            max={100000}
            step={100}
            value={bankroll}
            onChange={(e) => setBankroll(parseFloat(e.target.value))}
            className="w-full h-1 bg-muted rounded-full appearance-none cursor-pointer accent-poly-teal"
          />
        </div>
      </div>

      {/* Math display */}
      <div className="bg-muted/30 rounded p-2 text-[10px] font-mono space-y-1 border border-border">
        <div className="text-muted-foreground">
          Market: <span className="text-foreground font-bold">{market?.question?.slice(0, 50) || "Select a market"}</span>
        </div>
        <div className="border-t border-border/50 pt-1 mt-1" />

        <div className="flex justify-between">
          <span className="text-muted-foreground">Market price (m)</span>
          <span>{marketPrice.toFixed(3)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Your estimate (p)</span>
          <span className="text-poly-blue">{modelP.toFixed(3)}</span>
        </div>
        <div className="border-t border-border/50 pt-1 mt-1" />

        <div className="flex justify-between">
          <span className="text-muted-foreground">Shannon entropy H(m)</span>
          <span>{calc.h.toFixed(4)} bits</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">KL divergence D_KL</span>
          <span className={cn("font-bold", calc.kl > 0.08 ? "text-poly-green" : "text-muted-foreground")}>
            {calc.kl.toFixed(4)} bits
          </span>
        </div>
        <div className="border-t border-border/50 pt-1 mt-1" />

        <div className="flex justify-between">
          <span className="text-muted-foreground">Kelly fraction f*</span>
          <span>{calc.f.toFixed(4)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Quarter-Kelly f/4</span>
          <span className="font-bold">{calc.fq.toFixed(4)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Bet size</span>
          <span className="font-bold text-poly-green">{formatUSD(calc.bet)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">% of bankroll</span>
          <span>{(Math.abs(calc.fq) * 100).toFixed(1)}%</span>
        </div>
      </div>
    </PanelCard>
  );
}
