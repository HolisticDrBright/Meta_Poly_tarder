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
    """Return the shared scheduler-owned orchestrator if available,
    otherwise fall back to a local one.

    Sharing is critical: scheduler and API must use the SAME
    DecisionLogger instance so they share one DuckDB write connection
    to data/prediction_intelligence.db. Two separate instances = two
    write connections = lock conflict the moment /analysis/trigger
    runs while the scheduler is logging a decision = crash.
    """
    global _orchestrator
    # First try the shared instance from system_state (set by scheduler)
    try:
        from backend.state import system_state
        shared = getattr(system_state, "_pi_orchestrator", None)
        if shared is not None:
            return shared
    except Exception:
        pass
    # Fall back to a local one (for standalone API use without scheduler)
    if _orchestrator is None:
        _orchestrator = LoopOrchestrator()
    return _orchestrator


def _safe_report_fields(report_dict: Optional[dict]) -> dict:
    """Sanitize a report dict so every field has a JSON-safe, frontend-friendly
    value. The frontend IntelligenceTab crashes if it encounters NaN, Infinity,
    or unexpected None in places it expects numbers.
    """
    if not isinstance(report_dict, dict):
        return {}
    import math as _math
    def _clean(v):
        if isinstance(v, float):
            if _math.isnan(v) or _math.isinf(v):
                return None
            return v
        if isinstance(v, dict):
            return {k: _clean(val) for k, val in v.items()}
        if isinstance(v, list):
            return [_clean(x) for x in v]
        return v
    return _clean(report_dict)


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
#
# Every endpoint below is bulletproofed with try/except around the
# orchestrator call + _safe_report_fields() sanitation. The goal is
# that no matter what happens in the analyzer (DuckDB lock, empty
# tables, NaN/Inf values, divide-by-zero), the endpoint returns a
# well-formed JSON response with an "error" field, never a 500.
# Before this the trigger endpoint would 500 when the scheduler held
# the DuckDB lock, which crashed the Intelligence tab with a
# "client-side exception" when it tried to parse an HTML error page
# as JSON.

@router.get("/analysis/latest")
async def get_latest_analysis():
    try:
        orch = _get_orchestrator()
        report = orch.analyzer.get_latest_report()
        if not report:
            return {
                "status": "no analysis reports yet",
                "scored_outcomes": orch.decision_logger.get_scored_count(),
                "total_decisions": orch.decision_logger.get_total_count(),
            }
        return _safe_report_fields(report)
    except Exception as e:
        logger.error(f"get_latest_analysis failed: {e}")
        return {"status": "error", "error": str(e)[:200]}


@router.get("/analysis/history")
async def get_analysis_history():
    try:
        orch = _get_orchestrator()
        reports = orch.analyzer.get_all_reports()
        return {"reports": [_safe_report_fields(r) for r in reports]}
    except Exception as e:
        logger.error(f"get_analysis_history failed: {e}")
        return {"reports": [], "error": str(e)[:200]}


@router.post("/analysis/trigger")
async def trigger_analysis():
    try:
        orch = _get_orchestrator()
        scored = orch.decision_logger.get_scored_count()
        if scored < 10:
            return {
                "status": "not enough data",
                "scored_outcomes": scored,
                "minimum": 10,
                "overall_brier": None,
                "report_id": None,
                "optimization_ready": False,
            }
        report = orch.analyzer.run_analysis()
        # Try to auto-propose weights if optimization is ready
        proposal_info = None
        try:
            report_dict = {
                "scored_outcomes": report.scored_outcomes,
                "weight_recommendations": report.weight_recommendations,
                "overall_brier": report.overall_brier,
            }
            proposal = orch.adjuster.propose_weights(report_dict)
            if proposal is not None:
                proposal_info = {
                    "proposal_id": proposal.proposal_id,
                    "confidence": proposal.confidence_level,
                    "auto_deploy": proposal.auto_deploy,
                    "changes": len(proposal.weight_deltas),
                }
                if proposal.auto_deploy:
                    orch.adjuster.deploy_weights(proposal)
                    proposal_info["deployed"] = True
        except Exception as e:
            logger.warning(f"trigger proposal step failed: {e}")

        import math as _math
        brier = report.overall_brier
        if isinstance(brier, float) and (_math.isnan(brier) or _math.isinf(brier)):
            brier = None
        return {
            "status": "analysis complete",
            "report_id": report.report_id,
            "overall_brier": brier,
            "scored_outcomes": report.scored_outcomes,
            "total_decisions": report.total_decisions,
            "optimization_ready": report.optimization_ready,
            "proposal": proposal_info,
        }
    except Exception as e:
        logger.error(f"trigger_analysis failed: {e}")
        return {
            "status": "error",
            "error": str(e)[:200],
            "scored_outcomes": 0,
            "overall_brier": None,
            "report_id": None,
            "optimization_ready": False,
        }


# ── Calibration ─────────────────────────────────────────────

@router.get("/calibration")
async def get_calibration():
    try:
        orch = _get_orchestrator()
        report = orch.analyzer.get_latest_report()
        if not report:
            return {"status": "no data", "buckets": [], "overall_brier": None}
        safe = _safe_report_fields(report)
        return {
            "buckets": safe.get("calibration_buckets", []) or [],
            "overall_brier": safe.get("overall_brier"),
        }
    except Exception as e:
        logger.error(f"get_calibration failed: {e}")
        return {"buckets": [], "overall_brier": None, "error": str(e)[:200]}


@router.get("/calibration/by-theme")
async def calibration_by_theme():
    try:
        orch = _get_orchestrator()
        report = orch.analyzer.get_latest_report()
        if not report:
            return {"themes": []}
        safe = _safe_report_fields(report)
        return {"themes": safe.get("theme_performance", []) or []}
    except Exception as e:
        logger.error(f"calibration_by_theme failed: {e}")
        return {"themes": [], "error": str(e)[:200]}


@router.get("/calibration/by-regime")
async def calibration_by_regime():
    try:
        orch = _get_orchestrator()
        report = orch.analyzer.get_latest_report()
        if not report:
            return {"regimes": []}
        safe = _safe_report_fields(report)
        return {"regimes": safe.get("regime_performance", []) or []}
    except Exception as e:
        logger.error(f"calibration_by_regime failed: {e}")
        return {"regimes": [], "error": str(e)[:200]}


# ── Performance ─────────────────────────────────────────────

@router.get("/performance/summary")
async def performance_summary():
    try:
        orch = _get_orchestrator()
        scored = orch.decision_logger.get_scored_count()
        total = orch.decision_logger.get_total_count()
        report = orch.analyzer.get_latest_report()
        brier = None
        if report:
            import math as _math
            b = report.get("overall_brier")
            if isinstance(b, (int, float)) and not _math.isnan(float(b)) and not _math.isinf(float(b)):
                brier = float(b)
        return {
            "total_decisions": total,
            "scored_outcomes": scored,
            "overall_brier": brier,
            "optimization_active": scored >= 50,
        }
    except Exception as e:
        logger.error(f"performance_summary failed: {e}")
        return {
            "total_decisions": 0,
            "scored_outcomes": 0,
            "overall_brier": None,
            "optimization_active": False,
            "error": str(e)[:200],
        }


@router.get("/performance/errors")
async def performance_errors():
    try:
        orch = _get_orchestrator()
        report = orch.analyzer.get_latest_report()
        if not report:
            return {"error_counts": {}, "top_errors": []}
        safe = _safe_report_fields(report)
        return {
            "error_counts": safe.get("error_counts", {}) or {},
            "top_errors": safe.get("top_errors", []) or [],
        }
    except Exception as e:
        logger.error(f"performance_errors failed: {e}")
        return {"error_counts": {}, "top_errors": [], "error": str(e)[:200]}


@router.get("/performance/signals")
async def performance_signals():
    try:
        orch = _get_orchestrator()
        report = orch.analyzer.get_latest_report()
        if not report:
            return {"signal_attribution": {}, "weight_recommendations": {}}
        safe = _safe_report_fields(report)
        return {
            "signal_attribution": safe.get("signal_attribution", {}) or {},
            "weight_recommendations": safe.get("weight_recommendations", {}) or {},
        }
    except Exception as e:
        logger.error(f"performance_signals failed: {e}")
        return {"signal_attribution": {}, "weight_recommendations": {}, "error": str(e)[:200]}


# ── Weight management ───────────────────────────────────────

@router.get("/weights/current")
async def current_weights():
    try:
        orch = _get_orchestrator()
        return {
            "weights": orch.adjuster.get_active_weights() or {},
            "thresholds": orch.adjuster.get_active_thresholds() or {},
        }
    except Exception as e:
        logger.error(f"current_weights failed: {e}")
        return {"weights": {}, "thresholds": {}, "error": str(e)[:200]}


@router.get("/weights/proposals")
async def list_proposals():
    try:
        orch = _get_orchestrator()
        return {"proposals": orch.adjuster.get_proposals() or []}
    except Exception as e:
        logger.error(f"list_proposals failed: {e}")
        return {"proposals": [], "error": str(e)[:200]}


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
