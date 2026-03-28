"""Tests for the Shannon entropy, KL divergence, and Kelly criterion."""

import math
import pytest
from backend.quant.entropy import (
    market_entropy,
    kl_divergence,
    kelly_fraction,
    quarter_kelly,
    entropy_efficiency,
    score_market,
    Action,
)


class TestMarketEntropy:
    def test_max_entropy_at_50_50(self):
        """H(0.5) = 1.0 bit (maximum uncertainty)."""
        assert market_entropy(0.5) == pytest.approx(1.0, abs=1e-6)

    def test_zero_entropy_at_extremes(self):
        """Near-certain markets have near-zero entropy."""
        assert market_entropy(0.001) < 0.02
        assert market_entropy(0.999) < 0.02

    def test_symmetry(self):
        """H(p) = H(1-p)."""
        assert market_entropy(0.3) == pytest.approx(market_entropy(0.7), abs=1e-10)

    def test_known_value(self):
        """H(0.35) ≈ 0.9341 bits (student example)."""
        assert market_entropy(0.35) == pytest.approx(0.9341, abs=0.001)

    def test_monotonic_decrease_from_half(self):
        """Entropy decreases monotonically as p moves away from 0.5."""
        h50 = market_entropy(0.50)
        h40 = market_entropy(0.40)
        h30 = market_entropy(0.30)
        h20 = market_entropy(0.20)
        h10 = market_entropy(0.10)
        assert h50 > h40 > h30 > h20 > h10


class TestKLDivergence:
    def test_zero_when_equal(self):
        """KL(p||p) = 0."""
        assert kl_divergence(0.5, 0.5) == pytest.approx(0.0, abs=1e-10)
        assert kl_divergence(0.3, 0.3) == pytest.approx(0.0, abs=1e-10)

    def test_always_non_negative(self):
        """KL divergence is always >= 0."""
        for p in [0.1, 0.3, 0.5, 0.7, 0.9]:
            for m in [0.1, 0.3, 0.5, 0.7, 0.9]:
                assert kl_divergence(p, m) >= -1e-12

    def test_student_example(self):
        """KL(0.58 || 0.35) ≈ 0.158 bits."""
        kl = kl_divergence(0.58, 0.35)
        assert kl == pytest.approx(0.158, abs=0.001)

    def test_asymmetric(self):
        """KL(p||q) != KL(q||p) in general (for non-symmetric pairs)."""
        kl_pq = kl_divergence(0.8, 0.3)
        kl_qp = kl_divergence(0.3, 0.8)
        assert kl_pq != pytest.approx(kl_qp, abs=0.01)

    def test_large_divergence(self):
        """Large model-market disagreement → large KL."""
        kl = kl_divergence(0.9, 0.1)
        assert kl > 1.0


class TestKellyFraction:
    def test_no_edge(self):
        """If model agrees with market, Kelly ≈ 0."""
        f = kelly_fraction(0.5, 0.5)
        assert abs(f) < 0.01

    def test_student_example(self):
        """f*(0.58, 0.35) ≈ 0.354."""
        f = kelly_fraction(0.58, 0.35)
        assert f == pytest.approx(0.354, abs=0.001)

    def test_negative_for_overpriced(self):
        """If model < market, Kelly is negative (bet NO)."""
        f = kelly_fraction(0.3, 0.6)
        assert f < 0

    def test_quarter_kelly(self):
        """Quarter-Kelly = f* × 0.25."""
        fq = quarter_kelly(0.58, 0.35)
        f_full = kelly_fraction(0.58, 0.35)
        assert fq == pytest.approx(f_full * 0.25, abs=1e-10)

    def test_bounded(self):
        """Kelly fraction stays reasonable for moderate inputs."""
        for p in [0.2, 0.4, 0.5, 0.6, 0.8]:
            for m in [0.2, 0.4, 0.5, 0.6, 0.8]:
                f = kelly_fraction(p, m)
                assert -10.0 < f < 10.0


class TestEntropyEfficiency:
    def test_equal_prices(self):
        """R = 1.0 when current = base rate."""
        r = entropy_efficiency(0.5, 0.5)
        assert r == pytest.approx(1.0, abs=1e-6)

    def test_resolved_market(self):
        """R < 1 when market moves toward certainty from base rate."""
        r = entropy_efficiency(0.1, 0.5)
        assert r < 1.0

    def test_max_uncertainty(self):
        """Moving toward 0.5 from extreme → R > 1."""
        r = entropy_efficiency(0.5, 0.1)
        assert r > 1.0


class TestScoreMarket:
    def test_hold_when_no_edge(self):
        """Markets with no model disagreement should HOLD."""
        scored = score_market("m1", "Test?", 0.50, 0.50)
        assert scored.recommended_action == Action.HOLD

    def test_buy_yes_when_underpriced(self):
        """Model > market → BUY_YES."""
        scored = score_market(
            "m1", "Test?", 0.20, 0.50,
            entropy_threshold=0.01, efficiency_max=0.99,
        )
        assert scored.recommended_action == Action.BUY_YES
        assert scored.position_size_usdc > 0

    def test_buy_no_when_overpriced(self):
        """Model < market → BUY_NO."""
        scored = score_market(
            "m1", "Test?", 0.80, 0.50,
            entropy_threshold=0.01, efficiency_max=0.99,
        )
        assert scored.recommended_action == Action.BUY_NO
        assert scored.position_size_usdc > 0

    def test_size_capped_by_max(self):
        """Position size should not exceed max_trade_usdc."""
        scored = score_market(
            "m1", "Test?", 0.10, 0.80,
            bankroll=1_000_000, max_trade_usdc=150,
            entropy_threshold=0.01, efficiency_max=0.99,
        )
        assert scored.position_size_usdc <= 150

    def test_edge_strength_strong(self):
        """Large KL → strong edge."""
        scored = score_market(
            "m1", "Test?", 0.10, 0.60,
            entropy_threshold=0.01, efficiency_max=0.99,
        )
        assert scored.edge_strength == "strong"
        assert scored.kl_div_bits > 0.15
