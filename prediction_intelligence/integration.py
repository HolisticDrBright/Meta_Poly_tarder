"""
Integration module — drop-in connection point for the existing bot.

Call these methods from the trading scheduler and strategy evaluators
to feed data into the prediction intelligence system.
"""

from __future__ import annotations

import logging
from typing import Optional

from prediction_intelligence.logger import DecisionLogger, DecisionRecord
from prediction_intelligence.adjuster import WeightAdjuster

logger = logging.getLogger(__name__)

_instance: Optional[PredictionIntelligenceIntegration] = None


class PredictionIntelligenceIntegration:
    """Drop-in integration point for the existing bot."""

    def __init__(self, db_path: str | None = None) -> None:
        self.decision_logger = DecisionLogger(db_path)
        self.adjuster = WeightAdjuster(self.decision_logger)

    def get_active_weights(self) -> dict:
        """Call at the start of each analysis to get current optimized weights."""
        return self.adjuster.get_active_weights()

    def get_active_thresholds(self) -> dict:
        """Get current no-trade threshold, confidence floor, etc."""
        return self.adjuster.get_active_thresholds()

    def log_completed_analysis(self, analysis_result: dict) -> str:
        """Call after each analysis completes. Returns decision_id."""
        record = DecisionRecord(
            market_id=analysis_result.get("market_id", ""),
            market_title=analysis_result.get("market_title", analysis_result.get("question", "")),
            market_theme=analysis_result.get("market_theme", analysis_result.get("category", "")),
            resolution_date=analysis_result.get("resolution_date", ""),
            implied_probability=analysis_result.get("implied_probability", analysis_result.get("market_price", 0.5)),
            best_bid=analysis_result.get("best_bid", 0),
            best_ask=analysis_result.get("best_ask", 0),
            spread=analysis_result.get("spread", 0),
            volume_24h=analysis_result.get("volume_24h", 0),
            fair_probability=analysis_result.get("fair_probability", analysis_result.get("model_probability", 0.5)),
            model_confidence=analysis_result.get("model_confidence", analysis_result.get("confidence", 0.5)),
            edge_estimate=analysis_result.get("edge_estimate", 0),
            opportunity_score=analysis_result.get("opportunity_score", 0),
            edge_classification=analysis_result.get("edge_classification", "unknown"),
            classification=analysis_result.get("classification", analysis_result.get("action", "NO-TRADE")),
            paper_position_size=analysis_result.get("paper_position_size", analysis_result.get("size_usdc", 0)),
            paper_entry_price=analysis_result.get("paper_entry_price", analysis_result.get("price", 0)),
            risk_approved=analysis_result.get("risk_approved", False),
            signal_weights=analysis_result.get("signal_weights", self.get_active_weights()),
            regime_label=analysis_result.get("regime_label", ""),
            regime_confidence=analysis_result.get("regime_confidence", 0.5),
            resolution_rules_summary=analysis_result.get("resolution_rules_summary", ""),
            resolution_ambiguity_score=analysis_result.get("resolution_ambiguity_score", 0),
            evidence_strength_score=analysis_result.get("evidence_strength_score", 0),
            base_rate_prior=analysis_result.get("base_rate_prior", 0.5),
            sentiment_crowding_score=analysis_result.get("sentiment_crowding_score", 0),
            red_team_confidence_haircut=analysis_result.get("red_team_confidence_haircut", 0),
            fill_realism_score=analysis_result.get("fill_realism_score", 50),
        )
        return self.decision_logger.log_decision(record)

    def get_regime_performance(self, regime: str) -> dict:
        """Check historical performance in a specific regime."""
        rows = self.decision_logger.query("""
            SELECT
                COUNT(*) as count,
                AVG(o.brier_score) as avg_brier,
                AVG(o.paper_pnl) as avg_pnl,
                SUM(CASE WHEN o.paper_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0) as hit_rate
            FROM decision_log d
            JOIN outcome_log o ON d.decision_id = o.decision_id
            WHERE d.regime_label = ?
        """, [regime])
        return rows[0] if rows else {}

    def get_theme_performance(self, theme: str) -> dict:
        """Check historical performance on a specific market theme."""
        rows = self.decision_logger.query("""
            SELECT
                COUNT(*) as count,
                AVG(o.brier_score) as avg_brier,
                AVG(o.paper_pnl) as avg_pnl,
                SUM(CASE WHEN o.paper_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0) as hit_rate
            FROM decision_log d
            JOIN outcome_log o ON d.decision_id = o.decision_id
            WHERE d.market_theme = ?
        """, [theme])
        return rows[0] if rows else {}

    def close(self) -> None:
        self.decision_logger.close()


def get_integration(db_path: str | None = None) -> PredictionIntelligenceIntegration:
    """Get or create the singleton integration instance."""
    global _instance
    if _instance is None:
        _instance = PredictionIntelligenceIntegration(db_path)
    return _instance
