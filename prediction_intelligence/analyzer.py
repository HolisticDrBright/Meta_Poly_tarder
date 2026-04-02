"""
Retrospective Analyzer — finds systematic patterns in bot performance.

Analyzes resolved decisions to detect calibration errors, signal attribution,
regime performance, error taxonomy, and generates reports that feed into
the weight adjuster.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from prediction_intelligence.config import pi_config
from prediction_intelligence.logger import DecisionLogger

logger = logging.getLogger(__name__)


@dataclass
class AnalysisReport:
    report_id: str
    timestamp: str
    scored_outcomes: int
    total_decisions: int

    # Section A: Calibration
    calibration_buckets: list[dict] = field(default_factory=list)
    overall_brier: float = 0.0

    # Section B: By theme
    theme_performance: list[dict] = field(default_factory=list)

    # Section C: By regime
    regime_performance: list[dict] = field(default_factory=list)

    # Section D: By edge classification
    edge_performance: list[dict] = field(default_factory=list)

    # Section E: Signal attribution
    signal_attribution: dict = field(default_factory=dict)
    weight_recommendations: dict = field(default_factory=dict)

    # Section F: Error taxonomy
    error_counts: dict = field(default_factory=dict)
    top_errors: list[str] = field(default_factory=list)

    # Section G: No-trade analysis
    no_trade_analysis: dict = field(default_factory=dict)

    optimization_ready: bool = False


class RetrospectiveAnalyzer:
    """Analyzes resolved decisions to find patterns and produce reports."""

    def __init__(self, decision_logger: DecisionLogger) -> None:
        self.logger = decision_logger
        self._last_analysis_time: Optional[datetime] = None
        self._last_scored_count: int = 0

    def should_run(self) -> bool:
        """Check if analysis should run based on triggers."""
        scored = self.logger.get_scored_count()

        # Not enough data
        if scored < pi_config.min_outcomes_for_analysis:
            return False

        # Trigger 1: enough new outcomes since last run
        new_since_last = scored - self._last_scored_count
        if new_since_last >= pi_config.analysis_trigger_new_outcomes:
            return True

        # Trigger 2: enough time since last run
        if self._last_analysis_time:
            days_since = (datetime.now(timezone.utc) - self._last_analysis_time).days
            if days_since >= pi_config.analysis_trigger_max_days:
                return True
        else:
            return True  # Never run before

        return False

    def run_analysis(self) -> AnalysisReport:
        """Run the full retrospective analysis."""
        conn = self.logger._ensure_conn()
        scored = self.logger.get_scored_count()
        total = self.logger.get_total_count()
        optimization_ready = scored >= pi_config.min_outcomes_for_optimization

        report = AnalysisReport(
            report_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            scored_outcomes=scored,
            total_decisions=total,
            optimization_ready=optimization_ready,
        )

        # Section A: Calibration
        report.calibration_buckets = self._analyze_calibration(conn)
        brier_row = conn.execute(
            "SELECT AVG(o.brier_score) FROM outcome_log o"
        ).fetchone()
        report.overall_brier = brier_row[0] if brier_row and brier_row[0] else 0.0

        # Section B: By theme
        report.theme_performance = self._analyze_by_group(conn, "market_theme")

        # Section C: By regime
        report.regime_performance = self._analyze_by_group(conn, "regime_label")

        # Section D: By edge classification
        report.edge_performance = self._analyze_by_group(conn, "edge_classification")

        # Section E: Signal attribution
        report.signal_attribution = self._analyze_signals(conn)
        if optimization_ready:
            report.weight_recommendations = self._compute_weight_recommendations(conn)

        # Section F: Error taxonomy
        report.error_counts, report.top_errors = self._analyze_errors(conn)

        # Section G: No-trade analysis
        report.no_trade_analysis = self._analyze_no_trades(conn)

        # Store report
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_reports (
                report_id VARCHAR PRIMARY KEY,
                timestamp TIMESTAMP,
                report_json VARCHAR
            )
        """)
        conn.execute(
            "INSERT INTO analysis_reports VALUES (?, ?, ?)",
            [report.report_id, report.timestamp, json.dumps({
                "report_id": report.report_id,
                "timestamp": report.timestamp,
                "scored_outcomes": report.scored_outcomes,
                "total_decisions": report.total_decisions,
                "overall_brier": report.overall_brier,
                "calibration_buckets": report.calibration_buckets,
                "theme_performance": report.theme_performance,
                "regime_performance": report.regime_performance,
                "edge_performance": report.edge_performance,
                "signal_attribution": report.signal_attribution,
                "weight_recommendations": report.weight_recommendations,
                "error_counts": report.error_counts,
                "top_errors": report.top_errors,
                "no_trade_analysis": report.no_trade_analysis,
                "optimization_ready": report.optimization_ready,
            })],
        )

        self._last_analysis_time = datetime.now(timezone.utc)
        self._last_scored_count = scored

        logger.info(
            f"Analysis complete: {report.report_id} — "
            f"{scored} outcomes, brier={report.overall_brier:.4f}, "
            f"optimization={'ON' if optimization_ready else 'OFF'}"
        )
        return report

    def _analyze_calibration(self, conn) -> list[dict]:
        """Bucket fair_probability into deciles and compare to actual outcomes."""
        rows = conn.execute("""
            SELECT
                FLOOR(d.fair_probability * 10) / 10 as bucket,
                COUNT(*) as count,
                AVG(o.actual_outcome) as actual_freq,
                AVG(d.fair_probability) as predicted_freq,
                AVG(d.fair_probability) - AVG(o.actual_outcome) as calibration_error
            FROM decision_log d
            JOIN outcome_log o ON d.decision_id = o.decision_id
            GROUP BY FLOOR(d.fair_probability * 10) / 10
            ORDER BY bucket
        """).fetchall()
        return [
            {"bucket": r[0], "count": r[1], "actual_freq": r[2],
             "predicted_freq": r[3], "calibration_error": r[4]}
            for r in rows
        ]

    def _analyze_by_group(self, conn, group_col: str) -> list[dict]:
        """Analyze performance grouped by a column."""
        rows = conn.execute(f"""
            SELECT
                d.{group_col} as group_val,
                COUNT(*) as count,
                AVG(o.brier_score) as avg_brier,
                AVG(d.edge_estimate) as avg_edge,
                AVG(o.paper_pnl) as avg_pnl,
                SUM(CASE WHEN o.paper_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as hit_rate
            FROM decision_log d
            JOIN outcome_log o ON d.decision_id = o.decision_id
            WHERE d.{group_col} IS NOT NULL AND d.{group_col} != ''
            GROUP BY d.{group_col}
            ORDER BY avg_brier ASC
        """).fetchall()
        return [
            {"group": r[0], "count": r[1], "avg_brier": r[2], "avg_edge": r[3],
             "avg_pnl": r[4], "hit_rate": r[5]}
            for r in rows
        ]

    def _analyze_signals(self, conn) -> dict:
        """Correlate signal scores with prediction accuracy."""
        rows = conn.execute("""
            SELECT
                CASE WHEN o.forecast_error < 0.15 THEN 'right' ELSE 'wrong' END as accuracy,
                AVG(d.base_rate_prior) as avg_base_rate,
                AVG(d.evidence_strength_score) as avg_evidence,
                AVG(d.sentiment_crowding_score) as avg_sentiment,
                AVG(d.model_confidence) as avg_confidence,
                AVG(d.fill_realism_score) as avg_fill_realism,
                AVG(d.red_team_confidence_haircut) as avg_red_team,
                AVG(d.regime_confidence) as avg_regime_conf,
                COUNT(*) as count
            FROM decision_log d
            JOIN outcome_log o ON d.decision_id = o.decision_id
            GROUP BY CASE WHEN o.forecast_error < 0.15 THEN 'right' ELSE 'wrong' END
        """).fetchall()
        cols = ["accuracy", "avg_base_rate", "avg_evidence", "avg_sentiment",
                "avg_confidence", "avg_fill_realism", "avg_red_team", "avg_regime_conf", "count"]
        result = {}
        for r in rows:
            d = dict(zip(cols, r))
            result[d["accuracy"]] = d
        return result

    def _compute_weight_recommendations(self, conn) -> dict:
        """Compute empirically optimal signal weights."""
        # Compare signal values between right and wrong predictions
        attr = self._analyze_signals(conn)
        right = attr.get("right", {})
        wrong = attr.get("wrong", {})

        if not right or not wrong:
            return {}

        # Signals that are higher when right → should be weighted more
        signals = ["avg_base_rate", "avg_evidence", "avg_confidence", "avg_fill_realism", "avg_regime_conf"]
        signal_map = {
            "avg_base_rate": "base_rate",
            "avg_evidence": "catalyst_strength",
            "avg_confidence": "sentiment_divergence",
            "avg_fill_realism": "microstructure_anomaly",
            "avg_regime_conf": "cross_market_inconsistency",
        }

        recommendations = {}
        for s in signals:
            r_val = right.get(s, 0) or 0
            w_val = wrong.get(s, 0) or 0
            if r_val + w_val > 0:
                # Signal's predictive power = how much higher it is when right vs wrong
                predictive_power = (r_val - w_val) / max(r_val, w_val, 0.01)
                mapped = signal_map.get(s, s)
                recommendations[mapped] = round(predictive_power, 4)

        return recommendations

    def _analyze_errors(self, conn) -> tuple[dict, list[str]]:
        """Classify errors into taxonomy categories."""
        rows = conn.execute("""
            SELECT
                d.decision_id,
                d.fair_probability,
                o.actual_outcome,
                o.forecast_error,
                d.base_rate_prior,
                d.resolution_ambiguity_score,
                d.regime_label,
                d.sentiment_crowding_score,
                d.fill_realism_score,
                o.paper_pnl
            FROM decision_log d
            JOIN outcome_log o ON d.decision_id = o.decision_id
            WHERE o.forecast_error > 0.20
        """).fetchall()

        error_counts: dict[str, int] = {
            "overconfidence": 0, "base_rate_miss": 0, "evidence_stale": 0,
            "rule_misunderstanding": 0, "regime_mismatch": 0, "crowding_trap": 0,
            "liquidity_illusion": 0, "timing_error": 0,
        }

        for r in rows:
            fair_p, actual, error, base_rate, ambiguity, regime, crowd, fill, pnl = r[1:]

            # Overconfidence: extreme prediction, opposite outcome
            if (fair_p > 0.8 and actual < 0.5) or (fair_p < 0.2 and actual > 0.5):
                error_counts["overconfidence"] += 1
            # Base rate miss
            elif abs(base_rate - actual) > 0.3:
                error_counts["base_rate_miss"] += 1
            # Crowding trap
            elif crowd > 60 and pnl and pnl < 0:
                error_counts["crowding_trap"] += 1
            # Liquidity illusion
            elif fill > 70 and pnl and pnl < 0:
                error_counts["liquidity_illusion"] += 1
            # Rule misunderstanding
            elif ambiguity < 0.3 and error > 0.3:
                error_counts["rule_misunderstanding"] += 1
            else:
                error_counts["timing_error"] += 1

        sorted_errors = sorted(error_counts.items(), key=lambda x: x[1], reverse=True)
        top_3 = [e[0] for e in sorted_errors[:3]]
        return error_counts, top_3

    def _analyze_no_trades(self, conn) -> dict:
        """Analyze markets classified as NO-TRADE."""
        rows = conn.execute("""
            SELECT
                COUNT(*) as total_no_trade,
                COUNT(CASE WHEN o.decision_id IS NOT NULL THEN 1 END) as scored,
                AVG(CASE WHEN o.decision_id IS NOT NULL THEN o.brier_score END) as hypothetical_brier,
                AVG(CASE WHEN o.decision_id IS NOT NULL AND ABS(d.edge_estimate) > 0.05
                    THEN 1 ELSE 0 END) as missed_edge_rate
            FROM decision_log d
            LEFT JOIN outcome_log o ON d.decision_id = o.decision_id
            WHERE d.classification = 'NO-TRADE'
        """).fetchone()
        if rows:
            return {
                "total_no_trade": rows[0],
                "scored": rows[1],
                "hypothetical_brier": rows[2],
                "missed_edge_rate": rows[3],
            }
        return {}

    def get_latest_report(self) -> Optional[dict]:
        """Get the most recent analysis report."""
        conn = self.logger._ensure_conn()
        try:
            row = conn.execute(
                "SELECT report_json FROM analysis_reports ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            return json.loads(row[0]) if row else None
        except Exception:
            return None

    def get_all_reports(self, limit: int = 20) -> list[dict]:
        """Get all analysis reports."""
        conn = self.logger._ensure_conn()
        try:
            rows = conn.execute(
                "SELECT report_json FROM analysis_reports ORDER BY timestamp DESC LIMIT ?",
                [limit]
            ).fetchall()
            return [json.loads(r[0]) for r in rows]
        except Exception:
            return []
