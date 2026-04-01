"use client";

import { BookOpen, Clock, ArrowUpRight, ArrowDownRight, Minus } from "lucide-react";
import { Colors } from "@/lib/rork-types";
import { useSignalStore } from "@/stores/signalStore";
import { useMemo } from "react";

function getDirectionIcon(d?: string) {
  if (d === "bullish") return <ArrowUpRight size={14} color={Colors.cyan} />;
  if (d === "bearish") return <ArrowDownRight size={14} color={Colors.coral} />;
  return <Minus size={14} color={Colors.textTertiary} />;
}

function getTypeColor(t: string) {
  if (t === "trade" || t === "copy" || t === "arb") return Colors.cyan;
  if (t === "signal" || t === "entropy" || t === "theta") return Colors.amber;
  if (t === "regime" || t === "jet") return Colors.purple;
  return Colors.textSecondary;
}

export default function JournalTab() {
  const signals = useSignalStore((s) => s.signals);

  const entries = useMemo(() => {
    if (signals.length === 0) {
      // Fallback journal entries when no real signals
      return [
        { id: "1", timestamp: new Date().toISOString(), type: "regime", title: "System Started", description: "Polymarket Intelligence System initialized. All 7 strategies active.", direction: "neutral" },
        { id: "2", timestamp: new Date().toISOString(), type: "note", title: "Monitoring Active", description: "Scanning markets for opportunities. Waiting for signals.", direction: "neutral" },
      ];
    }
    return signals.slice(0, 20).map((s: any) => ({
      id: s.id || Math.random().toString(),
      timestamp: s.timestamp || new Date().toISOString(),
      type: s.strategy || "signal",
      title: `${(s.strategy || "signal").toUpperCase()}: ${s.question || s.market_id || "Signal"}`,
      description: s.reason || `${s.side} $${(s.size_usdc || 0).toFixed(0)} @ ${(s.price || 0).toFixed(3)} — confidence ${((s.confidence || 0) * 100).toFixed(0)}%`,
      direction: s.side === "YES" ? "bullish" : s.side === "NO" ? "bearish" : "neutral",
    }));
  }, [signals]);

  return (
    <div className="max-w-2xl mx-auto p-4 pb-8 space-y-3">
      {/* Header */}
      <div className="flex items-center gap-3 rounded-xl p-3.5" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <BookOpen size={20} color={Colors.cyan} />
        <div>
          <p className="text-[15px] font-bold" style={{ color: Colors.textPrimary }}>Research Journal</p>
          <p className="text-[11px]" style={{ color: Colors.textTertiary }}>AI agent activity log & decision audit trail</p>
        </div>
      </div>

      {/* Timeline */}
      {entries.map((entry, i) => (
        <div key={entry.id} className="flex gap-3">
          {/* Timeline dot + line */}
          <div className="flex flex-col items-center" style={{ width: 16 }}>
            <div className="w-2.5 h-2.5 rounded-full mt-1" style={{ backgroundColor: getTypeColor(entry.type) }} />
            {i < entries.length - 1 && <div className="flex-1 w-px my-0.5" style={{ backgroundColor: Colors.surfaceBorder }} />}
          </div>
          {/* Card */}
          <div className="flex-1 rounded-xl p-3 mb-2 space-y-1.5" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
            <div className="flex justify-between items-center">
              <span className="text-[8px] font-bold font-mono tracking-wider px-1.5 py-0.5 rounded" style={{ color: getTypeColor(entry.type), backgroundColor: getTypeColor(entry.type) + "1A" }}>
                {entry.type.toUpperCase()}
              </span>
              {getDirectionIcon(entry.direction)}
            </div>
            <p className="text-[13px] font-semibold leading-tight" style={{ color: Colors.textPrimary }}>{entry.title}</p>
            <p className="text-[11px] leading-4" style={{ color: Colors.textSecondary }}>{entry.description}</p>
            <div className="flex items-center gap-1 mt-0.5">
              <Clock size={10} color={Colors.textTertiary} />
              <span className="text-[10px] font-mono" style={{ color: Colors.textTertiary }}>
                {new Date(entry.timestamp).toLocaleString()}
              </span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
