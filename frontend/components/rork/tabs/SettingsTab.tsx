"use client";

import { useState, useEffect } from "react";
import { Bell, Shield, Cpu, Database, RefreshCw, Info, ChevronRight, Zap, AlertTriangle, Power } from "lucide-react";
import { Colors } from "@/lib/rork-types";
import { usePortfolioStore } from "@/stores/portfolioStore";
import { setExecutionMode, getExecutionStatus, executionKill, executionResume } from "@/lib/api";

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

  // Execution mode state
  const [execMode, setExecMode] = useState<"paper" | "live">("paper");
  const [isKilled, setIsKilled] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [dailyStats, setDailyStats] = useState<any>(null);
  const [statusMsg, setStatusMsg] = useState("");

  // Fetch execution status on mount
  useEffect(() => {
    getExecutionStatus().then((d) => {
      if (d?.mode) setExecMode(d.mode);
      if (d?.kill_switch !== undefined) setIsKilled(d.kill_switch);
      if (d?.daily_stats) setDailyStats(d.daily_stats);
    }).catch(() => {});
  }, []);

  const handleModeToggle = async () => {
    if (execMode === "paper") {
      setShowConfirm(true);
    } else {
      await handleModeToggleToPaper();
    }
  };

  const confirmGoLive = async () => {
    setStatusMsg("Switching to LIVE...");
    try {
      const resp = await setExecutionMode("live");
      if (resp) {
        setExecMode("live");
        // Update the portfolio store so header updates immediately
        usePortfolioStore.setState({
          stats: { ...stats, paper_trading: false, balance: resp.live_balance || stats.balance }
        });
        setStatusMsg("LIVE MODE ACTIVE");
      } else {
        setStatusMsg("Failed — no response");
      }
    } catch (e: any) {
      setStatusMsg(`Failed: ${e?.message || "unknown error"}`);
    }
    setShowConfirm(false);
  };

  const handleModeToggleToPaper = async () => {
    setStatusMsg("Switching to PAPER...");
    try {
      const resp = await setExecutionMode("paper");
      if (resp) {
        setExecMode("paper");
        usePortfolioStore.setState({
          stats: { ...stats, paper_trading: true }
        });
        setStatusMsg("PAPER MODE ACTIVE");
      }
    } catch {}
  };

  const handleKill = async () => {
    setStatusMsg("Activating kill switch...");
    try {
      await executionKill();
      setIsKilled(true);
      setExecMode("paper");
      usePortfolioStore.setState({
        stats: { ...stats, paper_trading: true }
      });
      setStatusMsg("KILL SWITCH ACTIVATED");
    } catch {}
  };

  const handleResume = async () => {
    try {
      await executionResume();
      setIsKilled(false);
    } catch {}
  };

  return (
    <div className="max-w-2xl mx-auto p-4 pb-8 space-y-4">

      {/* ── EXECUTION MODE ── */}
      <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>EXECUTION MODE</span>
      <div className="rounded-xl overflow-hidden" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <div className="p-4 space-y-3">
          {/* Mode toggle */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Power size={20} color={execMode === "live" ? Colors.green : Colors.textTertiary} />
              <div>
                <span className="text-sm font-semibold" style={{ color: Colors.textPrimary }}>Trading Mode</span>
                <div className="text-[10px] font-mono" style={{ color: Colors.textTertiary }}>
                  {execMode === "live" ? "LIVE — Real USDC at risk" : "PAPER — Simulated trades only"}
                </div>
              </div>
            </div>
            <button onClick={handleModeToggle}
              className="px-4 py-2 rounded-lg text-xs font-bold font-mono tracking-wider transition-all"
              style={{
                backgroundColor: execMode === "live" ? Colors.greenDim : Colors.surfaceBorder,
                border: `1px solid ${execMode === "live" ? Colors.green : Colors.textTertiary}`,
                color: execMode === "live" ? Colors.green : Colors.textTertiary,
              }}>
              {execMode === "live" ? "LIVE" : "PAPER"}
            </button>
          </div>

          {/* Kill switch */}
          {isKilled && (
            <div className="p-2.5 rounded-lg" style={{ backgroundColor: "rgba(255,59,92,0.1)", border: "1px solid rgba(255,59,92,0.3)" }}>
              <div className="flex items-center gap-2">
                <AlertTriangle size={14} color={Colors.coral} />
                <span className="text-xs font-bold" style={{ color: Colors.coral }}>KILL SWITCH ACTIVE — All trading halted</span>
              </div>
            </div>
          )}

          {/* Status message */}
          {statusMsg && (
            <div className="p-2 rounded-lg text-center text-xs font-bold font-mono"
              style={{
                backgroundColor: statusMsg.includes("LIVE") ? Colors.greenDim : statusMsg.includes("FAIL") || statusMsg.includes("KILL") ? Colors.coralDim : Colors.cyanDim,
                color: statusMsg.includes("LIVE") ? Colors.green : statusMsg.includes("FAIL") || statusMsg.includes("KILL") ? Colors.coral : Colors.cyan,
              }}>
              {statusMsg}
            </div>
          )}

          {/* Daily safety stats */}
          {dailyStats && (
            <div className="grid grid-cols-2 gap-2 pt-2" style={{ borderTop: `1px solid ${Colors.surfaceBorder}` }}>
              <div className="text-center">
                <span className="text-sm font-bold font-mono" style={{ color: Colors.textPrimary }}>{dailyStats.trades_today || 0}/{dailyStats.max_trades || 50}</span>
                <div className="text-[8px] font-semibold uppercase" style={{ color: Colors.textTertiary }}>Trades Today</div>
              </div>
              <div className="text-center">
                <span className="text-sm font-bold font-mono" style={{ color: (dailyStats.daily_pnl || 0) >= 0 ? Colors.green : Colors.coral }}>
                  {(dailyStats.daily_pnl || 0) >= 0 ? "+" : ""}${Math.abs(dailyStats.daily_pnl || 0).toFixed(2)}
                </span>
                <div className="text-[8px] font-semibold uppercase" style={{ color: Colors.textTertiary }}>Daily P&L / ${dailyStats.max_daily_loss || 45} limit</div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Emergency controls */}
      <div className="flex gap-2">
        <button onClick={handleKill}
          className="flex-1 py-3 rounded-xl text-xs font-bold font-mono tracking-wider flex items-center justify-center gap-2"
          style={{ backgroundColor: Colors.coralDim, color: Colors.coral, border: `1px solid rgba(255,59,92,0.3)` }}>
          <AlertTriangle size={14} /> KILL SWITCH
        </button>
        <button onClick={handleResume}
          className="flex-1 py-3 rounded-xl text-xs font-bold font-mono tracking-wider flex items-center justify-center gap-2"
          style={{ backgroundColor: Colors.greenDim, color: Colors.green, border: `1px solid rgba(0,214,143,0.3)` }}>
          <Zap size={14} /> RESUME
        </button>
      </div>

      {/* ── Confirmation Modal ── */}
      {showConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ backgroundColor: "rgba(0,0,0,0.7)" }}>
          <div className="rounded-2xl p-6 mx-4 max-w-sm space-y-4" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
            <div className="flex items-center gap-3">
              <AlertTriangle size={24} color={Colors.amber} />
              <span className="text-lg font-bold" style={{ color: Colors.textPrimary }}>Go Live?</span>
            </div>
            <p className="text-sm leading-relaxed" style={{ color: Colors.textSecondary }}>
              You are about to enable <strong style={{ color: Colors.coral }}>live trading</strong> with real USDC.
              Starting capital: <strong>${dailyStats?.starting_capital || 300}</strong>.
              All safety guardrails remain active.
            </p>
            <div className="text-[10px] space-y-1" style={{ color: Colors.textTertiary }}>
              <div>Max trade: $30 | Max daily loss: $45</div>
              <div>Kill switch at 35% drawdown</div>
            </div>
            <div className="flex gap-2 pt-2">
              <button onClick={() => setShowConfirm(false)}
                className="flex-1 py-2.5 rounded-lg text-xs font-bold font-mono"
                style={{ backgroundColor: Colors.surfaceBorder, color: Colors.textSecondary }}>
                CANCEL
              </button>
              <button onClick={confirmGoLive}
                className="flex-1 py-2.5 rounded-lg text-xs font-bold font-mono"
                style={{ backgroundColor: Colors.coralDim, color: Colors.coral, border: `1px solid rgba(255,59,92,0.4)` }}>
                CONFIRM — GO LIVE
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── AGENT CONFIGURATION ── */}
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
        <SettingRow icon={<Shield size={18} color={Colors.coral} />} label="Max Trade Size" value="$30" />
        <div className="h-px ml-10" style={{ backgroundColor: Colors.surfaceBorder }} />
        <SettingRow icon={<Shield size={18} color={Colors.amber} />} label="Max Daily Loss" value="$45" />
        <div className="h-px ml-10" style={{ backgroundColor: Colors.surfaceBorder }} />
        <SettingRow icon={<Shield size={18} color={Colors.coral} />} label="Max Drawdown" value="35%" />
      </div>

      <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>ABOUT</span>
      <div className="rounded-xl overflow-hidden" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <SettingRow icon={<Info size={18} color={Colors.textSecondary} />} label="Version" value="1.0.0" />
      </div>

      <div className="text-center mt-7 space-y-1">
        <p className="text-xs font-semibold" style={{ color: Colors.textTertiary }}>MetaPoly — Prediction Market Intelligence</p>
        <p className="text-[10px] font-mono" style={{ color: Colors.textTertiary }}>
          {execMode === "live" ? "LIVE TRADING MODE — Real funds at risk" : "Research mode only. No real money at risk."}
        </p>
      </div>
    </div>
  );
}
