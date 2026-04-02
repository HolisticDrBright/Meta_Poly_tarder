"""
FastAPI endpoints for the Prediction Intelligence Layer.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from prediction_intelligence.orchestrator import LoopOrchestrator

logger = logging.getLogger(__name__)
router = APIRouter()

_orchestrator: Optional[LoopOrchestrator] = None


def _get_orchestrator() -> LoopOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = LoopOrchestrator()
    return _orchestrator


class DecisionInput(BaseModel):
    market_id: str
    market_title: str = ""
    market_theme: str = ""
    implied_probability: float = 0.5
    fair_probability: float = 0.5
    edge_estimate: float = 0.0
    opportunity_score: float = 0.0
    classification: str = "NO-TRADE"


class OutcomeInput(BaseModel):
    decision_id: str
    market_id: str
    actual_outcome: float
    paper_pnl: float = 0.0
    resolution_source: str = ""


# ── Decision logging ────────────────────────────────────────

@router.post("/decisions")
async def log_decision(inp: DecisionInput):
    orch = _get_orchestrator()
    from prediction_intelligence.logger import DecisionRecord
    record = DecisionRecord(
        market_id=inp.market_id,
        market_title=inp.market_title,
        market_theme=inp.market_theme,
        implied_probability=inp.implied_probability,
        fair_probability=inp.fair_probability,
        edge_estimate=inp.edge_estimate,
        opportunity_score=inp.opportunity_score,
        classification=inp.classification,
    )
    decision_id = orch.decision_logger.log_decision(record)
    return {"decision_id": decision_id, "status": "logged"}


@router.post("/outcomes")
async def log_outcome(inp: OutcomeInput):
    orch = _get_orchestrator()
    from prediction_intelligence.logger import OutcomeRecord
    from datetime import datetime, timezone
    forecast_error = 0.0
    brier = 0.0

    # Try to compute scores from the original decision
    rows = orch.decision_logger.query(
        "SELECT fair_probability FROM decision_log WHERE decision_id = ?",
        [inp.decision_id]
    )
    if rows:
        fair_p = rows[0]["fair_probability"]
        forecast_error = abs(fair_p - inp.actual_outcome)
        brier = (fair_p - inp.actual_outcome) ** 2

    outcome = OutcomeRecord(
        decision_id=inp.decision_id,
        market_id=inp.market_id,
        resolution_timestamp=datetime.now(timezone.utc).isoformat(),
        actual_outcome=inp.actual_outcome,
        forecast_error=forecast_error,
        brier_score=brier,
        paper_pnl=inp.paper_pnl,
        resolution_source=inp.resolution_source,
    )
    orch.decision_logger.log_outcome(outcome)
    return {"status": "logged", "brier_score": brier, "forecast_error": forecast_error}


@router.post("/outcomes/backfill")
async def backfill_outcomes():
    """Trigger outcome backfill from Polymarket."""
    orch = _get_orchestrator()
    unscored = orch.decision_logger.get_unscored_decisions()
    return {"pending_decisions": len(unscored), "status": "backfill not yet automated"}


# ── Analysis ────────────────────────────────────────────────

@router.get("/analysis/latest")
async def get_latest_analysis():
    orch = _get_orchestrator()
    report = orch.analyzer.get_latest_report()
    if not report:
        return {"status": "no analysis reports yet", "scored_outcomes": orch.decision_logger.get_scored_count()}
    return report


@router.get("/analysis/history")
async def get_analysis_history():
    orch = _get_orchestrator()
    return {"reports": orch.analyzer.get_all_reports()}


@router.post("/analysis/trigger")
async def trigger_analysis():
    orch = _get_orchestrator()
    scored = orch.decision_logger.get_scored_count()
    if scored < 10:
        return {"status": "not enough data", "scored": scored, "minimum": 10}
    report = orch.analyzer.run_analysis()
    return {
        "status": "analysis complete",
        "report_id": report.report_id,
        "overall_brier": report.overall_brier,
        "scored_outcomes": report.scored_outcomes,
        "optimization_ready": report.optimization_ready,
    }


# ── Calibration ─────────────────────────────────────────────

@router.get("/calibration")
async def get_calibration():
    orch = _get_orchestrator()
    report = orch.analyzer.get_latest_report()
    if not report:
        return {"status": "no data", "buckets": []}
    return {"buckets": report.get("calibration_buckets", []), "overall_brier": report.get("overall_brier")}


@router.get("/calibration/by-theme")
async def calibration_by_theme():
    orch = _get_orchestrator()
    report = orch.analyzer.get_latest_report()
    if not report:
        return {"status": "no data"}
    return {"themes": report.get("theme_performance", [])}


@router.get("/calibration/by-regime")
async def calibration_by_regime():
    orch = _get_orchestrator()
    report = orch.analyzer.get_latest_report()
    if not report:
        return {"status": "no data"}
    return {"regimes": report.get("regime_performance", [])}


# ── Performance ─────────────────────────────────────────────

@router.get("/performance/summary")
async def performance_summary():
    orch = _get_orchestrator()
    scored = orch.decision_logger.get_scored_count()
    total = orch.decision_logger.get_total_count()
    report = orch.analyzer.get_latest_report()
    return {
        "total_decisions": total,
        "scored_outcomes": scored,
        "overall_brier": report.get("overall_brier") if report else None,
        "optimization_active": scored >= 50,
    }


@router.get("/performance/errors")
async def performance_errors():
    orch = _get_orchestrator()
    report = orch.analyzer.get_latest_report()
    if not report:
        return {"status": "no data"}
    return {
        "error_counts": report.get("error_counts", {}),
        "top_errors": report.get("top_errors", []),
    }


@router.get("/performance/signals")
async def performance_signals():
    orch = _get_orchestrator()
    report = orch.analyzer.get_latest_report()
    if not report:
        return {"status": "no data"}
    return {
        "signal_attribution": report.get("signal_attribution", {}),
        "weight_recommendations": report.get("weight_recommendations", {}),
    }


# ── Weight management ───────────────────────────────────────

@router.get("/weights/current")
async def current_weights():
    orch = _get_orchestrator()
    return {
        "weights": orch.adjuster.get_active_weights(),
        "thresholds": orch.adjuster.get_active_thresholds(),
    }


@router.get("/weights/proposals")
async def list_proposals():
    orch = _get_orchestrator()
    return {"proposals": orch.adjuster.get_proposals()}


@router.post("/weights/proposals/{proposal_id}/deploy")
async def deploy_proposal(proposal_id: str):
    orch = _get_orchestrator()
    proposals = orch.adjuster.get_proposals()
    found = None
    for p in proposals:
        if p.get("proposal_id") == proposal_id:
            found = p
            break
    if not found:
        raise HTTPException(404, "Proposal not found")
    # Create a minimal WeightProposal for deployment
    from prediction_intelligence.adjuster import WeightProposal
    wp = WeightProposal(
        proposal_id=found["proposal_id"],
        timestamp=found["timestamp"],
        analysis_report_id="",
        current_weights=found["current_weights"],
        proposed_weights=found["proposed_weights"],
        weight_deltas=found["weight_deltas"],
        current_no_trade_threshold=35.0,
        proposed_no_trade_threshold=35.0,
        current_confidence_floor=0.4,
        proposed_confidence_floor=0.4,
        revert_checkpoint={"weights": found["current_weights"]},
    )
    success = orch.adjuster.deploy_weights(wp)
    return {"status": "deployed" if success else "failed"}


@router.post("/weights/revert")
async def revert_weights():
    orch = _get_orchestrator()
    success = orch.adjuster.revert_weights()
    return {"status": "reverted" if success else "no checkpoint to revert to"}


# ── Health ──────────────────────────────────────────────────

@router.get("/health")
async def intelligence_health():
    orch = _get_orchestrator()
    return orch.get_health()
