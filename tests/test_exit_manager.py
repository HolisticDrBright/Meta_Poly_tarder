"""Tests for position exit logic — take-profit, stop-loss, resolution, trailing."""

import pytest
from datetime import datetime, timezone, timedelta

from backend.execution.exit_manager import ExitManager, ExitRule, ExitSignal
from backend.strategies.base import MarketState, Position, Side, StrategyName


def make_position(**overrides) -> Position:
    defaults = dict(
        market_id="m1", condition_id="c1", question="Test?",
        side=Side.YES, entry_price=0.40, size_usdc=100,
        current_price=0.40, strategy=StrategyName.ENTROPY,
    )
    defaults.update(overrides)
    return Position(**defaults)


def make_market(**overrides) -> MarketState:
    defaults = dict(
        market_id="m1", condition_id="c1", question="Test?", category="test",
        yes_price=0.40, no_price=0.60, mid_price=0.50, spread=0.02,
        best_bid=0.39, best_ask=0.41, bid_depth=100, ask_depth=100,
        liquidity=50000, volume_24h=5000,
        end_date=datetime.now(timezone.utc) + timedelta(days=7),
    )
    defaults.update(overrides)
    return MarketState(**defaults)


class TestExitManager:
    def test_take_profit_triggers(self):
        mgr = ExitManager(ExitRule(take_profit_pct=0.20))
        pos = make_position(entry_price=0.40, current_price=0.55, size_usdc=100)
        # PnL = (0.55 - 0.40) * 100 = 15, pct = 15 / (0.40*100) = 37.5%
        market = make_market(yes_price=0.55)
        signals = mgr.check_exits([pos], [market])
        assert len(signals) == 1
        assert "TAKE PROFIT" in signals[0].reason

    def test_stop_loss_triggers(self):
        mgr = ExitManager(ExitRule(stop_loss_pct=-0.15))
        pos = make_position(entry_price=0.40, current_price=0.30, size_usdc=100)
        market = make_market(yes_price=0.30)
        signals = mgr.check_exits([pos], [market])
        assert len(signals) == 1
        assert "STOP LOSS" in signals[0].reason
        assert signals[0].urgency == "immediate"

    def test_no_exit_in_normal_range(self):
        mgr = ExitManager(ExitRule(take_profit_pct=0.30, stop_loss_pct=-0.20))
        pos = make_position(entry_price=0.40, current_price=0.42, size_usdc=100)
        market = make_market(yes_price=0.42)
        signals = mgr.check_exits([pos], [market])
        assert len(signals) == 0

    def test_resolution_exit(self):
        mgr = ExitManager(ExitRule(resolution_hours=2.0))
        pos = make_position(entry_price=0.40, current_price=0.42, size_usdc=100)
        market = make_market(
            yes_price=0.42,
            end_date=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        signals = mgr.check_exits([pos], [market])
        assert len(signals) == 1
        assert "RESOLUTION" in signals[0].reason

    def test_near_certain_exit(self):
        # Disable take-profit so near-certain can trigger
        mgr = ExitManager(ExitRule(take_profit_pct=9.99))
        pos = make_position(entry_price=0.40, current_price=0.96, side=Side.YES)
        market = make_market(yes_price=0.96)
        signals = mgr.check_exits([pos], [market])
        assert len(signals) == 1
        assert "NEAR CERTAIN" in signals[0].reason

    def test_trailing_stop(self):
        mgr = ExitManager(ExitRule(
            trailing_stop_pct=0.10,
            take_profit_pct=9.99,  # effectively disabled
            stop_loss_pct=-9.99,  # effectively disabled
        ))
        pos = make_position(entry_price=0.40, current_price=0.55, size_usdc=100)

        # First call: sets peak at 0.55
        market = make_market(yes_price=0.55, end_date=datetime.now(timezone.utc) + timedelta(days=30))
        signals = mgr.check_exits([pos], [market])
        assert len(signals) == 0

        # Price drops to 0.48 = 12.7% drawdown from peak 0.55 → triggers
        pos.current_price = 0.48
        market2 = make_market(yes_price=0.48, end_date=datetime.now(timezone.utc) + timedelta(days=30))
        signals = mgr.check_exits([pos], [market2])
        assert len(signals) == 1
        assert "TRAILING STOP" in signals[0].reason
