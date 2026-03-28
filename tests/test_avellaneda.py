"""Tests for the Avellaneda-Stoikov market maker math."""

import math
import pytest
from backend.quant.avellaneda_math import (
    reservation_price,
    optimal_spread,
    compute_quotes,
    vpin,
    order_flow_imbalance,
    TradeBucket,
    ASQuotes,
)


class TestReservationPrice:
    def test_no_inventory(self):
        """With zero inventory, reservation price = mid."""
        r = reservation_price(mid=0.5, inventory=0, gamma=0.1, volatility=0.02, t_remaining=3600)
        assert r == pytest.approx(0.5, abs=1e-10)

    def test_long_inventory_lowers_price(self):
        """Long inventory → reservation price below mid (want to sell)."""
        r = reservation_price(mid=0.5, inventory=5, gamma=0.1, volatility=0.02, t_remaining=3600)
        assert r < 0.5

    def test_short_inventory_raises_price(self):
        """Short inventory → reservation price above mid (want to buy)."""
        r = reservation_price(mid=0.5, inventory=-5, gamma=0.1, volatility=0.02, t_remaining=3600)
        assert r > 0.5

    def test_higher_gamma_larger_adjustment(self):
        """Higher risk aversion → larger price adjustment for same inventory."""
        r_low = reservation_price(0.5, 3, gamma=0.05, volatility=0.02, t_remaining=3600)
        r_high = reservation_price(0.5, 3, gamma=0.20, volatility=0.02, t_remaining=3600)
        # Higher gamma pushes price further from mid
        assert abs(0.5 - r_high) > abs(0.5 - r_low)


class TestOptimalSpread:
    def test_positive(self):
        """Spread should always be positive."""
        s = optimal_spread(gamma=0.1, volatility=0.02, t_remaining=3600, kappa=1.5)
        assert s > 0

    def test_higher_volatility_wider_spread(self):
        """Higher volatility → wider spread."""
        s_low = optimal_spread(gamma=0.1, volatility=0.01, t_remaining=3600, kappa=1.5)
        s_high = optimal_spread(gamma=0.1, volatility=0.05, t_remaining=3600, kappa=1.5)
        assert s_high > s_low

    def test_less_time_narrower_spread(self):
        """Less time remaining → narrower volatility component."""
        s_full = optimal_spread(gamma=0.1, volatility=0.02, t_remaining=86400, kappa=1.5)
        s_half = optimal_spread(gamma=0.1, volatility=0.02, t_remaining=3600, kappa=1.5)
        assert s_full > s_half


class TestComputeQuotes:
    def test_bid_below_ask(self):
        """Bid should always be below ask."""
        q = compute_quotes(mid=0.5, inventory=0, gamma=0.1, volatility=0.02, t_remaining=3600, kappa=1.5)
        assert q.bid < q.ask

    def test_spread_bps_positive(self):
        q = compute_quotes(mid=0.5, inventory=0, gamma=0.1, volatility=0.02, t_remaining=3600, kappa=1.5)
        assert q.spread_bps > 0

    def test_returns_asquotes(self):
        q = compute_quotes(mid=0.5, inventory=0, gamma=0.1, volatility=0.02, t_remaining=3600, kappa=1.5)
        assert isinstance(q, ASQuotes)


class TestVPIN:
    def test_balanced_flow(self):
        """Equal buy/sell → VPIN = 0."""
        buckets = [TradeBucket(100, 100) for _ in range(10)]
        assert vpin(buckets) == pytest.approx(0.0, abs=1e-10)

    def test_all_buy_flow(self):
        """All buys, no sells → VPIN = 1.0."""
        buckets = [TradeBucket(100, 0) for _ in range(10)]
        assert vpin(buckets) == pytest.approx(1.0, abs=1e-10)

    def test_partial_imbalance(self):
        """Partial imbalance → 0 < VPIN < 1."""
        buckets = [TradeBucket(100, 50), TradeBucket(80, 90), TradeBucket(120, 60)]
        v = vpin(buckets)
        assert 0 < v < 1

    def test_empty_buckets(self):
        assert vpin([]) == 0.0

    def test_n_buckets_limit(self):
        """Only use last N buckets."""
        buckets = [TradeBucket(100, 100)] * 5 + [TradeBucket(100, 0)] * 5
        v_all = vpin(buckets)
        v_last5 = vpin(buckets, n_buckets=5)
        assert v_last5 > v_all  # last 5 are all-buy


class TestOrderFlowImbalance:
    def test_balanced(self):
        assert order_flow_imbalance(100, 100) == pytest.approx(0.0)

    def test_all_bid(self):
        assert order_flow_imbalance(100, 0) == pytest.approx(1.0)

    def test_all_ask(self):
        assert order_flow_imbalance(0, 100) == pytest.approx(-1.0)

    def test_zero_both(self):
        assert order_flow_imbalance(0, 0) == 0.0

    def test_range(self):
        ofi = order_flow_imbalance(150, 80)
        assert -1.0 <= ofi <= 1.0
