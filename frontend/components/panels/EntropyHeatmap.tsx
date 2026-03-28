"use client";

import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { EdgeBadge, SideBadge } from "../shared/SignalBadge";
import { useMarketStore, type Market } from "@/stores/marketStore";
import { cn, formatPct, formatBits } from "@/lib/utils";

function computeEntropy(p: number): number {
  if (p <= 0 || p >= 1) return 0;
  return -(p * Math.log2(p) + (1 - p) * Math.log2(1 - p));
}

function computeKL(modelP: number, marketP: number): number {
  const p = Math.max(1e-12, Math.min(1 - 1e-12, modelP));
  const m = Math.max(1e-12, Math.min(1 - 1e-12, marketP));
  return p * Math.log2(p / m) + (1 - p) * Math.log2((1 - p) / (1 - m));
}

function kellyF(modelP: number, marketP: number): number {
  const b = 1 / marketP - 1;
  if (b <= 0) return 0;
  return (modelP * b - (1 - modelP)) / b;
}

function simpleModel(price: number): number {
  const nudge = (0.5 - price) * 0.16;
  return Math.max(0.05, Math.min(0.95, price + nudge));
}

function edgeStrength(kl: number): string {
  if (kl > 0.15) return "strong";
  if (kl > 0.08) return "moderate";
  if (kl > 0.02) return "weak";
  return "none";
}

export default function EntropyHeatmap() {
  const markets = useMarketStore((s) => s.markets);
  const selectMarket = useMarketStore((s) => s.selectMarket);

  const scored = markets
    .filter((m) => m.yes_price > 0.02 && m.yes_price < 0.98)
    .map((m) => {
      const modelP = simpleModel(m.yes_price);
      const h = computeEntropy(m.yes_price);
      const kl = computeKL(modelP, m.yes_price);
      const f = kellyF(modelP, m.yes_price);
      const action = modelP > m.yes_price ? "BUY_YES" : modelP < m.yes_price ? "BUY_NO" : "HOLD";
      return { market: m, modelP, h, kl, f, fq: f * 0.25, action, edge: edgeStrength(kl) };
    })
    .sort((a, b) => b.kl - a.kl)
    .slice(0, 50);

  const rowColor = (kl: number) => {
    if (kl > 0.15) return "bg-poly-red/5";
    if (kl > 0.08) return "bg-poly-amber/5";
    return "";
  };

  return (
    <PanelCard>
      <PanelHeader title="ENTROPY HEATMAP" subtitle="Top 50 by KL divergence" refreshInterval={60} status="live" />
      <div className="overflow-auto max-h-[calc(100%-40px)]">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-card">
            <tr className="text-muted-foreground text-left">
              <th className="py-1 pr-2">Market</th>
              <th className="py-1 px-1 text-right">Price</th>
              <th className="py-1 px-1 text-right">H(p)</th>
              <th className="py-1 px-1 text-right">Model</th>
              <th className="py-1 px-1 text-right">KL</th>
              <th className="py-1 px-1 text-right">f*</th>
              <th className="py-1 px-1">Edge</th>
              <th className="py-1 px-1">Action</th>
            </tr>
          </thead>
          <tbody>
            {scored.map(({ market, modelP, h, kl, f, fq, action, edge }, i) => (
              <tr
                key={market.id}
                onClick={() => selectMarket(market.id)}
                className={cn(
                  "cursor-pointer hover:bg-muted/40 border-b border-border/30",
                  rowColor(kl)
                )}
              >
                <td className="py-1 pr-2 max-w-[200px] truncate" title={market.question}>
                  {market.question}
                </td>
                <td className="py-1 px-1 text-right font-medium">
                  {formatPct(market.yes_price, 0)}
                </td>
                <td className="py-1 px-1 text-right text-muted-foreground">
                  {h.toFixed(3)}
                </td>
                <td className="py-1 px-1 text-right text-poly-blue">
                  {formatPct(modelP, 0)}
                </td>
                <td className="py-1 px-1 text-right font-medium">
                  {kl.toFixed(4)}
                </td>
                <td className="py-1 px-1 text-right text-muted-foreground">
                  {fq.toFixed(3)}
                </td>
                <td className="py-1 px-1">
                  <EdgeBadge strength={edge} />
                </td>
                <td className="py-1 px-1">
                  {action !== "HOLD" && (
                    <SideBadge side={action === "BUY_YES" ? "YES" : "NO"} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {scored.length === 0 && (
          <div className="text-center text-muted-foreground text-sm py-8">
            Loading markets...
          </div>
        )}
      </div>
    </PanelCard>
  );
}
