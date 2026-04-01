"use client";

import { useState } from "react";
import { Bell, Shield, Cpu, Database, RefreshCw, Info, ChevronRight } from "lucide-react";
import { Colors } from "@/lib/rork-types";
import { usePortfolioStore } from "@/stores/portfolioStore";
import { killSwitch, unkill } from "@/lib/api";

function SettingRow({ icon, label, value, hasToggle, toggleValue, onToggle }: {
  icon: React.ReactNode; label: string; value?: string;
  hasToggle?: boolean; toggleValue?: boolean; onToggle?: () => void;
}) {
  return (
    <div className="flex items-center justify-between px-3.5 py-3">
      <div className="flex items-center gap-2.5">
        {icon}
        <span className="text-sm font-medium" style={{ color: Colors.textPrimary }}>{label}</span>
      </div>
      {hasToggle ? (
        <button onClick={onToggle}
          className="w-10 h-5 rounded-full relative transition-colors"
          style={{ backgroundColor: toggleValue ? Colors.cyanDim : Colors.surfaceBorder }}
        >
          <div className="w-4 h-4 rounded-full absolute top-0.5 transition-all"
            style={{ backgroundColor: toggleValue ? Colors.cyan : Colors.textTertiary, left: toggleValue ? 22 : 2 }}
          />
        </button>
      ) : (
        <div className="flex items-center gap-1.5">
          {value && <span className="text-[13px] font-mono" style={{ color: Colors.textSecondary }}>{value}</span>}
          <ChevronRight size={16} color={Colors.textTertiary} />
        </div>
      )}
    </div>
  );
}

export default function SettingsTab() {
  const stats = usePortfolioStore((s) => s.stats);
  const [signalAlerts, setSignalAlerts] = useState(true);
  const [regimeAlerts, setRegimeAlerts] = useState(true);
  const [riskAlerts, setRiskAlerts] = useState(false);

  return (
    <div className="max-w-2xl mx-auto p-4 pb-8 space-y-4">
      <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>AGENT CONFIGURATION</span>
      <div className="rounded-xl overflow-hidden" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <SettingRow icon={<Cpu size={18} color={Colors.cyan} />} label="Agent Ensemble Size" value="7 agents" />
        <div className="h-px ml-10" style={{ backgroundColor: Colors.surfaceBorder }} />
        <SettingRow icon={<RefreshCw size={18} color={Colors.amber} />} label="Scan Interval" value="45 sec" />
        <div className="h-px ml-10" style={{ backgroundColor: Colors.surfaceBorder }} />
        <SettingRow icon={<Database size={18} color={Colors.purple} />} label="Data Sources" value="6 active" />
      </div>

      <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>NOTIFICATIONS</span>
      <div className="rounded-xl overflow-hidden" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <SettingRow icon={<Bell size={18} color={Colors.cyan} />} label="Signal Alerts" hasToggle toggleValue={signalAlerts} onToggle={() => setSignalAlerts(!signalAlerts)} />
        <div className="h-px ml-10" style={{ backgroundColor: Colors.surfaceBorder }} />
        <SettingRow icon={<Bell size={18} color={Colors.amber} />} label="Regime Changes" hasToggle toggleValue={regimeAlerts} onToggle={() => setRegimeAlerts(!regimeAlerts)} />
        <div className="h-px ml-10" style={{ backgroundColor: Colors.surfaceBorder }} />
        <SettingRow icon={<Bell size={18} color={Colors.coral} />} label="Risk Warnings" hasToggle toggleValue={riskAlerts} onToggle={() => setRiskAlerts(!riskAlerts)} />
      </div>

      <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>RISK MANAGEMENT</span>
      <div className="rounded-xl overflow-hidden" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <SettingRow icon={<Shield size={18} color={Colors.coral} />} label="Max Position Size" value="$150" />
        <div className="h-px ml-10" style={{ backgroundColor: Colors.surfaceBorder }} />
        <SettingRow icon={<Shield size={18} color={Colors.amber} />} label="Min Opportunity Score" value="40" />
      </div>

      <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>EMERGENCY</span>
      <div className="flex gap-2">
        <button onClick={async () => { try { await killSwitch(); } catch {} }}
          className="flex-1 py-3 rounded-xl text-xs font-bold font-mono tracking-wider"
          style={{ backgroundColor: Colors.coralDim, color: Colors.coral, border: `1px solid rgba(255,59,92,0.3)` }}>
          KILL SWITCH
        </button>
        <button onClick={async () => { try { await unkill(); } catch {} }}
          className="flex-1 py-3 rounded-xl text-xs font-bold font-mono tracking-wider"
          style={{ backgroundColor: Colors.greenDim, color: Colors.green, border: `1px solid rgba(0,214,143,0.3)` }}>
          RESUME
        </button>
      </div>

      <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>ABOUT</span>
      <div className="rounded-xl overflow-hidden" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <SettingRow icon={<Info size={18} color={Colors.textSecondary} />} label="Version" value="1.0.0" />
      </div>

      <div className="text-center mt-7 space-y-1">
        <p className="text-xs font-semibold" style={{ color: Colors.textTertiary }}>MetaPoly — Prediction Market Intelligence</p>
        <p className="text-[10px] font-mono" style={{ color: Colors.textTertiary }}>
          {stats.paper_trading ? "Research mode only. No real money at risk." : "LIVE MODE."}
        </p>
      </div>
    </div>
  );
}
