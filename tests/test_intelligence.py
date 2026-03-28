"""Tests for intelligence layer upgrades."""

import pytest
from backend.quant.opportunity_score import (
    compute_opportunity_score,
    OpportunityAction,
)
from backend.quant.edge_classifier import (
    classify_edge_heuristic,
    EdgeType,
)
from backend.quant.regime_detector import (
    detect_regime,
    Regime,
)
from backend.quant.error_taxonomy import (
    ErrorTracker,
    Postmortem,
    ForecastRecord,
    ErrorType,
)
from backend.agents.resolution_rules import (
    ResolutionRulesAgent,
)
from datetime import datetime, timezone


class TestOpportunityScore:
    def test_high_score_with_strong_signals(self):
        result = compute_opportunity_score(
            model_prob=0.70,
            market_price=0.35,
            evidence_quality=0.9,
            resolution_clarity=0.95,
            liquidity=200_000,
            regime_fit=0.8,
            calibration_score=0.8,
            hours_to_close=24,
            spread=0.01,
        )
        assert result.score >= 60
        assert result.action in (OpportunityAction.HIGH_PRIORITY, OpportunityAction.EXCEPTIONAL, OpportunityAction.PAPER_TRADE)

    def test_hard_block_low_resolution_clarity(self):
        result = compute_opportunity_score(
            model_prob=0.70, market_price=0.35,
            resolution_clarity=0.2,  # below 0.4 threshold
        )
        assert result.score == 0.0
        assert result.action == OpportunityAction.HARD_BLOCK
        assert "ambiguous" in result.hard_block_reason.lower()

    def test_hard_block_low_liquidity(self):
        result = compute_opportunity_score(
            model_prob=0.70, market_price=0.35,
            liquidity=500,  # below 10k threshold
        )
        assert result.score == 0.0

    def test_hard_block_spread_eats_edge(self):
        result = compute_opportunity_score(
            model_prob=0.52, market_price=0.50,  # 2% edge
            spread=0.03,  # 3% spread > 50% of edge
        )
        assert result.score == 0.0

    def test_no_trade_weak_everything(self):
        result = compute_opportunity_score(
            model_prob=0.51, market_price=0.50,
            evidence_quality=0.3,
            resolution_clarity=0.5,
            liquidity=20_000,
        )
        assert result.action in (OpportunityAction.NO_TRADE, OpportunityAction.WATCHLIST, OpportunityAction.HARD_BLOCK)


class TestEdgeClassifier:
    def test_structural_arb(self):
        result = classify_edge_heuristic(
            model_prob=0.5, market_price=0.5,
            arb_edge=0.03,
        )
        assert result.edge_type == EdgeType.STRUCTURAL

    def test_rules_edge_on_headline_mismatch(self):
        result = classify_edge_heuristic(
            model_prob=0.6, market_price=0.4,
            headline_mismatch=True,
        )
        assert result.edge_type == EdgeType.RULES

    def test_information_edge(self):
        result = classify_edge_heuristic(
            model_prob=0.7, market_price=0.5,
            evidence_quality=0.85,
        )
        assert result.edge_type == EdgeType.INFORMATION

    def test_fake_edge_tiny_magnitude(self):
        result = classify_edge_heuristic(
            model_prob=0.51, market_price=0.50,
        )
        assert result.edge_type == EdgeType.FAKE
        assert result.blocked

    def test_base_rate_low_evidence(self):
        result = classify_edge_heuristic(
            model_prob=0.7, market_price=0.5,
            evidence_quality=0.2,
        )
        assert result.edge_type == EdgeType.BASE_RATE


class TestRegimeDetector:
    def test_liquidity_vacuum(self):
        r = detect_regime(spread_pct=0.08, volume_24h=1000, liquidity=3000, hours_to_close=100)
        assert r.regime == Regime.LIQUIDITY_VACUUM

    def test_event_countdown(self):
        r = detect_regime(spread_pct=0.02, volume_24h=10000, liquidity=50000, hours_to_close=12)
        assert r.regime == Regime.EVENT_COUNTDOWN

    def test_information_driven(self):
        r = detect_regime(
            spread_pct=0.02, volume_24h=50000, liquidity=100000,
            hours_to_close=168, volume_spike=True, has_news_catalyst=True,
        )
        assert r.regime == Regime.INFORMATION_DRIVEN

    def test_rumor_driven(self):
        r = detect_regime(
            spread_pct=0.02, volume_24h=50000, liquidity=100000,
            hours_to_close=168, volume_spike=True, has_news_catalyst=False,
        )
        assert r.regime == Regime.RUMOR_DRIVEN

    def test_consensus_grind_default(self):
        r = detect_regime(spread_pct=0.02, volume_24h=10000, liquidity=50000, hours_to_close=200)
        assert r.regime == Regime.CONSENSUS_GRIND

    def test_weights_sum_to_one(self):
        r = detect_regime(spread_pct=0.02, volume_24h=10000, liquidity=50000, hours_to_close=200)
        w = r.weights
        total = w.evidence + w.base_rate + w.sentiment + w.microstructure
        assert total == pytest.approx(1.0, abs=0.01)


class TestErrorTracker:
    def test_error_counting(self):
        tracker = ErrorTracker()
        for _ in range(5):
            tracker.add_postmortem(Postmortem(
                trade_id="t1", market_id="m1", strategy="entropy",
                model_prob=0.7, market_price_at_entry=0.5,
                outcome=0.0, pnl=-50, brier_score=0.49,
                error_type=ErrorType.OVERCONFIDENCE,
            ))
        counts = tracker.error_counts()
        assert counts["overconfidence"] == 5

    def test_brier_by_strategy(self):
        tracker = ErrorTracker()
        for i in range(10):
            tracker.add_forecast(ForecastRecord(
                market_id=f"m{i}",
                prediction_date=datetime.now(timezone.utc),
                model_probability=0.7,
                market_price_at_entry=0.5,
                edge_type="information_edge",
                strategy_source="entropy",
                outcome=1.0 if i < 7 else 0.0,
                brier_score=(0.7 - (1.0 if i < 7 else 0.0)) ** 2,
            ))
        brier = tracker.strategy_brier_scores()
        assert "entropy" in brier
        assert 0 < brier["entropy"] < 1

    def test_kelly_adjustment(self):
        tracker = ErrorTracker()
        # Add 25 losses
        for i in range(25):
            tracker.add_postmortem(Postmortem(
                trade_id=f"t{i}", market_id=f"m{i}", strategy="arb",
                model_prob=0.6, market_price_at_entry=0.5,
                outcome=0.0, pnl=-10, brier_score=0.36,
            ))
        adjusted = tracker.kelly_adjustment("arb", base_kelly=0.25)
        assert adjusted == 0.25 * 0.75  # 25% reduction after 20+ losses

    def test_recalibrated_weights(self):
        tracker = ErrorTracker()
        # Good strategy
        for i in range(10):
            tracker.add_forecast(ForecastRecord(
                market_id=f"m{i}", prediction_date=datetime.now(timezone.utc),
                model_probability=0.7, market_price_at_entry=0.5,
                edge_type="info", strategy_source="good",
                outcome=1.0, brier_score=0.09,
            ))
        # Bad strategy
        for i in range(10):
            tracker.add_forecast(ForecastRecord(
                market_id=f"n{i}", prediction_date=datetime.now(timezone.utc),
                model_probability=0.7, market_price_at_entry=0.5,
                edge_type="info", strategy_source="bad",
                outcome=0.0, brier_score=0.49,
            ))
        weights = tracker.recalibrated_weights()
        assert weights["good"] > weights["bad"]


class TestResolutionRulesAgent:
    def test_wording_trap_detection(self):
        agent = ResolutionRulesAgent()
        traps = agent._check_wording_traps(
            "Will the S&P 500 close above 6000 by at least 50 points on EDT?"
        )
        assert len(traps) >= 2  # "at least" + "EDT"

    def test_no_traps_clear_question(self):
        agent = ResolutionRulesAgent()
        traps = agent._check_wording_traps("Will it rain tomorrow?")
        assert len(traps) == 0

    @pytest.mark.asyncio
    async def test_local_only_analysis(self):
        agent = ResolutionRulesAgent(max_ambiguity=0.6)
        memo = await agent.analyze(
            question="Will BTC close above $100k by at least $1000 in official trading?",
            description="Must close above on at least one UTC trading day.",
        )
        assert memo.ambiguity_score > 0
        assert len(memo.wording_traps) > 0

    @pytest.mark.asyncio
    async def test_clean_market_passes(self):
        agent = ResolutionRulesAgent(max_ambiguity=0.6)
        memo = await agent.analyze(question="Will it rain in NYC tomorrow?")
        assert not memo.blocked
