"""
Configuration for the Prediction Intelligence Layer.

All values are overridable via environment variables with the PI_ prefix.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _float(val: str | None, default: float) -> float:
    return float(val) if val else default


def _int(val: str | None, default: int) -> int:
    return int(val) if val else default


def _bool(val: str | None, default: bool) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes")


@dataclass(frozen=True)
class PredictionIntelligenceConfig:
    # Database — absolute path so systemd CWD changes don't break it
    duckdb_path: str = os.getenv(
        "PI_DUCKDB_PATH",
        str(Path(__file__).resolve().parent.parent / "data" / "prediction_intelligence.duckdb"),
    )

    # Decision Logger
    log_full_evidence: bool = _bool(os.getenv("PI_LOG_FULL_EVIDENCE"), True)
    outcome_backfill_interval_hours: int = _int(os.getenv("PI_OUTCOME_BACKFILL_HOURS"), 6)

    # Retrospective Analyzer
    min_outcomes_for_analysis: int = _int(os.getenv("PI_MIN_OUTCOMES_ANALYSIS"), 50)
    min_outcomes_for_optimization: int = _int(os.getenv("PI_MIN_OUTCOMES_OPTIMIZATION"), 50)
    analysis_trigger_new_outcomes: int = _int(os.getenv("PI_ANALYSIS_TRIGGER_OUTCOMES"), 25)
    analysis_trigger_max_days: int = _int(os.getenv("PI_ANALYSIS_TRIGGER_DAYS"), 7)
    rolling_window_days: int = _int(os.getenv("PI_ROLLING_WINDOW_DAYS"), 90)
    decay_half_life_days: int = _int(os.getenv("PI_DECAY_HALF_LIFE_DAYS"), 60)

    # Weight Adjuster
    max_weight_change_per_cycle: float = _float(os.getenv("PI_MAX_WEIGHT_CHANGE"), 0.05)
    min_weight_bound: float = _float(os.getenv("PI_MIN_WEIGHT"), 0.05)
    max_weight_bound: float = _float(os.getenv("PI_MAX_WEIGHT"), 0.45)
    min_improvement_threshold: float = _float(os.getenv("PI_MIN_IMPROVEMENT"), 0.10)
    revert_degradation_threshold: float = _float(os.getenv("PI_REVERT_THRESHOLD"), 0.15)
    monitoring_window_days: int = _int(os.getenv("PI_MONITORING_WINDOW_DAYS"), 14)
    auto_deploy_confidence: str = os.getenv("PI_AUTO_DEPLOY_CONFIDENCE", "high")

    # Orchestrator
    cycle_interval_hours: int = _int(os.getenv("PI_CYCLE_INTERVAL_HOURS"), 6)

    # Signal Weights (initial defaults)
    default_signal_weights: dict = field(default_factory=lambda: {
        "base_rate": 0.30,
        "catalyst_strength": 0.25,
        "sentiment_divergence": 0.15,
        "microstructure_anomaly": 0.15,
        "cross_market_inconsistency": 0.15,
    })

    # No-trade thresholds
    default_no_trade_threshold: float = 35.0
    default_confidence_floor: float = 0.40
    default_min_edge: float = 0.03
    default_max_ambiguity: float = 0.60


pi_config = PredictionIntelligenceConfig()
