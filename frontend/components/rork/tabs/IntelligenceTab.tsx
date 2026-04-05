"use client";

import { useEffect, useState } from "react";
import {
  Brain,
  AlertTriangle,
  RefreshCw,
  Sliders,
  Activity,
} from "lucide-react";
import { Colors } from "@/lib/rork-types";

// ── API response shapes ─────────────────────────────────────

interface HealthReport {
  total_decisions?: number;
  scored_outcomes?: number;
  latest_brier?: number | null;
  optimization_active?: boolean;
  note?: string;
}

interface PerformanceSummary {
  total_decisions?: number;
  scored_outcomes?: number;
  overall_brier?: number | null;
  optimization_active?: boolean;
}

interface SignalAttribution {
  signal_attribution?: Record<string, number>;
  weight_recommendations?: Record<string, number>;
  status?: string;
}

interface CalibrationBucket {
  bucket: number;
  count: number;
  actual_freq: number;
  avg_brier: number;
}

interface CalibrationResponse {
  buckets?: CalibrationBucket[];
  overall_brier?: number;
  status?: string;
}

interface GroupPerf {
  group: string;
  count: number;
  avg_brier: number;
  avg_edge?: number;
  avg_pnl?: number;
  hit_rate?: number;
}

interface ErrorsReport {
  error_counts?: Record<string, number>;
  top_errors?: string[];
  status?: string;
}

interface Proposal {
  proposal_id: string;
  timestamp: string;
  current_weights: Record<string, number>;
  proposed_weights: Record<string, number>;
  weight_deltas: Record<string, number>;
  confidence_level?: string;
  sample_size?: number;
  supporting_evidence?: string[];
  requires_human_review?: boolean;
}

interface WeightsResponse {
  weights?: Record<string, number>;
  thresholds?: Record<string, number>;
}

// ── Helpers ──────────────────────────────────────────────────

async function safeJson<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(10000) });
    if (!r.ok) return null;
    const raw = await r.text();
    const cleaned = raw
      .replace(/:\s*Infinity/g, ": null")
      .replace(/:\s*-Infinity/g, ": null")
      .replace(/:\s*NaN/g, ": null");
    return JSON.parse(cleaned) as T;
  } catch {
    return null;
  }
}

// Coerce ANY input (number, string, null, undefined, NaN, Infinity) to a
// safe finite number or null. Prevents the whole tab from crashing when
// the backend returns Decimals-as-strings or unexpected NaN/Infinity.
function toSafeNum(v: any): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return null;
  return n;
}

function fmtNumber(v: any, digits = 3): string {
  const n = toSafeNum(v);
  if (n === null) return "—";
  return n.toFixed(digits);
}

function fmtPct(v: any, digits = 1): string {
  const n = toSafeNum(v);
  if (n === null) return "—";
  return `${n.toFixed(digits)}%`;
}

function brierColor(brier: any): string {
  const n = toSafeNum(brier);
  if (n === null) return Colors.textTertiary;
  // Lower is better. 0 = perfect, 0.25 = chance, >0.25 = worse than chance.
  if (n <= 0.15) return Colors.green;
  if (n <= 0.22) return Colors.amber;
  return Colors.coral;
}

function deltaColor(delta: number): string {
  if (delta > 0) return Colors.green;
  if (delta < 0) return Colors.coral;
  return Colors.textTertiary;
}

// ── Component ────────────────────────────────────────────────

export default function IntelligenceTab() {
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [summary, setSummary] = useState<PerformanceSummary | null>(null);
  const [signals, setSignals] = useState<SignalAttribution | null>(null);
  const [calibration, setCalibration] = useState<CalibrationResponse | null>(null);
  const [themes, setThemes] = useState<GroupPerf[]>([]);
  const [regimes, setRegimes] = useState<GroupPerf[]>([]);
  const [errors, setErrors] = useState<ErrorsReport | null>(null);
  const [weights, setWeights] = useState<WeightsResponse | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [message, setMessage] = useState<string>("");
  const [triggering, setTriggering] = useState(false);

  async function loadAll() {
    setLoading(true);
    const [
      healthR,
      sumR,
      sigR,
      calR,
      themeR,
      regimeR,
      errR,
      weightR,
      propR,
    ] = await Promise.all([
      safeJson<HealthReport>("/api/v1/intelligence/health"),
      safeJson<PerformanceSummary>("/api/v1/intelligence/performance/summary"),
      safeJson<SignalAttribution>("/api/v1/intelligence/performance/signals"),
      safeJson<CalibrationResponse>("/api/v1/intelligence/calibration"),
      safeJson<{ themes?: GroupPerf[]; status?: string }>(
        "/api/v1/intelligence/calibration/by-theme"
      ),
      safeJson<{ regimes?: GroupPerf[]; status?: string }>(
        "/api/v1/intelligence/calibration/by-regime"
      ),
      safeJson<ErrorsReport>("/api/v1/intelligence/performance/errors"),
      safeJson<WeightsResponse>("/api/v1/intelligence/weights/current"),
      safeJson<{ proposals?: Proposal[] }>("/api/v1/intelligence/weights/proposals"),
    ]);

    setHealth(healthR);
    setSummary(sumR);
    setSignals(sigR);
    setCalibration(calR);
    setThemes(themeR?.themes || []);
    setRegimes(regimeR?.regimes || []);
    setErrors(errR);
    setWeights(weightR);
    setProposals(propR?.proposals || []);
    setLoading(false);
  }

  useEffect(() => {
    loadAll();
    const id = setInterval(loadAll, 60000); // refresh every 60s
    return () => clearInterval(id);
  }, []);

  async function triggerAnalysis() {
    setTriggering(true);
    setMessage("");
    try {
      const r = await fetch("/api/v1/intelligence/analysis/trigger", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      // Parse defensively — if backend returns HTML (500), sanitise it
      const raw = await r.text();
      let d: any = {};
      try {
        d = JSON.parse(
          raw
            .replace(/:\s*Infinity/g, ": null")
            .replace(/:\s*-Infinity/g, ": null")
            .replace(/:\s*NaN/g, ": null")
        );
      } catch {
        setMessage(`Server returned non-JSON (HTTP ${r.status})`);
        return;
      }
      if (d.status === "analysis complete") {
        const brier = fmtNumber(d.overall_brier, 4);
        const scored = toSafeNum(d.scored_outcomes) ?? 0;
        setMessage(`Analysis complete — Brier ${brier} over ${scored} outcomes`);
        await loadAll();
      } else if (d.status === "error") {
        setMessage(`Server error: ${d.error || "unknown"}`);
      } else if (d.status === "not enough data") {
        const scored = toSafeNum(d.scored_outcomes) ?? 0;
        const minimum = toSafeNum(d.minimum) ?? 10;
        setMessage(`Not enough data yet (${scored} / ${minimum} graded outcomes)`);
      } else {
        setMessage(String(d.status || "Unknown response"));
      }
    } catch (e: any) {
      setMessage(`Request failed: ${e?.message || "unknown"}`);
    } finally {
      setTriggering(false);
    }
  }

  async function deployProposal(id: string) {
    setMessage("Deploying…");
    try {
      const r = await fetch(
        `/api/v1/intelligence/weights/proposals/${id}/deploy`,
        { method: "POST" }
      );
      const d = await r.json();
      setMessage(`Proposal ${d.status}`);
      await loadAll();
    } catch (e: any) {
      setMessage(`Error: ${e?.message || "unknown"}`);
    }
  }

  async function revertWeights() {
    if (!confirm("Revert to the previous weight checkpoint?")) return;
    try {
      const r = await fetch("/api/v1/intelligence/weights/revert", {
        method: "POST",
      });
      const d = await r.json();
      setMessage(`Weights ${d.status}`);
      await loadAll();
    } catch (e: any) {
      setMessage(`Error: ${e?.message || "unknown"}`);
    }
  }

  // ── Derived values ────────────────────────────────────────
  const totalDecisions =
    summary?.total_decisions ?? health?.total_decisions ?? 0;
  const scoredOutcomes =
    summary?.scored_outcomes ?? health?.scored_outcomes ?? 0;
  const overallBrier =
    summary?.overall_brier ?? health?.latest_brier ?? null;
  const optimizationActive = summary?.optimization_active ?? false;
  const needMore = Math.max(0, 50 - scoredOutcomes);

  const signalAttribution = signals?.signal_attribution || {};
  const weightRecommendations = signals?.weight_recommendations || {};
  const currentWeights = weights?.weights || {};

  // Union of all signal names across current weights + attribution
  const signalKeys = Array.from(
    new Set([
      ...Object.keys(currentWeights),
      ...Object.keys(signalAttribution),
      ...Object.keys(weightRecommendations),
    ])
  ).sort();

  return (
    <div className="max-w-2xl mx-auto space-y-3 pb-8">
      {/* Header */}
      <div
        className="flex items-center justify-between rounded-xl px-3.5 py-2.5"
        style={{
          backgroundColor: Colors.card,
          border: `1px solid ${Colors.cardBorder}`,
        }}
      >
        <div className="flex items-center gap-2">
          <Brain size={16} color={Colors.cyan} />
          <span
            className="text-[13px] font-bold tracking-wider uppercase"
            style={{ color: Colors.textPrimary }}
          >
            Prediction Intelligence
          </span>
        </div>
        <button
          onClick={loadAll}
          disabled={loading}
          className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider px-2 py-1 rounded"
          style={{
            color: Colors.cyan,
            backgroundColor: Colors.cyanDim,
            opacity: loading ? 0.5 : 1,
          }}
        >
          <RefreshCw size={10} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {/* Summary cards */}
      <div
        className="rounded-xl p-3.5"
        style={{
          backgroundColor: Colors.card,
          border: `1px solid ${Colors.cardBorder}`,
        }}
      >
        <div className="grid grid-cols-4 gap-3">
          <div className="text-center">
            <div
              className="text-[9px] font-semibold uppercase tracking-wider mb-1"
              style={{ color: Colors.textTertiary }}
            >
              Decisions
            </div>
            <div
              className="text-xl font-extrabold font-mono"
              style={{ color: Colors.textPrimary }}
            >
              {totalDecisions.toLocaleString()}
            </div>
          </div>
          <div className="text-center">
            <div
              className="text-[9px] font-semibold uppercase tracking-wider mb-1"
              style={{ color: Colors.textTertiary }}
            >
              Graded
            </div>
            <div
              className="text-xl font-extrabold font-mono"
              style={{ color: Colors.textPrimary }}
            >
              {scoredOutcomes.toLocaleString()}
            </div>
          </div>
          <div className="text-center">
            <div
              className="text-[9px] font-semibold uppercase tracking-wider mb-1"
              style={{ color: Colors.textTertiary }}
            >
              Brier
            </div>
            <div
              className="text-xl font-extrabold font-mono"
              style={{ color: brierColor(overallBrier) }}
            >
              {fmtNumber(overallBrier, 4)}
            </div>
          </div>
          <div className="text-center">
            <div
              className="text-[9px] font-semibold uppercase tracking-wider mb-1"
              style={{ color: Colors.textTertiary }}
            >
              Learning
            </div>
            <div
              className="text-xl font-extrabold font-mono"
              style={{
                color: optimizationActive ? Colors.green : Colors.amber,
              }}
            >
              {optimizationActive ? "ON" : "WARM"}
            </div>
          </div>
        </div>

        {!optimizationActive && needMore > 0 && (
          <div
            className="mt-3 pt-3 text-[11px] font-mono text-center"
            style={{
              color: Colors.textTertiary,
              borderTop: `1px solid ${Colors.surfaceBorder}`,
            }}
          >
            {needMore} more graded outcomes until the weight adjuster can
            propose changes (min 50)
          </div>
        )}

        <div className="mt-3 flex gap-2">
          <button
            onClick={triggerAnalysis}
            disabled={triggering || scoredOutcomes < 10}
            className="flex-1 py-2 rounded text-[11px] font-bold uppercase tracking-wider"
            style={{
              color: Colors.cyan,
              backgroundColor: Colors.cyanDim,
              opacity: scoredOutcomes < 10 ? 0.4 : 1,
            }}
          >
            {triggering ? "Running…" : "Trigger Analysis"}
          </button>
          <button
            onClick={revertWeights}
            className="flex-1 py-2 rounded text-[11px] font-bold uppercase tracking-wider"
            style={{ color: Colors.coral, backgroundColor: Colors.coralDim }}
          >
            Revert Weights
          </button>
        </div>

        {message && (
          <div
            className="mt-2 text-[11px] font-mono text-center"
            style={{ color: Colors.textTertiary }}
          >
            {message}
          </div>
        )}
      </div>

      {/* Signal attribution + current weights */}
      <div
        className="rounded-xl p-3.5"
        style={{
          backgroundColor: Colors.card,
          border: `1px solid ${Colors.cardBorder}`,
        }}
      >
        <div className="flex items-center gap-2 mb-3">
          <Sliders size={12} color={Colors.cyan} />
          <span
            className="text-[11px] font-bold uppercase tracking-wider"
            style={{ color: Colors.textPrimary }}
          >
            Signal Weights & Predictive Power
          </span>
        </div>

        {signalKeys.length === 0 ? (
          <div
            className="text-[11px] font-mono text-center py-4"
            style={{ color: Colors.textTertiary }}
          >
            No signal data yet — analyzer hasn't run
          </div>
        ) : (
          <div className="space-y-2">
            <div
              className="grid grid-cols-12 gap-2 text-[9px] font-bold uppercase tracking-wider pb-1"
              style={{
                color: Colors.textTertiary,
                borderBottom: `1px solid ${Colors.surfaceBorder}`,
              }}
            >
              <div className="col-span-4">Signal</div>
              <div className="col-span-2 text-right">Weight</div>
              <div className="col-span-3 text-right">Predictive</div>
              <div className="col-span-3 text-right">Suggested</div>
            </div>
            {signalKeys.map((key) => {
              const wNum = toSafeNum(currentWeights[key]) ?? 0;
              const powerNum = toSafeNum(signalAttribution[key]);
              const recNum = toSafeNum(weightRecommendations[key]);
              const deltaNum = recNum !== null ? recNum - wNum : 0;
              return (
                <div
                  key={key}
                  className="grid grid-cols-12 gap-2 text-[11px] font-mono items-center"
                >
                  <div
                    className="col-span-4 truncate"
                    style={{ color: Colors.textPrimary }}
                  >
                    {key}
                  </div>
                  <div
                    className="col-span-2 text-right font-bold"
                    style={{ color: Colors.textPrimary }}
                  >
                    {fmtNumber(wNum, 3)}
                  </div>
                  <div
                    className="col-span-3 text-right"
                    style={{
                      color:
                        powerNum === null
                          ? Colors.textTertiary
                          : powerNum > 0
                            ? Colors.green
                            : powerNum < 0
                              ? Colors.coral
                              : Colors.textTertiary,
                    }}
                  >
                    {fmtNumber(powerNum, 3)}
                  </div>
                  <div
                    className="col-span-3 text-right"
                    style={{ color: deltaColor(deltaNum) }}
                  >
                    {recNum === null
                      ? "—"
                      : `${fmtNumber(recNum, 3)} (${deltaNum >= 0 ? "+" : ""}${fmtNumber(deltaNum, 3)})`}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Calibration */}
      <div
        className="rounded-xl p-3.5"
        style={{
          backgroundColor: Colors.card,
          border: `1px solid ${Colors.cardBorder}`,
        }}
      >
        <div className="flex items-center gap-2 mb-3">
          <Activity size={12} color={Colors.cyan} />
          <span
            className="text-[11px] font-bold uppercase tracking-wider"
            style={{ color: Colors.textPrimary }}
          >
            Calibration (predicted vs actual)
          </span>
        </div>

        {!calibration?.buckets?.length ? (
          <div
            className="text-[11px] font-mono text-center py-4"
            style={{ color: Colors.textTertiary }}
          >
            No calibration data yet
          </div>
        ) : (
          <div className="space-y-1.5">
            {calibration.buckets.map((b, i) => {
              // Coerce all numeric fields — backend may send Decimal-as-string
              const bucket = toSafeNum(b.bucket) ?? 0;
              const actual = toSafeNum(b.actual_freq) ?? 0;
              const predicted = bucket + 0.05;
              const err = Math.abs(predicted - actual);
              const errColor =
                err < 0.05
                  ? Colors.green
                  : err < 0.12
                    ? Colors.amber
                    : Colors.coral;
              const barWidth = Math.min(100, Math.max(0, actual * 100));
              return (
                <div key={i} className="flex items-center gap-2 text-[10px] font-mono">
                  <div
                    className="w-16"
                    style={{ color: Colors.textTertiary }}
                  >
                    p=
                    {bucket.toFixed(1)}-{(bucket + 0.1).toFixed(1)}
                  </div>
                  <div
                    className="flex-1 h-3 rounded-sm relative"
                    style={{ backgroundColor: Colors.surfaceBorder }}
                  >
                    <div
                      className="absolute inset-y-0 left-0 rounded-sm"
                      style={{
                        width: `${barWidth}%`,
                        backgroundColor: errColor,
                      }}
                    />
                  </div>
                  <div
                    className="w-14 text-right"
                    style={{ color: Colors.textPrimary }}
                  >
                    {(actual * 100).toFixed(0)}%
                  </div>
                  <div
                    className="w-10 text-right"
                    style={{ color: Colors.textTertiary }}
                  >
                    n={b.count}
                  </div>
                </div>
              );
            })}
            <div
              className="text-[9px] font-mono text-center pt-2"
              style={{ color: Colors.textTertiary }}
            >
              Well-calibrated = each bar matches its "p=" range.
              Green bars are within 5%, red &gt; 12%.
            </div>
          </div>
        )}
      </div>

      {/* Theme performance */}
      {themes.length > 0 && (
        <div
          className="rounded-xl p-3.5"
          style={{
            backgroundColor: Colors.card,
            border: `1px solid ${Colors.cardBorder}`,
          }}
        >
          <div
            className="text-[11px] font-bold uppercase tracking-wider mb-3"
            style={{ color: Colors.textPrimary }}
          >
            Performance by Theme
          </div>
          <div className="space-y-1.5">
            {themes.slice(0, 10).map((t) => (
              <GroupRow key={t.group} row={t} />
            ))}
          </div>
        </div>
      )}

      {/* Regime performance */}
      {regimes.length > 0 && (
        <div
          className="rounded-xl p-3.5"
          style={{
            backgroundColor: Colors.card,
            border: `1px solid ${Colors.cardBorder}`,
          }}
        >
          <div
            className="text-[11px] font-bold uppercase tracking-wider mb-3"
            style={{ color: Colors.textPrimary }}
          >
            Performance by Regime
          </div>
          <div className="space-y-1.5">
            {regimes.map((r) => (
              <GroupRow key={r.group} row={r} />
            ))}
          </div>
        </div>
      )}

      {/* Top errors */}
      {errors && (errors.top_errors?.length || Object.keys(errors.error_counts || {}).length > 0) && (
        <div
          className="rounded-xl p-3.5"
          style={{
            backgroundColor: Colors.card,
            border: `1px solid ${Colors.cardBorder}`,
          }}
        >
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle size={12} color={Colors.coral} />
            <span
              className="text-[11px] font-bold uppercase tracking-wider"
              style={{ color: Colors.textPrimary }}
            >
              Top Failure Modes
            </span>
          </div>
          {errors.top_errors && errors.top_errors.length > 0 ? (
            <div className="space-y-1.5">
              {errors.top_errors.slice(0, 6).map((e, i) => (
                <div
                  key={i}
                  className="text-[11px] font-mono flex items-start gap-2"
                >
                  <span style={{ color: Colors.coral }}>•</span>
                  <span style={{ color: Colors.textPrimary }}>{e}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="space-y-1">
              {Object.entries(errors.error_counts || {})
                .sort((a, b) => b[1] - a[1])
                .slice(0, 6)
                .map(([k, v]) => (
                  <div
                    key={k}
                    className="flex justify-between text-[11px] font-mono"
                  >
                    <span style={{ color: Colors.textPrimary }}>{k}</span>
                    <span style={{ color: Colors.coral }}>{v}</span>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}

      {/* Pending proposals */}
      <div
        className="rounded-xl p-3.5"
        style={{
          backgroundColor: Colors.card,
          border: `1px solid ${Colors.cardBorder}`,
        }}
      >
        <div
          className="text-[11px] font-bold uppercase tracking-wider mb-3"
          style={{ color: Colors.textPrimary }}
        >
          Pending Weight Proposals ({proposals.length})
        </div>
        {proposals.length === 0 ? (
          <div
            className="text-[11px] font-mono text-center py-4"
            style={{ color: Colors.textTertiary }}
          >
            No pending proposals. High-confidence changes auto-deploy;
            medium/low sit here for review.
          </div>
        ) : (
          <div className="space-y-3">
            {proposals.map((p) => (
              <div
                key={p.proposal_id}
                className="rounded-lg p-2.5"
                style={{
                  backgroundColor: Colors.surfaceBorder,
                  border: `1px solid ${Colors.cardBorder}`,
                }}
              >
                <div className="flex justify-between items-start mb-2">
                  <div>
                    <div
                      className="text-[11px] font-bold font-mono"
                      style={{ color: Colors.textPrimary }}
                    >
                      {p.proposal_id.slice(0, 8)}
                    </div>
                    <div
                      className="text-[9px] font-mono"
                      style={{ color: Colors.textTertiary }}
                    >
                      {p.timestamp?.slice(0, 16).replace("T", " ")} · n=
                      {p.sample_size ?? "?"} ·{" "}
                      {p.confidence_level?.toUpperCase() ?? "?"} confidence
                    </div>
                  </div>
                  <button
                    onClick={() => deployProposal(p.proposal_id)}
                    className="text-[10px] font-bold uppercase tracking-wider px-2 py-1 rounded"
                    style={{
                      color: Colors.green,
                      backgroundColor: Colors.greenDim,
                    }}
                  >
                    Deploy
                  </button>
                </div>
                <div className="space-y-1">
                  {Object.entries(p.weight_deltas || {}).map(([k, d]) => {
                    const dNum = toSafeNum(d) ?? 0;
                    return (
                      <div
                        key={k}
                        className="flex justify-between text-[10px] font-mono"
                      >
                        <span style={{ color: Colors.textPrimary }}>{k}</span>
                        <span style={{ color: deltaColor(dNum) }}>
                          {dNum >= 0 ? "+" : ""}
                          {fmtNumber(dNum, 3)}
                        </span>
                      </div>
                    );
                  })}
                </div>
                {p.supporting_evidence && p.supporting_evidence.length > 0 && (
                  <div
                    className="mt-2 pt-2 space-y-0.5"
                    style={{
                      borderTop: `1px solid ${Colors.cardBorder}`,
                    }}
                  >
                    {p.supporting_evidence.slice(0, 3).map((e, i) => (
                      <div
                        key={i}
                        className="text-[10px] font-mono"
                        style={{ color: Colors.textTertiary }}
                      >
                        {e}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Shared row component ─────────────────────────────────────

function GroupRow({ row }: { row: GroupPerf }) {
  return (
    <div className="grid grid-cols-12 gap-2 text-[11px] font-mono items-center">
      <div
        className="col-span-5 truncate"
        style={{ color: Colors.textPrimary }}
      >
        {row.group || "(uncategorized)"}
      </div>
      <div
        className="col-span-2 text-right"
        style={{ color: Colors.textTertiary }}
      >
        n={row.count}
      </div>
      <div
        className="col-span-2 text-right font-bold"
        style={{ color: brierColor(row.avg_brier) }}
      >
        {fmtNumber(row.avg_brier, 3)}
      </div>
      <div
        className="col-span-3 text-right"
        style={{
          color:
            (row.hit_rate ?? 0) >= 50 ? Colors.green : Colors.coral,
        }}
      >
        {fmtPct(row.hit_rate, 0)}
      </div>
    </div>
  );
}
