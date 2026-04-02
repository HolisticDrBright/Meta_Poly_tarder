"""
Loop Orchestrator — ties the entire prediction intelligence system together.

Runs on a 6-hour cycle:
1. Backfill outcomes for resolved markets
2. Check if retrospective analysis should run
3. If yes, produce weight proposals
4. Auto-deploy high-confidence proposals
5. Monitor active deployments for revert
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from prediction_intelligence.config import pi_config
from prediction_intelligence.logger import DecisionLogger
from prediction_intelligence.analyzer import RetrospectiveAnalyzer
from prediction_intelligence.adjuster import WeightAdjuster

logger = logging.getLogger(__name__)


class LoopOrchestrator:
    """Orchestrates the prediction intelligence feedback loop."""

    def __init__(self, db_path: str | None = None) -> None:
        self.decision_logger = DecisionLogger(db_path)
        self.analyzer = RetrospectiveAnalyzer(self.decision_logger)
        self.adjuster = WeightAdjuster(self.decision_logger)
        self._last_cycle: Optional[datetime] = None

    async def run_cycle(self) -> dict:
        """Run one complete intelligence cycle."""
        cycle_start = datetime.now(timezone.utc)
        result = {
            "timestamp": cycle_start.isoformat(),
            "outcomes_backfilled": 0,
            "analysis_produced": False,
            "proposal_produced": False,
            "weights_deployed": False,
            "revert_triggered": False,
        }

        try:
            # Step 1: Backfill outcomes (check for resolved markets)
            # This would call Polymarket API — for now just report status
            unscored = self.decision_logger.get_unscored_decisions()
            result["pending_outcomes"] = len(unscored)

            # Step 2: Check if analysis should run
            if self.analyzer.should_run():
                report = self.analyzer.run_analysis()
                result["analysis_produced"] = True
                result["analysis_id"] = report.report_id
                result["overall_brier"] = report.overall_brier
                result["scored_outcomes"] = report.scored_outcomes

                # Step 3: Propose weights if optimization is ready
                if report.optimization_ready:
                    report_dict = {
                        "report_id": report.report_id,
                        "scored_outcomes": report.scored_outcomes,
                        "weight_recommendations": report.weight_recommendations,
                    }
                    proposal = self.adjuster.propose_weights(report_dict)
                    if proposal:
                        result["proposal_produced"] = True
                        result["proposal_id"] = proposal.proposal_id
                        result["auto_deploy"] = proposal.auto_deploy

                        # Step 4: Auto-deploy if confidence is high enough
                        if proposal.auto_deploy:
                            deployed = self.adjuster.deploy_weights(proposal)
                            result["weights_deployed"] = deployed

            # Step 5: Check for revert on active deployments
            reverted = self.adjuster.check_revert()
            result["revert_triggered"] = reverted

        except Exception as e:
            logger.error(f"Intelligence cycle failed: {e}")
            result["error"] = str(e)

        self._last_cycle = cycle_start
        logger.info(f"Intelligence cycle complete: {result}")
        return result

    def get_health(self) -> dict:
        """Get system health status."""
        scored = self.decision_logger.get_scored_count()
        total = self.decision_logger.get_total_count()
        unscored = total - scored

        return {
            "total_decisions": total,
            "scored_outcomes": scored,
            "pending_outcomes": unscored,
            "last_cycle": self._last_cycle.isoformat() if self._last_cycle else None,
            "active_weights_version": self.adjuster.get_active_weights(),
            "optimization_active": scored >= pi_config.min_outcomes_for_optimization,
            "cold_start_progress": f"{scored}/{pi_config.min_outcomes_for_optimization}",
            "cycle_interval_hours": pi_config.cycle_interval_hours,
        }

    def close(self) -> None:
        self.decision_logger.close()
