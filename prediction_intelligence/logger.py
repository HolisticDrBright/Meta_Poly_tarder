"""
Structured Decision Logger — records every analysis decision and outcome.

Every market analysis the bot produces gets logged here with full context:
agent outputs, scores, signal weights, and the final verdict. When markets
resolve, outcomes are matched to decisions for scoring.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

import duckdb

from prediction_intelligence.config import pi_config

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decision_log (
    decision_id VARCHAR PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    market_id VARCHAR NOT NULL,
    market_title VARCHAR,
    market_theme VARCHAR,
    resolution_date TIMESTAMP,
    implied_probability DOUBLE,
    best_bid DOUBLE,
    best_ask DOUBLE,
    spread DOUBLE,
    depth_score DOUBLE,
    volume_24h DOUBLE,
    resolution_rules_summary VARCHAR,
    resolution_ambiguity_score DOUBLE,
    rule_hazard_flags VARCHAR,
    evidence_items VARCHAR,
    evidence_strength_score DOUBLE,
    base_rate_prior DOUBLE,
    base_rate_comparables VARCHAR,
    base_rate_uncertainty DOUBLE,
    fair_probability DOUBLE,
    probability_range_low DOUBLE,
    probability_range_high DOUBLE,
    model_confidence DOUBLE,
    regime_label VARCHAR,
    regime_confidence DOUBLE,
    sentiment_crowding_score DOUBLE,
    sentiment_reflexivity_score DOUBLE,
    red_team_strongest_objection VARCHAR,
    red_team_confidence_haircut DOUBLE,
    red_team_hidden_assumptions VARCHAR,
    market_structure_tradability VARCHAR,
    fill_realism_score DOUBLE,
    slippage_estimate_bps DOUBLE,
    edge_estimate DOUBLE,
    opportunity_score DOUBLE,
    edge_classification VARCHAR,
    classification VARCHAR,
    paper_position_size DOUBLE,
    paper_entry_price DOUBLE,
    risk_approved BOOLEAN,
    risk_block_reason VARCHAR,
    signal_weights VARCHAR,
    prompt_versions VARCHAR,
    total_tokens_used INTEGER,
    total_cost_usd DOUBLE,
    execution_time_ms INTEGER
);

CREATE TABLE IF NOT EXISTS outcome_log (
    decision_id VARCHAR PRIMARY KEY,
    market_id VARCHAR NOT NULL,
    resolution_timestamp TIMESTAMP NOT NULL,
    actual_outcome DOUBLE NOT NULL,
    forecast_error DOUBLE NOT NULL,
    brier_score DOUBLE NOT NULL,
    paper_pnl DOUBLE,
    resolution_source VARCHAR,
    time_to_resolution_hours DOUBLE
);

CREATE INDEX IF NOT EXISTS idx_decision_market ON decision_log(market_id);
CREATE INDEX IF NOT EXISTS idx_decision_timestamp ON decision_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_decision_theme ON decision_log(market_theme);
CREATE INDEX IF NOT EXISTS idx_decision_classification ON decision_log(classification);
CREATE INDEX IF NOT EXISTS idx_decision_regime ON decision_log(regime_label);
CREATE INDEX IF NOT EXISTS idx_decision_edge_class ON decision_log(edge_classification);
CREATE INDEX IF NOT EXISTS idx_outcome_market ON outcome_log(market_id);
"""


@dataclass
class DecisionRecord:
    market_id: str
    market_title: str = ""
    market_theme: str = ""
    resolution_date: str = ""
    implied_probability: float = 0.5
    best_bid: float = 0.0
    best_ask: float = 0.0
    spread: float = 0.0
    depth_score: float = 0.0
    volume_24h: float = 0.0
    resolution_rules_summary: str = ""
    resolution_ambiguity_score: float = 0.0
    rule_hazard_flags: list[str] = field(default_factory=list)
    evidence_items: list[dict] = field(default_factory=list)
    evidence_strength_score: float = 0.0
    base_rate_prior: float = 0.5
    base_rate_comparables: list[str] = field(default_factory=list)
    base_rate_uncertainty: float = 0.5
    fair_probability: float = 0.5
    probability_range_low: float = 0.0
    probability_range_high: float = 1.0
    model_confidence: float = 0.5
    regime_label: str = ""
    regime_confidence: float = 0.5
    sentiment_crowding_score: float = 0.0
    sentiment_reflexivity_score: float = 0.0
    red_team_strongest_objection: str = ""
    red_team_confidence_haircut: float = 0.0
    red_team_hidden_assumptions: list[str] = field(default_factory=list)
    market_structure_tradability: str = "good"
    fill_realism_score: float = 50.0
    slippage_estimate_bps: float = 0.0
    edge_estimate: float = 0.0
    opportunity_score: float = 0.0
    edge_classification: str = "unknown"
    classification: str = "NO-TRADE"
    paper_position_size: float = 0.0
    paper_entry_price: float = 0.0
    risk_approved: bool = False
    risk_block_reason: str | None = None
    signal_weights: dict = field(default_factory=dict)
    prompt_versions: dict = field(default_factory=dict)
    total_tokens_used: int = 0
    total_cost_usd: float = 0.0
    execution_time_ms: int = 0


@dataclass
class OutcomeRecord:
    decision_id: str
    market_id: str
    resolution_timestamp: str
    actual_outcome: float
    forecast_error: float
    brier_score: float
    paper_pnl: float = 0.0
    resolution_source: str = ""
    time_to_resolution_hours: float = 0.0


class DecisionLogger:
    """Logs every bot decision and outcome to DuckDB."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or pi_config.duckdb_path
        self._conn: Optional[duckdb.DuckDBPyConnection] = None

    def _ensure_conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            from pathlib import Path
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(self.db_path)
            self._conn.execute(SCHEMA_SQL)
            logger.info(f"Decision logger connected: {self.db_path}")
        return self._conn

    def log_decision(self, record: DecisionRecord) -> str:
        """Log a decision. Returns the decision_id."""
        conn = self._ensure_conn()
        decision_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn.execute("""
            INSERT OR REPLACE INTO decision_log VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?
            )
        """, [
            decision_id, now, record.market_id, record.market_title,
            record.market_theme, record.resolution_date or None,
            record.implied_probability, record.best_bid, record.best_ask,
            record.spread, record.depth_score, record.volume_24h,
            record.resolution_rules_summary, record.resolution_ambiguity_score,
            json.dumps(record.rule_hazard_flags),
            json.dumps(record.evidence_items) if pi_config.log_full_evidence else "[]",
            record.evidence_strength_score,
            record.base_rate_prior, json.dumps(record.base_rate_comparables),
            record.base_rate_uncertainty,
            record.fair_probability, record.probability_range_low,
            record.probability_range_high, record.model_confidence,
            record.regime_label, record.regime_confidence,
            record.sentiment_crowding_score, record.sentiment_reflexivity_score,
            record.red_team_strongest_objection, record.red_team_confidence_haircut,
            json.dumps(record.red_team_hidden_assumptions),
            record.market_structure_tradability, record.fill_realism_score,
            record.slippage_estimate_bps,
            record.edge_estimate, record.opportunity_score,
            record.edge_classification, record.classification,
            record.paper_position_size, record.paper_entry_price,
            record.risk_approved, record.risk_block_reason,
            json.dumps(record.signal_weights), json.dumps(record.prompt_versions),
            record.total_tokens_used, record.total_cost_usd,
            record.execution_time_ms,
        ])

        logger.info(f"Decision logged: {decision_id} — {record.market_title[:50]} [{record.classification}]")
        return decision_id

    def log_outcome(self, outcome: OutcomeRecord) -> None:
        """Log a market resolution outcome."""
        conn = self._ensure_conn()
        conn.execute("""
            INSERT OR REPLACE INTO outcome_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            outcome.decision_id, outcome.market_id,
            outcome.resolution_timestamp, outcome.actual_outcome,
            outcome.forecast_error, outcome.brier_score,
            outcome.paper_pnl, outcome.resolution_source,
            outcome.time_to_resolution_hours,
        ])
        logger.info(
            f"Outcome logged: {outcome.decision_id} — "
            f"actual={outcome.actual_outcome}, brier={outcome.brier_score:.4f}"
        )

    def get_unscored_decisions(self) -> list[dict]:
        """Get decisions that don't have outcomes yet."""
        conn = self._ensure_conn()
        rows = conn.execute("""
            SELECT d.decision_id, d.market_id, d.market_title, d.fair_probability,
                   d.classification, d.resolution_date, d.paper_position_size,
                   d.paper_entry_price
            FROM decision_log d
            LEFT JOIN outcome_log o ON d.decision_id = o.decision_id
            WHERE o.decision_id IS NULL
            ORDER BY d.timestamp DESC
        """).fetchall()
        cols = ["decision_id", "market_id", "market_title", "fair_probability",
                "classification", "resolution_date", "paper_position_size", "paper_entry_price"]
        return [dict(zip(cols, r)) for r in rows]

    def get_scored_count(self) -> int:
        """Count how many decisions have outcomes."""
        conn = self._ensure_conn()
        result = conn.execute("SELECT COUNT(*) FROM outcome_log").fetchone()
        return result[0] if result else 0

    def get_total_count(self) -> int:
        conn = self._ensure_conn()
        result = conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()
        return result[0] if result else 0

    def query(self, sql: str, params: list | None = None) -> list[dict]:
        """Run arbitrary SQL query."""
        conn = self._ensure_conn()
        result = conn.execute(sql, params or [])
        cols = [desc[0] for desc in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
