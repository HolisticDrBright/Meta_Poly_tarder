"use client";

import { useState, useEffect, useCallback } from "react";
import { Bell, Shield, Cpu, Database, RefreshCw, Info, ChevronRight, Zap, AlertTriangle, Power, DollarSign, TrendingUp, Clock, Sliders } from "lucide-react";
import { Colors } from "@/lib/rork-types";
import { usePortfolioStore } from "@/stores/portfolioStore";
import { setExecutionMode, getExecutionStatus, executionKill, executionResume, fetchSettings, updateSettings } from "@/lib/api";

/* ── Reusable slider row ─────────────────────────────────────── */
function SliderRow({ label, sublabel, value, min, max, step, unit, color, onChange }: {
  label: string; sublabel?: string; value: number;
  min: number; max: number; step: number;
  unit?: string; color?: string; onChange: (v: number) => void;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="px-3.5 py-3 space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-sm font-medium" style={{ color: Colors.textPrimary }}>{label}</span>
          {sublabel && <div className="text-[9px] font-mono" style={{ color: Colors.textTertiary }}>{sublabel}</div>}
        </div>
        <span className="text-sm font-bold font-mono px-2 py-0.5 rounded" style={{ color: color || Colors.cyan, backgroundColor: (color || Colors.cyan) + "1A" }}>
          {unit === "$" ? `$${value}` : unit === "%" ? `${(value * 100).toFixed(0)}%` : unit === "h" ? `${value}h` : value}
        </span>
      </div>
      <div className="relative">
        <div className="w-full h-1.5 rounded-full" style={{ backgroundColor: Colors.surfaceBorder }}>
          <div className="h-1.5 rounded-full" style={{ width: `${pct}%`, backgroundColor: color || Colors.cyan }} />
        </div>
        <input
          type="range" min={min} max={max} step={step} value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="absolute inset-0 w-full h-6 opacity-0 cursor-pointer" style={{ top: "-6px" }}
        />
      </div>
    </div>
  );
}

/* ── Toggle row ──────────────────────────────────────────────── */
function ToggleRow({ label, sublabel, value, onChange }: {
  label: string; sublabel?: string; value: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between px-3.5 py-3">
      <div>
        <span className="text-sm font-medium" style={{ color: Colors.textPrimary }}>{label}</span>
        {sublabel && <div className="text-[9px] font-mono" style={{ color: Colors.textTertiary }}>{sublabel}</div>}
      </div>
      <button onClick={() => onChange(!value)}
        className="w-10 h-5 rounded-full relative transition-colors"
        style={{ backgroundColor: value ? Colors.cyanDim : Colors.surfaceBorder }}
      >
        <div className="w-4 h-4 rounded-full absolute top-0.5 transition-all"
          style={{ backgroundColor: value ? Colors.cyan : Colors.textTertiary, left: value ? 22 : 2 }}
        />
      </button>
    </div>
  );
}

function Divider() {
  return <div className="h-px ml-3" style={{ backgroundColor: Colors.surfaceBorder }} />;
}

/* ── Main component ──────────────────────────────────────────── */
export default function SettingsTab() {
  const stats = usePortfolioStore((s) => s.stats);

  // Execution mode
  const [execMode, setExecMode] = useState<"paper" | "live">("paper");
  const [isKilled, setIsKilled] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [dailyStats, setDailyStats] = useState<any>(null);
  const [statusMsg, setStatusMsg] = useState("");

  // Live settings from API
  const [settings, setSettings] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  // Fetch execution status + settings on mount
  useEffect(() => {
    getExecutionStatus().then((d) => {
      if (d?.mode) setExecMode(d.mode);
      if (d?.kill_switch !== undefined) setIsKilled(d.kill_switch);
      if (d?.daily_stats) setDailyStats(d.daily_stats);
    }).catch(() => {});

    fetchSettings().then(setSettings).catch(() => {});
  }, []);

  // Debounced save
  const saveField = useCallback(async (field: string, value: any) => {
    setSaving(true);
    setSaveMsg("");
    try {
      const resp = await updateSettings({ [field]: value });
      setSaveMsg(`Updated: ${resp?.updated?.join(", ") || field}`);
      setTimeout(() => setSaveMsg(""), 3000);
    } catch (e: any) {
      setSaveMsg(`Failed: ${e?.message || "error"}`);
    }
    setSaving(false);
  }, []);

  const handleSlider = (field: string, value: number) => {
    setSettings((s: any) => ({ ...s, [field]: value }));
    saveField(field, value);
  };

  const handleToggle = (field: string, value: boolean) => {
    setSettings((s: any) => ({ ...s, [field]: value }));
    saveField(field, value);
  };

  // Execution mode handlers
  const handleModeToggle = async () => {
    if (execMode === "paper") setShowConfirm(true);
    else {
      try {
        await setExecutionMode("paper");
        setExecMode("paper");
        usePortfolioStore.setState({ stats: { ...stats, paper_trading: true } });
        setStatusMsg("PAPER MODE ACTIVE");
      } catch {}
    }
  };

  const confirmGoLive = async () => {
    setStatusMsg("Switching to LIVE...");
    try {
      const resp = await setExecutionMode("live");
      if (resp) {
        setExecMode("live");
        usePortfolioStore.setState({ stats: { ...stats, paper_trading: false, balance: resp.live_balance || stats.balance } });
        setStatusMsg("LIVE MODE ACTIVE");
      }
    } catch (e: any) {
      setStatusMsg(`Failed: ${e?.message || "unknown"}`);
    }
    setShowConfirm(false);
  };

  return (
    <div className="max-w-2xl mx-auto p-4 pb-8 space-y-4">

      {/* ── EXECUTION MODE ── */}
      <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>EXECUTION MODE</span>
      <div className="rounded-xl overflow-hidden" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
        <div className="p-4 space-y-3">
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
              className="px-4 py-2 rounded-lg text-xs font-bold font-mono tracking-wider"
              style={{
                backgroundColor: execMode === "live" ? Colors.greenDim : Colors.surfaceBorder,
                border: `1px solid ${execMode === "live" ? Colors.green : Colors.textTertiary}`,
                color: execMode === "live" ? Colors.green : Colors.textTertiary,
              }}>
              {execMode === "live" ? "LIVE" : "PAPER"}
            </button>
          </div>
          {isKilled && (
            <div className="p-2.5 rounded-lg" style={{ backgroundColor: "rgba(255,59,92,0.1)", border: "1px solid rgba(255,59,92,0.3)" }}>
              <div className="flex items-center gap-2">
                <AlertTriangle size={14} color={Colors.coral} />
                <span className="text-xs font-bold" style={{ color: Colors.coral }}>KILL SWITCH ACTIVE</span>
              </div>
            </div>
          )}
          {statusMsg && (
            <div className="p-2 rounded-lg text-center text-xs font-bold font-mono"
              style={{ backgroundColor: Colors.cyanDim, color: Colors.cyan }}>
              {statusMsg}
            </div>
          )}
        </div>
      </div>

      {/* Emergency controls */}
      <div className="flex gap-2">
        <button onClick={async () => { await executionKill(); setIsKilled(true); setStatusMsg("KILL SWITCH ACTIVATED"); }}
          className="flex-1 py-3 rounded-xl text-xs font-bold font-mono tracking-wider flex items-center justify-center gap-2"
          style={{ backgroundColor: Colors.coralDim, color: Colors.coral, border: "1px solid rgba(255,59,92,0.3)" }}>
          <AlertTriangle size={14} /> KILL SWITCH
        </button>
        <button onClick={async () => { await executionResume(); setIsKilled(false); setStatusMsg("RESUMED"); }}
          className="flex-1 py-3 rounded-xl text-xs font-bold font-mono tracking-wider flex items-center justify-center gap-2"
          style={{ backgroundColor: Colors.greenDim, color: Colors.green, border: "1px solid rgba(0,214,143,0.3)" }}>
          <Zap size={14} /> RESUME
        </button>
      </div>

      {/* Save indicator */}
      {saveMsg && (
        <div className="p-2 rounded-lg text-center text-[10px] font-bold font-mono"
          style={{ backgroundColor: saveMsg.includes("Failed") ? Colors.coralDim : Colors.cyanDim, color: saveMsg.includes("Failed") ? Colors.coral : Colors.cyan }}>
          {saving ? "Saving..." : saveMsg}
        </div>
      )}

      {settings && (
        <>
          {/* ── POSITION SIZING ── */}
          <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>POSITION SIZING</span>
          <div className="rounded-xl overflow-hidden" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
            <SliderRow label="Trade Size" sublabel="USDC per trade" value={settings.max_trade_size_usdc} min={1} max={50} step={1} unit="$" color={Colors.cyan} onChange={(v) => handleSlider("max_trade_size_usdc", v)} />
            <Divider />
            <SliderRow label="Max Per Market" sublabel="% of bankroll per market" value={settings.max_single_market_pct} min={0.02} max={0.30} step={0.01} unit="%" color={Colors.cyan} onChange={(v) => handleSlider("max_single_market_pct", v)} />
            <Divider />
            <SliderRow label="Portfolio Exposure" sublabel="Total capital deployed" value={settings.max_portfolio_exposure} min={0.20} max={0.95} step={0.05} unit="%" color={Colors.cyan} onChange={(v) => handleSlider("max_portfolio_exposure", v)} />
            <Divider />
            <SliderRow label="Daily Loss Limit" sublabel="Max daily drawdown before lockout" value={settings.max_daily_loss_pct} min={0.05} max={0.30} step={0.01} unit="%" color={Colors.amber} onChange={(v) => handleSlider("max_daily_loss_pct", v)} />
          </div>

          {/* ── RISK & EXITS ── */}
          <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>RISK & EXITS</span>
          <div className="rounded-xl overflow-hidden" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
            <SliderRow label="Stop Loss" sublabel="Close if position loses this %" value={Math.abs(settings.stop_loss_pct)} min={0.05} max={0.50} step={0.01} unit="%" color={Colors.coral} onChange={(v) => handleSlider("stop_loss_pct", -v)} />
            <Divider />
            <SliderRow label="Take Profit" sublabel="Flat take-profit (no model)" value={settings.take_profit_pct} min={0.10} max={1.00} step={0.05} unit="%" color={Colors.green} onChange={(v) => handleSlider("take_profit_pct", v)} />
            <Divider />
            <SliderRow label="Trailing Stop" sublabel="Drawdown from peak to close" value={settings.trailing_stop_pct} min={0.0} max={0.40} step={0.01} unit="%" color={Colors.amber} onChange={(v) => handleSlider("trailing_stop_pct", v)} />
            <Divider />
            <SliderRow label="Edge Capture" sublabel="% of model edge to capture before selling" value={settings.edge_capture_pct} min={0.20} max={1.00} step={0.05} unit="%" color={Colors.cyan} onChange={(v) => handleSlider("edge_capture_pct", v)} />
          </div>

          {/* ── TIMING ── */}
          <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>EXIT TIMING</span>
          <div className="rounded-xl overflow-hidden" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
            <SliderRow label="Full Target Hold" sublabel="Hours to hold for full edge capture" value={settings.age_hours_full_target} min={0.5} max={12} step={0.5} unit="h" color={Colors.cyan} onChange={(v) => handleSlider("age_hours_full_target", v)} />
            <Divider />
            <SliderRow label="Min Profit Age" sublabel="Hours before accepting min profit exit" value={settings.age_hours_min_target} min={2} max={72} step={1} unit="h" color={Colors.cyan} onChange={(v) => handleSlider("age_hours_min_target", v)} />
            <Divider />
            <SliderRow label="Max Position Age" sublabel="Unconditional close (zombie prevention)" value={settings.max_age_hours} min={12} max={168} step={6} unit="h" color={Colors.amber} onChange={(v) => handleSlider("max_age_hours", v)} />
          </div>

          {/* ── STRATEGIES ── */}
          <span className="text-[11px] font-bold font-mono tracking-widest block" style={{ color: Colors.textTertiary }}>STRATEGIES</span>
          <div className="rounded-xl overflow-hidden" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
            <ToggleRow label="Avellaneda-Stoikov" sublabel="Market making — captures bid/ask spread" value={settings.avellaneda_enabled} onChange={(v) => handleToggle("avellaneda_enabled", v)} />
            <Divider />
            <ToggleRow label="Entropy Screener" sublabel="Directional bets on mispriced markets" value={settings.entropy_enabled} onChange={(v) => handleToggle("entropy_enabled", v)} />
            <Divider />
            <ToggleRow label="Theta Harvester" sublabel="Profits from time decay near resolution" value={settings.theta_enabled} onChange={(v) => handleToggle("theta_enabled", v)} />
            <Divider />
            <ToggleRow label="AI Ensemble" sublabel="Claude + GPT-4o probability estimation" value={settings.ensemble_enabled} onChange={(v) => handleToggle("ensemble_enabled", v)} />
            <Divider />
            <ToggleRow label="Binance Arb" sublabel="Crypto price arbitrage vs Polymarket" value={settings.binance_arb_enabled} onChange={(v) => handleToggle("binance_arb_enabled", v)} />
          </div>
        </>
      )}

      {!settings && (
        <div className="rounded-xl p-6 text-center" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
          <RefreshCw size={20} color={Colors.textTertiary} className="mx-auto mb-2 animate-spin" />
          <span className="text-sm font-mono" style={{ color: Colors.textTertiary }}>Loading settings...</span>
        </div>
      )}

      {/* ── Confirmation Modal ── */}
      {showConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ backgroundColor: "rgba(0,0,0,0.7)" }}>
          <div className="rounded-2xl p-6 mx-4 max-w-sm space-y-4" style={{ backgroundColor: Colors.card, border: `1px solid ${Colors.cardBorder}` }}>
            <div className="flex items-center gap-3">
              <AlertTriangle size={24} color={Colors.coral} />
              <span className="text-lg font-bold" style={{ color: Colors.textPrimary }}>Go Live?</span>
            </div>
            <p className="text-sm leading-relaxed" style={{ color: Colors.textSecondary }}>
              Enable <strong style={{ color: Colors.coral }}>live trading</strong> with real USDC.
              All safety guardrails remain active.
            </p>
            <div className="flex gap-2 pt-2">
              <button onClick={() => setShowConfirm(false)}
                className="flex-1 py-2.5 rounded-lg text-xs font-bold font-mono"
                style={{ backgroundColor: Colors.surfaceBorder, color: Colors.textSecondary }}>CANCEL</button>
              <button onClick={confirmGoLive}
                className="flex-1 py-2.5 rounded-lg text-xs font-bold font-mono"
                style={{ backgroundColor: Colors.coralDim, color: Colors.coral, border: "1px solid rgba(255,59,92,0.4)" }}>CONFIRM — GO LIVE</button>
            </div>
          </div>
        </div>
      )}

      <div className="text-center mt-7 space-y-1">
        <p className="text-xs font-semibold" style={{ color: Colors.textTertiary }}>MetaPoly — Prediction Market Intelligence</p>
        <p className="text-[10px] font-mono" style={{ color: Colors.textTertiary }}>
          {execMode === "live" ? "LIVE TRADING MODE — Real funds at risk" : "Research mode only. No real money at risk."}
        </p>
      </div>
    </div>
  );
}
