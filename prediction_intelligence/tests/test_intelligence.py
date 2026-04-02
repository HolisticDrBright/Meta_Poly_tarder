"""Tests for the Prediction Intelligence Layer."""

import pytest
import os
import tempfile
from datetime import datetime, timezone

from prediction_intelligence.config import PredictionIntelligenceConfig
from prediction_intelligence.logger import DecisionLogger, DecisionRecord, OutcomeRecord
from prediction_intelligence.analyzer import RetrospectiveAnalyzer
from prediction_intelligence.adjuster import WeightAdjuster
from prediction_intelligence.orchestrator import LoopOrchestrator
from prediction_intelligence.integration import PredictionIntelligenceIntegration


@pytest.fixture
def tmp_db():
    """Create a temporary path for DuckDB (file must not exist yet)."""
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)  # DuckDB needs to create the file itself
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def decision_logger(tmp_db):
    dl = DecisionLogger(tmp_db)
    yield dl
    dl.close()


def make_decision(market_id: str = "test-1", fair_p: float = 0.6, implied_p: float = 0.5,
                  theme: str = "politics", regime: str = "information", classification: str = "PAPER-TRADE") -> DecisionRecord:
    return DecisionRecord(
        market_id=market_id,
        market_title=f"Test market {market_id}",
        market_theme=theme,
        implied_probability=implied_p,
        fair_probability=fair_p,
        edge_estimate=fair_p - implied_p,
        opportunity_score=60,
        classification=classification,
        model_confidence=0.7,
        regime_label=regime,
        regime_confidence=0.8,
        base_rate_prior=0.5,
        evidence_strength_score=60,
        sentiment_crowding_score=30,
        red_team_confidence_haircut=0.1,
        fill_realism_score=70,
        paper_position_size=100,
        paper_entry_price=implied_p,
        risk_approved=True,
    )


class TestDecisionLogger:
    def test_log_and_count(self, decision_logger):
        record = make_decision()
        did = decision_logger.log_decision(record)
        assert did
        assert decision_logger.get_total_count() == 1

    def test_log_outcome(self, decision_logger):
        did = decision_logger.log_decision(make_decision())
        outcome = OutcomeRecord(
            decision_id=did, market_id="test-1",
            resolution_timestamp=datetime.now(timezone.utc).isoformat(),
            actual_outcome=1.0, forecast_error=0.4, brier_score=0.16,
            paper_pnl=50.0,
        )
        decision_logger.log_outcome(outcome)
        assert decision_logger.get_scored_count() == 1

    def test_unscored_decisions(self, decision_logger):
        did1 = decision_logger.log_decision(make_decision("m1"))
        did2 = decision_logger.log_decision(make_decision("m2"))
        outcome = OutcomeRecord(
            decision_id=did1, market_id="m1",
            resolution_timestamp=datetime.now(timezone.utc).isoformat(),
            actual_outcome=1.0, forecast_error=0.4, brier_score=0.16,
        )
        decision_logger.log_outcome(outcome)
        unscored = decision_logger.get_unscored_decisions()
        assert len(unscored) == 1
        assert unscored[0]["market_id"] == "m2"


class TestAnalyzer:
    def _populate(self, dl, n=60):
        """Populate with n decisions and outcomes."""
        import random
        random.seed(42)
        dids = []
        for i in range(n):
            theme = random.choice(["politics", "crypto", "sports"])
            regime = random.choice(["information", "rumor", "consensus"])
            fair_p = random.uniform(0.2, 0.8)
            implied_p = fair_p + random.uniform(-0.1, 0.1)
            did = dl.log_decision(make_decision(
                market_id=f"m{i}", fair_p=fair_p, implied_p=implied_p,
                theme=theme, regime=regime,
            ))
            dids.append((did, f"m{i}", fair_p))
        # Score them
        for did, mid, fair_p in dids:
            actual = 1.0 if random.random() < fair_p else 0.0
            error = abs(fair_p - actual)
            brier = (fair_p - actual) ** 2
            pnl = 10 if (fair_p > 0.5 and actual == 1) or (fair_p < 0.5 and actual == 0) else -10
            dl.log_outcome(OutcomeRecord(
                decision_id=did, market_id=mid,
                resolution_timestamp=datetime.now(timezone.utc).isoformat(),
                actual_outcome=actual, forecast_error=error,
                brier_score=brier, paper_pnl=pnl,
            ))

    def test_should_run(self, decision_logger):
        analyzer = RetrospectiveAnalyzer(decision_logger)
        assert not analyzer.should_run()  # No data
        self._populate(decision_logger, 60)
        assert analyzer.should_run()

    def test_run_analysis(self, decision_logger):
        self._populate(decision_logger, 60)
        analyzer = RetrospectiveAnalyzer(decision_logger)
        report = analyzer.run_analysis()
        assert report.scored_outcomes == 60
        assert report.overall_brier > 0
        assert len(report.calibration_buckets) > 0
        assert len(report.theme_performance) > 0
        assert report.optimization_ready

    def test_analysis_stored(self, decision_logger):
        self._populate(decision_logger, 60)
        analyzer = RetrospectiveAnalyzer(decision_logger)
        analyzer.run_analysis()
        latest = analyzer.get_latest_report()
        assert latest is not None
        assert latest["scored_outcomes"] == 60


class TestWeightAdjuster:
    def test_default_weights(self, decision_logger):
        adj = WeightAdjuster(decision_logger)
        w = adj.get_active_weights()
        assert "base_rate" in w
        assert sum(w.values()) == pytest.approx(1.0, abs=0.01)

    def test_propose_needs_minimum(self, decision_logger):
        adj = WeightAdjuster(decision_logger)
        result = adj.propose_weights({"scored_outcomes": 10, "weight_recommendations": {"base_rate": 0.5}})
        assert result is None  # Not enough data

    def test_propose_with_data(self, tmp_db):
        dl = DecisionLogger(tmp_db)
        # Populate enough data
        import random
        random.seed(42)
        for i in range(60):
            fair_p = random.uniform(0.3, 0.7)
            did = dl.log_decision(make_decision(f"m{i}", fair_p=fair_p))
            actual = 1.0 if random.random() < fair_p else 0.0
            dl.log_outcome(OutcomeRecord(
                decision_id=did, market_id=f"m{i}",
                resolution_timestamp=datetime.now(timezone.utc).isoformat(),
                actual_outcome=actual,
                forecast_error=abs(fair_p - actual),
                brier_score=(fair_p - actual) ** 2,
                paper_pnl=10 if actual == 1 else -5,
            ))

        analyzer = RetrospectiveAnalyzer(dl)
        report = analyzer.run_analysis()

        adj = WeightAdjuster(dl)
        proposal = adj.propose_weights({
            "report_id": report.report_id,
            "scored_outcomes": 60,
            "weight_recommendations": report.weight_recommendations,
        })
        # May or may not produce a proposal depending on recommendations
        if proposal:
            assert abs(sum(proposal.proposed_weights.values()) - 1.0) < 0.01
            for delta in proposal.weight_deltas.values():
                assert abs(delta) <= 0.05 + 0.001  # max change per cycle
        dl.close()


class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_cycle(self, tmp_db):
        orch = LoopOrchestrator(tmp_db)
        result = await orch.run_cycle()
        assert "timestamp" in result
        assert "analysis_produced" in result
        orch.close()

    def test_health(self, tmp_db):
        orch = LoopOrchestrator(tmp_db)
        health = orch.get_health()
        assert "total_decisions" in health
        assert "optimization_active" in health
        orch.close()


class TestIntegration:
    def test_log_and_retrieve(self, tmp_db):
        integration = PredictionIntelligenceIntegration(tmp_db)
        did = integration.log_completed_analysis({
            "market_id": "test-1",
            "market_title": "Test?",
            "category": "politics",
            "market_price": 0.5,
            "model_probability": 0.65,
            "confidence": 0.8,
            "action": "BUY_YES",
            "size_usdc": 50,
            "price": 0.5,
        })
        assert did
        weights = integration.get_active_weights()
        assert isinstance(weights, dict)
        integration.close()
