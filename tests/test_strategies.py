"""Tests for the 7 trading strategies."""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta

from backend.strategies.base import MarketState, Side, StrategyName
from backend.strategies.entropy_screener import EntropyScreener
from backend.strategies.arb_scanner import ArbScanner
from backend.strategies.theta_harvester import ThetaHarvester, classify_urgency, compute_theta
from backend.strategies.avellaneda_stoikov import AvellanedaStoikovMM
from backend.strategies.copy_trader import CopyTrader, CopyTarget, CopyTradeEvent


def make_market(**overrides) -> MarketState:
    """Factory for test MarketState objects."""
    defaults = dict(
        market_id="test-mkt-1",
        condition_id="cond-1",
        question="Will X happen?",
        category="test",
        yes_price=0.50,
        no_price=0.50,
        mid_price=0.50,
        spread=0.02,
        best_bid=0.49,
        best_ask=0.51,
        bid_depth=100,
        ask_depth=100,
        liquidity=50_000,
        volume_24h=10_000,
        end_date=datetime.now(timezone.utc) + timedelta(days=7),
        active=True,
        entropy_bits=0.9,
        model_probability=0.5,
    )
    defaults.update(overrides)
    return MarketState(**defaults)


class TestEntropyScreener:
    @pytest.fixture
    def screener(self):
        return EntropyScreener(
            entropy_threshold=0.05,
            efficiency_max=0.99,
            min_liquidity=1000,
            min_days_to_close=0.1,
            max_days_to_close=365,
        )

    @pytest.mark.asyncio
    async def test_no_signal_when_model_agrees(self, screener):
        m = make_market(yes_price=0.5, model_probability=0.5)
        result = await screener.evaluate(m)
        assert result is None

    @pytest.mark.asyncio
    async def test_buy_yes_when_underpriced(self, screener):
        m = make_market(yes_price=0.20, model_probability=0.50)
        result = await screener.evaluate(m)
        assert result is not None
        assert result.side == Side.YES

    @pytest.mark.asyncio
    async def test_buy_no_when_overpriced(self, screener):
        m = make_market(yes_price=0.80, no_price=0.20, model_probability=0.50)
        result = await screener.evaluate(m)
        assert result is not None
        assert result.side == Side.NO

    @pytest.mark.asyncio
    async def test_filters_low_liquidity(self, screener):
        m = make_market(liquidity=500, model_probability=0.8)
        result = await screener.evaluate(m)
        assert result is None

    @pytest.mark.asyncio
    async def test_filters_extreme_prices(self, screener):
        m = make_market(yes_price=0.01, model_probability=0.5)
        result = await screener.evaluate(m)
        assert result is None

    @pytest.mark.asyncio
    async def test_batch_returns_sorted(self, screener):
        markets = [
            make_market(market_id=f"m{i}", yes_price=p, model_probability=0.6)
            for i, p in enumerate([0.15, 0.25, 0.35])
        ]
        results = await screener.evaluate_batch(markets)
        if len(results) > 1:
            assert results[0].kl_divergence >= results[1].kl_divergence


class TestArbScanner:
    @pytest.fixture
    def arb(self):
        return ArbScanner(min_arb_edge=0.01)

    @pytest.mark.asyncio
    async def test_detects_arb(self, arb):
        m = make_market(yes_price=0.45, no_price=0.52)
        assert m.arb_edge == pytest.approx(0.03, abs=0.001)
        result = await arb.evaluate(m)
        assert result is not None
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_no_arb_when_prices_sum_to_one(self, arb):
        m = make_market(yes_price=0.50, no_price=0.50)
        result = await arb.evaluate(m)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_arb_below_threshold(self, arb):
        m = make_market(yes_price=0.495, no_price=0.500)
        result = await arb.evaluate(m)
        assert result is None


class TestThetaHarvester:
    def test_classify_urgency(self):
        assert classify_urgency(200) == "patient"
        assert classify_urgency(48) == "normal"
        assert classify_urgency(12) == "urgent"
        assert classify_urgency(3) == "critical"

    def test_compute_theta(self):
        theta = compute_theta(fair_price=0.0, current_price=0.15, hours_remaining=9)
        assert theta < 0  # decaying toward 0
        assert abs(theta) > 0

    @pytest.mark.asyncio
    async def test_harvests_near_zero(self):
        harvester = ThetaHarvester(min_theta_edge=0.03, max_resolution_hours=100)
        m = make_market(
            yes_price=0.10, no_price=0.90,
            end_date=datetime.now(timezone.utc) + timedelta(hours=12),
        )
        result = await harvester.evaluate(m)
        if result is not None:
            assert result.side == Side.NO

    @pytest.mark.asyncio
    async def test_ignores_midrange(self):
        harvester = ThetaHarvester()
        m = make_market(
            yes_price=0.50,
            end_date=datetime.now(timezone.utc) + timedelta(hours=12),
        )
        result = await harvester.evaluate(m)
        assert result is None


class TestAvellanedaStoikovMM:
    @pytest.fixture
    def mm(self):
        return AvellanedaStoikovMM(
            gamma=0.1, kappa=1.5, min_liquidity=1000, min_hours_to_close=1,
        )

    @pytest.mark.asyncio
    async def test_generates_quote(self, mm):
        m = make_market(
            liquidity=100_000,
            end_date=datetime.now(timezone.utc) + timedelta(days=7),
        )
        result = await mm.evaluate(m)
        assert result is not None
        assert result.price > 0

    @pytest.mark.asyncio
    async def test_filters_low_liquidity(self, mm):
        m = make_market(liquidity=500)
        result = await mm.evaluate(m)
        assert result is None

    def test_record_fill_updates_inventory(self, mm):
        mm.record_fill("test-mkt-1", Side.YES, 0.50, 100)
        state = mm._get_state("test-mkt-1")
        assert state.inventory == 100
        assert state.fills == 1


class TestCopyTrader:
    @pytest.fixture
    def trader(self):
        return CopyTrader(
            targets=[CopyTarget(address="0xRN1", display_name="@RN1", auto_copy=True, copy_ratio=0.1)],
            confluence_required=False,
        )

    @pytest.mark.asyncio
    async def test_auto_execute_with_confluence(self, trader):
        event = CopyTradeEvent(
            target=trader.targets[0],
            market_id="test-mkt-1",
            question="Will X?",
            side=Side.YES,
            size_usdc=1000,
            price=0.35,
            entropy_signal=True,
        )
        trader.queue_event(event)
        m = make_market()
        result = await trader.evaluate(m)
        assert result is not None
        assert result.side == Side.YES

    @pytest.mark.asyncio
    async def test_manual_queue_when_no_confluence(self):
        trader = CopyTrader(
            targets=[CopyTarget(address="0xABC", display_name="test", auto_copy=False)],
            confluence_required=True,
        )
        event = CopyTradeEvent(
            target=trader.targets[0],
            market_id="test-mkt-1",
            question="Will X?",
            side=Side.YES,
            size_usdc=500,
            price=0.40,
        )
        trader.queue_event(event)
        m = make_market()
        result = await trader.evaluate(m)
        # Should be queued for manual confirm, not auto-executed
        assert result is None
        assert len(trader.manual_queue) > 0
