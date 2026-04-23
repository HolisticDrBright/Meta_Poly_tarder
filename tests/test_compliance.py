"""
Compliance posture tests.

Verifies:
1. VPNGuard starts healthy with VPN_REQUIRED=False (no network needed)
2. Executor returns LIVE_DISABLED when POLYMARKET_LIVE is unset
3. Paper order path works regardless of POLYMARKET_LIVE state
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from backend.execution.executor import ExecutionResult, OrderExecutor
from backend.observability.vpn_guard import VPNGuard
from backend.strategies.base import OrderIntent, OrderType, ScoredIntent, Side, StrategyName


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_scored_intent(approved: bool = True) -> ScoredIntent:
    intent = OrderIntent(
        strategy=StrategyName.ENTROPY,
        market_id="test-market-001",
        condition_id="0xdeadbeef",
        question="Will BTC hit $100k?",
        side=Side.YES,
        order_type=OrderType.LIMIT,
        price=0.45,
        size_usdc=10.0,
        confidence=0.7,
        reason="test",
    )
    return ScoredIntent(intent=intent, composite_score=0.7, approved=approved)


# ── Test 1: VPNGuard is a no-op when VPN_REQUIRED=False ──────────────────────

@pytest.mark.asyncio
async def test_vpn_guard_noop_when_not_required():
    """App starts healthy with VPN_REQUIRED=False — no proxy, no network check."""
    guard = VPNGuard(proxy_url="", required=False)

    # healthy property returns True immediately
    assert guard.healthy is True

    # startup_gate returns True without touching the network
    result = await guard.startup_gate()
    assert result is True


@pytest.mark.asyncio
async def test_vpn_guard_check_healthy_when_not_required():
    """check() returns healthy=True when VPN_REQUIRED=False, even with no proxy."""
    guard = VPNGuard(proxy_url="", required=False)
    status = await guard.check()
    assert status.healthy is True


# ── Test 2: Executor returns LIVE_DISABLED when POLYMARKET_LIVE is unset ─────

@pytest.mark.asyncio
async def test_executor_live_disabled_when_flag_unset():
    """_live_fill returns LIVE_DISABLED structured error when POLYMARKET_LIVE is not set."""
    with patch.dict(os.environ, {}, clear=False):
        # Ensure POLYMARKET_LIVE is absent
        os.environ.pop("POLYMARKET_LIVE", None)

        executor = OrderExecutor(paper_trading=False)
        scored = _make_scored_intent(approved=True)
        result = await executor.execute(scored)

    assert result.success is False
    assert result.code == "LIVE_DISABLED"
    assert "POLYMARKET_LIVE=true" in result.error


@pytest.mark.asyncio
async def test_executor_live_disabled_when_flag_false():
    """LIVE_DISABLED returned when POLYMARKET_LIVE=false explicitly."""
    with patch.dict(os.environ, {"POLYMARKET_LIVE": "false"}):
        executor = OrderExecutor(paper_trading=False)
        scored = _make_scored_intent(approved=True)
        result = await executor.execute(scored)

    assert result.success is False
    assert result.code == "LIVE_DISABLED"


@pytest.mark.asyncio
async def test_set_mode_live_blocked_without_flag():
    """set_mode('live') returns LIVE_DISABLED dict when POLYMARKET_LIVE is unset."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("POLYMARKET_LIVE", None)
        executor = OrderExecutor(paper_trading=True, private_key="0xdeadbeef")
        result = executor.set_mode("live")

    assert result["ok"] is False
    assert result["code"] == "LIVE_DISABLED"


# ── Test 3: Paper order path unaffected by POLYMARKET_LIVE ───────────────────

@pytest.mark.asyncio
async def test_paper_fill_works_without_live_flag():
    """Paper fills succeed regardless of POLYMARKET_LIVE."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("POLYMARKET_LIVE", None)

        executor = OrderExecutor(paper_trading=True)
        scored = _make_scored_intent(approved=True)
        result = await executor.execute(scored, market_price=0.45)

    assert result.success is True
    assert result.paper is True
    assert result.fill_price == pytest.approx(0.45)


@pytest.mark.asyncio
async def test_paper_fill_works_with_live_flag_true():
    """Paper fills also work when POLYMARKET_LIVE=true — the flag only gates live path."""
    with patch.dict(os.environ, {"POLYMARKET_LIVE": "true"}):
        executor = OrderExecutor(paper_trading=True)
        scored = _make_scored_intent(approved=True)
        result = await executor.execute(scored, market_price=0.60)

    assert result.success is True
    assert result.paper is True


@pytest.mark.asyncio
async def test_unapproved_intent_rejected_regardless():
    """Risk-rejected intents never reach the live gate."""
    with patch.dict(os.environ, {"POLYMARKET_LIVE": "true"}):
        executor = OrderExecutor(paper_trading=False)
        scored = _make_scored_intent(approved=False)
        result = await executor.execute(scored)

    assert result.success is False
    assert result.code != "LIVE_DISABLED"  # rejected before the gate
