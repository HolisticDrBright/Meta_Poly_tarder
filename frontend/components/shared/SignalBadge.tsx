"use client";

import { cn } from "@/lib/utils";

type Strategy =
  | "entropy"
  | "avellaneda"
  | "arb"
  | "ensemble_ai"
  | "jet"
  | "copy"
  | "theta";
type EdgeStrength = "strong" | "moderate" | "weak" | "none";

const strategyColors: Record<Strategy, string> = {
  entropy: "bg-poly-purple/20 text-poly-purple border-poly-purple/40",
  avellaneda: "bg-poly-teal/20 text-poly-teal border-poly-teal/40",
  arb: "bg-poly-green/20 text-poly-green border-poly-green/40",
  ensemble_ai: "bg-poly-blue/20 text-poly-blue border-poly-blue/40",
  jet: "bg-poly-amber/20 text-poly-amber border-poly-amber/40",
  copy: "bg-poly-coral/20 text-poly-coral border-poly-coral/40",
  theta: "bg-gray-500/20 text-gray-400 border-gray-500/40",
};

const edgeColors: Record<EdgeStrength, string> = {
  strong: "bg-poly-green/20 text-poly-green border-poly-green/40",
  moderate: "bg-poly-amber/20 text-poly-amber border-poly-amber/40",
  weak: "bg-gray-500/20 text-gray-400 border-gray-500/40",
  none: "bg-gray-800/20 text-gray-500 border-gray-700/40",
};

export function StrategyBadge({ strategy }: { strategy: string }) {
  const colors =
    strategyColors[strategy as Strategy] || strategyColors.theta;
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border",
        colors
      )}
    >
      {strategy.replace("_", " ").toUpperCase()}
    </span>
  );
}

export function EdgeBadge({ strength }: { strength: string }) {
  const colors = edgeColors[strength as EdgeStrength] || edgeColors.none;
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border",
        colors
      )}
    >
      {strength.toUpperCase()}
    </span>
  );
}

export function SideBadge({ side }: { side: "YES" | "NO" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded text-xs font-bold border",
        side === "YES"
          ? "bg-poly-green/20 text-poly-green border-poly-green/40"
          : "bg-poly-red/20 text-poly-red border-poly-red/40"
      )}
    >
      {side}
    </span>
  );
}

export function ConfluenceBadge({ count }: { count: number }) {
  const color =
    count >= 3
      ? "bg-poly-green/20 text-poly-green border-poly-green/40"
      : count >= 2
        ? "bg-poly-amber/20 text-poly-amber border-poly-amber/40"
        : "bg-gray-500/20 text-gray-400 border-gray-500/40";
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border",
        color
      )}
    >
      {count}x CONFLUENCE
    </span>
  );
}

export function TierBadge({ tier }: { tier: string }) {
  const tierColors: Record<string, string> = {
    legendary: "bg-yellow-500/20 text-yellow-400 border-yellow-500/40",
    elite: "bg-poly-purple/20 text-poly-purple border-poly-purple/40",
    pro: "bg-poly-blue/20 text-poly-blue border-poly-blue/40",
    rising: "bg-gray-500/20 text-gray-400 border-gray-500/40",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded text-xs font-bold border",
        tierColors[tier] || tierColors.rising
      )}
    >
      {tier.toUpperCase()}
    </span>
  );
}
