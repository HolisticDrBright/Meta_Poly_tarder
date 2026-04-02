"""
Weight Adjuster — proposes and deploys optimized signal weights.

Takes the analyzer's output and produces specific, validated configuration
changes with strict safety rails to prevent overfitting.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from prediction_intelligence.config import pi_config
from prediction_intelligence.logger import DecisionLogger

logger = logging.getLogger(__name__)

WEIGHTS_FILE = Path("data/active_weights.json")


@dataclass
class WeightProposal:
    proposal_id: str
    timestamp: str
    analysis_report_id: str
    current_weights: dict
    proposed_weights: dict
    weight_deltas: dict
    current_no_trade_threshold: float
    proposed_no_trade_threshold: float
    current_confidence_floor: float
    proposed_confidence_floor: float
    regime_overrides: dict = field(default_factory=dict)
    supporting_evidence: list[str] = field(default_factory=list)
    expected_improvement: dict = field(default_factory=dict)
    confidence_level: str = "low"
    sample_size: int = 0
    auto_deploy: bool = False
    requires_human_review: bool = True
    revert_checkpoint: dict = field(default_factory=dict)


class WeightAdjuster:
    """Proposes and deploys optimized signal weights with safety rails."""

    def __init__(self, decision_logger: DecisionLogger) -> None:
        self.logger = decision_logger
        self._active_deployment: Optional[dict] = None

    def get_active_weights(self) -> dict:
        """Get current active signal weights."""
        if WEIGHTS_FILE.exists():
            try:
                data = json.loads(WEIGHTS_FILE.read_text())
                return data.get("weights", pi_config.default_signal_weights)
            except Exception:
                pass
        return dict(pi_config.default_signal_weights)

    def get_active_thresholds(self) -> dict:
        """Get current active thresholds."""
        if WEIGHTS_FILE.exists():
            try:
                data = json.loads(WEIGHTS_FILE.read_text())
                return {
                    "no_trade_threshold": data.get("no_trade_threshold", pi_config.default_no_trade_threshold),
                    "confidence_floor": data.get("confidence_floor", pi_config.default_confidence_floor),
                    "min_edge": data.get("min_edge", pi_config.default_min_edge),
                    "max_ambiguity": data.get("max_ambiguity", pi_config.default_max_ambiguity),
                }
            except Exception:
                pass
        return {
            "no_trade_threshold": pi_config.default_no_trade_threshold,
            "confidence_floor": pi_config.default_confidence_floor,
            "min_edge": pi_config.default_min_edge,
            "max_ambiguity": pi_config.default_max_ambiguity,
        }

    def propose_weights(self, report: dict) -> Optional[WeightProposal]:
        """Generate a weight proposal from an analysis report."""
        scored = report.get("scored_outcomes", 0)

        # Safety: minimum outcomes
        if scored < pi_config.min_outcomes_for_optimization:
            logger.info(f"Not enough outcomes for optimization: {scored} < {pi_config.min_outcomes_for_optimization}")
            return None

        recommendations = report.get("weight_recommendations", {})
        if not recommendations:
            logger.info("No weight recommendations in report")
            return None

        current = self.get_active_weights()
        proposed = dict(current)
        deltas = {}
        evidence = []

        for signal, power in recommendations.items():
            if signal not in current:
                continue

            # Direction: positive power → increase weight, negative → decrease
            if power > 0:
                delta = min(pi_config.max_weight_change_per_cycle, power * 0.1)
            else:
                delta = max(-pi_config.max_weight_change_per_cycle, power * 0.1)

            new_val = current[signal] + delta
            new_val = max(pi_config.min_weight_bound, min(pi_config.max_weight_bound, new_val))
            delta = new_val - current[signal]

            if abs(delta) > 0.001:
                proposed[signal] = round(new_val, 4)
                deltas[signal] = round(delta, 4)
                direction = "increase" if delta > 0 else "decrease"
                evidence.append(
                    f"{signal}: {direction} by {abs(delta):.3f} "
                    f"(predictive power: {power:.3f})"
                )

        if not deltas:
            logger.info("No meaningful weight changes proposed")
            return None

        # Normalize weights to sum to 1
        total = sum(proposed.values())
        if total > 0:
            proposed = {k: round(v / total, 4) for k, v in proposed.items()}

        # Determine confidence level
        if scored >= 200 and len(deltas) <= 3:
            confidence = "high"
        elif scored >= 100:
            confidence = "medium"
        else:
            confidence = "low"

        auto_deploy = (confidence == pi_config.auto_deploy_confidence)

        proposal = WeightProposal(
            proposal_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            analysis_report_id=report.get("report_id", ""),
            current_weights=current,
            proposed_weights=proposed,
            weight_deltas=deltas,
            current_no_trade_threshold=self.get_active_thresholds()["no_trade_threshold"],
            proposed_no_trade_threshold=self.get_active_thresholds()["no_trade_threshold"],
            current_confidence_floor=self.get_active_thresholds()["confidence_floor"],
            proposed_confidence_floor=self.get_active_thresholds()["confidence_floor"],
            supporting_evidence=evidence,
            expected_improvement={"brier_reduction_estimate": round(sum(abs(d) for d in deltas.values()) * 0.01, 4)},
            confidence_level=confidence,
            sample_size=scored,
            auto_deploy=auto_deploy,
            requires_human_review=not auto_deploy,
            revert_checkpoint={"weights": current, "timestamp": datetime.now(timezone.utc).isoformat()},
        )

        # Store proposal
        self._store_proposal(proposal)

        logger.info(
            f"Weight proposal: {proposal.proposal_id} — "
            f"{len(deltas)} changes, confidence={confidence}, auto_deploy={auto_deploy}"
        )
        return proposal

    def deploy_weights(self, proposal: WeightProposal) -> bool:
        """Deploy proposed weights as the active configuration."""
        try:
            WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "weights": proposal.proposed_weights,
                "no_trade_threshold": proposal.proposed_no_trade_threshold,
                "confidence_floor": proposal.proposed_confidence_floor,
                "deployed_at": datetime.now(timezone.utc).isoformat(),
                "proposal_id": proposal.proposal_id,
                "revert_checkpoint": proposal.revert_checkpoint,
            }
            WEIGHTS_FILE.write_text(json.dumps(data, indent=2))
            self._active_deployment = data
            logger.info(f"Weights deployed: {proposal.proposal_id}")
            return True
        except Exception as e:
            logger.error(f"Weight deployment failed: {e}")
            return False

    def revert_weights(self) -> bool:
        """Revert to the previous weight configuration."""
        if WEIGHTS_FILE.exists():
            try:
                data = json.loads(WEIGHTS_FILE.read_text())
                checkpoint = data.get("revert_checkpoint", {})
                if checkpoint.get("weights"):
                    reverted = {
                        "weights": checkpoint["weights"],
                        "deployed_at": datetime.now(timezone.utc).isoformat(),
                        "proposal_id": "REVERTED",
                        "revert_checkpoint": {},
                    }
                    WEIGHTS_FILE.write_text(json.dumps(reverted, indent=2))
                    logger.warning("Weights REVERTED to previous checkpoint")
                    return True
            except Exception as e:
                logger.error(f"Weight revert failed: {e}")
        return False

    def check_revert(self) -> bool:
        """Check if current deployment should be reverted."""
        if not WEIGHTS_FILE.exists() or not self._active_deployment:
            return False

        deployed_at = self._active_deployment.get("deployed_at")
        if not deployed_at:
            return False

        deployed_time = datetime.fromisoformat(deployed_at)
        window_end = deployed_time + timedelta(days=pi_config.monitoring_window_days)

        if datetime.now(timezone.utc) < window_end:
            return False  # Still in monitoring window

        # Compare Brier scores: 14 days before vs after deployment
        conn = self.logger._ensure_conn()
        try:
            before = conn.execute("""
                SELECT AVG(o.brier_score) FROM outcome_log o
                JOIN decision_log d ON o.decision_id = d.decision_id
                WHERE d.timestamp < ? AND d.timestamp > ?
            """, [deployed_at, (deployed_time - timedelta(days=14)).isoformat()]).fetchone()

            after = conn.execute("""
                SELECT AVG(o.brier_score) FROM outcome_log o
                JOIN decision_log d ON o.decision_id = d.decision_id
                WHERE d.timestamp >= ?
            """, [deployed_at]).fetchone()

            before_brier = before[0] if before and before[0] else None
            after_brier = after[0] if after and after[0] else None

            if before_brier and after_brier and before_brier > 0:
                degradation = (after_brier - before_brier) / before_brier
                if degradation > pi_config.revert_degradation_threshold:
                    logger.warning(
                        f"Performance degraded {degradation:.1%} — auto-reverting weights"
                    )
                    self.revert_weights()
                    return True
                logger.info(f"Weight deployment stable: {degradation:.1%} change")
        except Exception as e:
            logger.error(f"Revert check failed: {e}")

        return False

    def _store_proposal(self, proposal: WeightProposal) -> None:
        """Store proposal in DuckDB."""
        conn = self.logger._ensure_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weight_proposals (
                proposal_id VARCHAR PRIMARY KEY,
                timestamp TIMESTAMP,
                proposal_json VARCHAR
            )
        """)
        conn.execute(
            "INSERT INTO weight_proposals VALUES (?, ?, ?)",
            [proposal.proposal_id, proposal.timestamp, json.dumps({
                "proposal_id": proposal.proposal_id,
                "timestamp": proposal.timestamp,
                "current_weights": proposal.current_weights,
                "proposed_weights": proposal.proposed_weights,
                "weight_deltas": proposal.weight_deltas,
                "confidence_level": proposal.confidence_level,
                "auto_deploy": proposal.auto_deploy,
                "sample_size": proposal.sample_size,
                "supporting_evidence": proposal.supporting_evidence,
            })],
        )

    def get_proposals(self, limit: int = 20) -> list[dict]:
        """Get all weight proposals."""
        conn = self.logger._ensure_conn()
        try:
            rows = conn.execute(
                "SELECT proposal_json FROM weight_proposals ORDER BY timestamp DESC LIMIT ?",
                [limit]
            ).fetchall()
            return [json.loads(r[0]) for r in rows]
        except Exception:
            return []
