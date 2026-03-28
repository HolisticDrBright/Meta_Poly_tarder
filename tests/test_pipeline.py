"""Tests for the full signal aggregation → risk → execution pipeline."""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta

from backend.strategies.base import (
    MarketState, OrderIntent, Side, OrderType, StrategyName, ScoredIntent,
)
from backend.aggregator.signal_aggregator import SignalAggregator
from backend.risk.engine import RiskEngine
from backend.execution.executor import OrderExecutor
from backend.state import SystemState
from backend.quant.bayesian import BayesianTracker, SignalType
from backend.quant.calibration import CalibrationTracker


def make_intent(**overrides) -> OrderIntent:
    defaults = dict(
        strategy=StrategyName.ENTROPY,
        market_id="test-mkt-1",
        condition_id="cond-1",
        question="Will X?",
        side=Side.YES,
        order_type=OrderType.LIMIT,
        price=0.35,
        size_usdc=50,
        confidence=0.8,
        reason="test",
        kl_divergence=0.10,
        kelly_fraction=0.09,
    )
    defaults.update(overrides)
    return OrderIntent(**defaults)


class TestSignalAggregator:
    def test_scores_signals(self):
        agg = SignalAggregator()
        intents = [make_intent(strategy=StrategyName.ENTROPY, confidence=0.9)]
        scored = agg.score(intents)
        assert len(scored) == 1
        assert scored[0].composite_score > 0

    def test_confluence_detection(self):
        agg = SignalAggregator()
        intents = [
            make_intent(strategy=StrategyName.ENTROPY, side=Side.YES),
            make_intent(strategy=StrategyName.ENSEMBLE_AI, side=Side.YES),
            make_intent(strategy=StrategyName.THETA, side=Side.YES),
        ]
        scored = agg.score(intents)
        # All agree on YES for same market → confluence
        for s in scored:
            assert s.intent.confluence_count == 3

    def test_priority_ordering(self):
        agg = SignalAggregator()
        intents = [
            make_intent(strategy=StrategyName.THETA, market_id="m1", confidence=0.9),
            make_intent(strategy=StrategyName.ARB, market_id="m2", confidence=0.5),
        ]
        scored = agg.score(intents)
        # ARB has higher priority (lower number) than THETA
        assert scored[0].intent.strategy == StrategyName.ARB


class TestRiskEngine:
    def test_approves_paper_trade(self):
        risk = RiskEngine(paper_trading=True)
        intent = make_intent()
        scored = ScoredIntent(intent=intent, composite_score=1.0)
        result = risk.check(scored)
        assert result.approved
        assert "PAPER" in result.reason

    def test_rejects_after_kill(self):
        risk = RiskEngine(paper_trading=True)
        risk.kill()
        intent = make_intent()
        scored = ScoredIntent(intent=intent, composite_score=1.0)
        result = risk.check(scored)
        assert not result.approved
        assert "Kill switch" in result.reason

    def test_caps_trade_size(self):
        risk = RiskEngine(paper_trading=True, max_trade_size_usdc=50)
        intent = make_intent(size_usdc=200)
        scored = ScoredIntent(intent=intent, composite_score=1.0)
        result = risk.check(scored)
        assert result.approved
        assert result.adjusted_size <= 50

    def test_rejects_low_balance(self):
        risk = RiskEngine(paper_trading=False, min_balance_usdc=100)
        risk.state.balance = 5  # below minimum
        intent = make_intent()
        scored = ScoredIntent(intent=intent, composite_score=1.0)
        result = risk.check(scored)
        assert not result.approved

    def test_daily_loss_limit(self):
        risk = RiskEngine(paper_trading=False, max_daily_loss_pct=0.10)
        risk.state.balance = 10000
        risk.state.daily_pnl = -1500  # 15% loss
        intent = make_intent()
        scored = ScoredIntent(intent=intent, composite_score=1.0)
        result = risk.check(scored)
        assert not result.approved


class TestOrderExecutor:
    @pytest.mark.asyncio
    async def test_paper_fill(self):
        executor = OrderExecutor(paper_trading=True)
        intent = make_intent()
        scored = ScoredIntent(intent=intent, composite_score=1.0, approved=True)
        result = await executor.execute(scored)
        assert result.success
        assert result.paper
        assert result.fill_price == 0.35
        assert result.fill_size == 50

    @pytest.mark.asyncio
    async def test_rejects_unapproved(self):
        executor = OrderExecutor(paper_trading=True)
        intent = make_intent()
        scored = ScoredIntent(intent=intent, composite_score=1.0, approved=False)
        result = await executor.execute(scored)
        assert not result.success

    def test_to_position(self):
        from backend.execution.executor import ExecutionResult
        executor = OrderExecutor(paper_trading=True)
        intent = make_intent()
        result = ExecutionResult(success=True, fill_price=0.35, fill_size=50)
        pos = executor.to_position(intent, result)
        assert pos is not None
        assert pos.market_id == "test-mkt-1"
        assert pos.entry_price == 0.35


class TestSystemState:
    def test_add_and_close_position(self):
        from backend.strategies.base import Position
        state = SystemState()
        pos = Position(
            market_id="m1", condition_id="c1", question="Test?",
            side=Side.YES, entry_price=0.35, size_usdc=50,
            current_price=0.40, strategy=StrategyName.ENTROPY,
        )
        state.add_position(pos)
        assert len(state.positions) == 1
        assert state.total_exposure == 50

        closed = state.close_position("m1")
        assert closed is not None
        assert len(state.positions) == 0
        assert state.total_exposure == 0

    def test_signal_tracking(self):
        state = SystemState()
        intent = make_intent()
        state.add_signal(intent)
        assert len(state.recent_signals) == 1
        assert state.recent_signals[0]["strategy"] == "entropy"

    def test_stats(self):
        state = SystemState()
        stats = state.get_stats()
        assert "balance" in stats
        assert "paper_trading" in stats
        assert stats["paper_trading"] is True


class TestBayesianTracker:
    def test_update_moves_posterior(self):
        tracker = BayesianTracker(market_id="m1", prior=0.5)
        tracker.update(SignalType.WHALE_POSITION, direction=1.0)
        assert tracker.posterior > 0.5

    def test_negative_direction(self):
        tracker = BayesianTracker(market_id="m1", prior=0.5)
        tracker.update(SignalType.WHALE_POSITION, direction=-1.0)
        assert tracker.posterior < 0.5

    def test_bounds(self):
        tracker = BayesianTracker(market_id="m1", prior=0.5)
        for _ in range(100):
            tracker.update(SignalType.JET_SIGNAL, direction=1.0, strength=2.0)
        assert tracker.posterior <= 0.99

    def test_drift_detection(self):
        tracker = BayesianTracker(market_id="m1", prior=0.5)
        tracker.update(SignalType.JET_SIGNAL, direction=1.0, strength=1.5)
        assert tracker.should_reevaluate


class TestCalibrationTracker:
    def test_brier_score_perfect(self):
        tracker = CalibrationTracker()
        tracker.add(1.0, 1)
        tracker.add(0.0, 0)
        assert tracker.brier_score() == 0.0

    def test_brier_score_worst(self):
        tracker = CalibrationTracker()
        tracker.add(1.0, 0)
        tracker.add(0.0, 1)
        assert tracker.brier_score() == 1.0

    def test_win_rate(self):
        tracker = CalibrationTracker()
        tracker.add(0.8, 1)  # correct
        tracker.add(0.3, 0)  # correct
        tracker.add(0.6, 0)  # wrong
        assert tracker.win_rate == pytest.approx(2 / 3)

    def test_calibration_bins(self):
        tracker = CalibrationTracker()
        for _ in range(10):
            tracker.add(0.8, 1)
        bins = tracker.calibration_bins(n_bins=10)
        assert len(bins) > 0
