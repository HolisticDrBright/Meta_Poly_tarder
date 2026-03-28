"use client";

import { PanelCard, PanelHeader } from "../shared/AlertFeed";
import { useSignalStore, type JetEvent } from "@/stores/signalStore";
import { cn, timeAgo } from "@/lib/utils";

const strengthColors: Record<string, string> = {
  strong: "text-poly-green bg-poly-green/10",
  moderate: "text-poly-amber bg-poly-amber/10",
  weak: "text-gray-400 bg-gray-500/10",
};

function SignalStrengthDot({ strength }: { strength: string }) {
  return (
    <span
      className={cn(
        "inline-block w-2 h-2 rounded-full",
        strength === "strong" && "bg-poly-green animate-pulse-glow",
        strength === "moderate" && "bg-poly-amber",
        strength === "weak" && "bg-gray-500"
      )}
    />
  );
}

export default function JetTracker() {
  const jetEvents = useSignalStore((s) => s.jetEvents);

  // Demo data
  const events: JetEvent[] = jetEvents.length
    ? jetEvents
    : [
        {
          target_name: "Pharma CEO A",
          role: "CEO",
          tail: "N000AA",
          from_location: "Teterboro, NJ",
          to_poi: "FDA White Oak",
          distance_nm: 8.2,
          signal_strength: "strong",
          market_tags: ["pharma", "fda"],
          timestamp: new Date(Date.now() - 3600000).toISOString(),
        },
        {
          target_name: "Tech Exec B",
          role: "CFO",
          tail: "N000BB",
          from_location: "San Jose, CA",
          to_poi: "SEC HQ",
          distance_nm: 22.5,
          signal_strength: "moderate",
          market_tags: ["regulation", "sec"],
          timestamp: new Date(Date.now() - 7200000).toISOString(),
        },
      ];

  return (
    <PanelCard>
      <PanelHeader title="JET TRACKER" subtitle="Private flight intelligence" refreshInterval={60} status="live" />
      <div className="overflow-auto max-h-[calc(100%-40px)]">
        <table className="w-full text-[10px]">
          <thead className="sticky top-0 bg-card">
            <tr className="text-muted-foreground text-left">
              <th className="py-1">Target</th>
              <th className="py-1">Tail</th>
              <th className="py-1">From</th>
              <th className="py-1">To POI</th>
              <th className="py-1 text-right">Dist</th>
              <th className="py-1">Signal</th>
              <th className="py-1 text-right">When</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={i} className="border-b border-border/30 hover:bg-muted/30">
                <td className="py-1">
                  <span className="font-medium">{e.target_name}</span>
                  <span className="text-muted-foreground ml-1">({e.role})</span>
                </td>
                <td className="py-1 font-mono text-muted-foreground">{e.tail}</td>
                <td className="py-1">{e.from_location}</td>
                <td className="py-1 font-medium">{e.to_poi}</td>
                <td className="py-1 text-right">{e.distance_nm.toFixed(1)}nm</td>
                <td className="py-1">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-medium",
                      strengthColors[e.signal_strength]
                    )}
                  >
                    <SignalStrengthDot strength={e.signal_strength} />
                    {e.signal_strength.toUpperCase()}
                  </span>
                </td>
                <td className="py-1 text-right text-muted-foreground">
                  {timeAgo(new Date(e.timestamp))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {events.length === 0 && (
          <div className="text-center text-muted-foreground text-sm py-6">No active flights</div>
        )}
      </div>
      <div className="mt-1 text-[9px] text-muted-foreground">
        Tags: {events.flatMap((e) => e.market_tags).filter((v, i, a) => a.indexOf(v) === i).join(", ") || "—"}
      </div>
    </PanelCard>
  );
}
