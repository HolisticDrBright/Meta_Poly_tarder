"""
Execution Engine — routes trades to paper or live mode.

Paper mode: simulates fills at market price.
Live mode: places real orders via py-clob-client on Polymarket CLOB.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from execution.models import TradeRequest, TradeResult, TradeStatus

logger = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


class ExecutionEngine:
    """Executes trades in paper or live mode."""

    def __init__(self, mode: str | None = None) -> None:
        self.mode = mode or os.getenv("EXECUTION_MODE", "paper").lower()
        self.client: Any = None
        self._initialized = False

        if self.mode == "live":
            try:
                self._init_clob_client()
            except Exception as e:
                logger.error(f"CLOB init failed — falling back to paper: {e}")
                self.mode = "paper"

    def _init_clob_client(self) -> None:
        """Initialize the Polymarket CLOB client for live trading."""
        pk = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
        sig_type = int(os.getenv("SIGNATURE_TYPE", "1"))

        if not pk:
            raise ValueError("POLYMARKET_PRIVATE_KEY not set")

        from py_clob_client.client import ClobClient

        self.client = ClobClient(
            CLOB_HOST,
            key=pk,
            chain_id=CHAIN_ID,
            signature_type=sig_type,
            funder=funder or None,
        )
        self.client.set_api_creds(self.client.create_or_derive_api_creds())

        # Verify connection
        try:
            ok = self.client.get_ok()
            if not ok:
                raise ConnectionError("CLOB health check failed")
        except Exception as e:
            raise ConnectionError(f"Cannot reach CLOB API: {e}")

        self._initialized = True
        logger.info(f"CLOB client initialized (sig_type={sig_type}, funder={funder[:10]}...)")

    async def execute_trade(self, trade: TradeRequest) -> TradeResult:
        """Main entry point. Routes to paper or live."""
        if self.mode == "paper":
            return await self._execute_paper(trade)
        elif self.mode == "live":
            return await self._execute_live(trade)
        return TradeResult(status=TradeStatus.ERROR.value, error_message=f"Unknown mode: {self.mode}")

    async def _execute_paper(self, trade: TradeRequest) -> TradeResult:
        """Simulate a fill at market price with minor slippage."""
        import random
        slippage = random.uniform(0, trade.max_slippage_pct / 100)
        fill_price = (trade.price or 0.5) * (1 + slippage)
        fill_size = trade.size or (trade.amount_usd / fill_price if trade.amount_usd and fill_price > 0 else 0)

        return TradeResult(
            trade_id=str(uuid.uuid4()),
            order_id=f"PAPER-{uuid.uuid4().hex[:8]}",
            decision_id=trade.decision_id,
            mode="paper",
            market_id=trade.market_id,
            market_title=trade.market_title,
            token_id=trade.token_id,
            direction=trade.direction,
            order_type=trade.order_type,
            requested_price=trade.price,
            fill_price=round(fill_price, 4),
            requested_size=trade.size or 0,
            filled_size=round(fill_size, 2),
            fill_percentage=100.0,
            amount_usd=round(fill_size * fill_price, 2),
            fees_usd=0.0,
            slippage_bps=round(slippage * 10000, 1),
            status=TradeStatus.FILLED.value,
            timestamp=datetime.now(timezone.utc),
        )

    async def _execute_live(self, trade: TradeRequest) -> TradeResult:
        """Place a real order on Polymarket CLOB."""
        if not self.client:
            return TradeResult(status=TradeStatus.ERROR.value, error_message="CLOB client not initialized")

        try:
            from py_clob_client.order_builder.constants import BUY, SELL

            side = BUY if trade.direction == "YES" else SELL

            if trade.order_type == "market":
                resp = await self._execute_market_order(trade, side)
            else:
                resp = await self._execute_limit_order(trade, side)

            if isinstance(resp, dict) and resp.get("error"):
                return TradeResult(
                    decision_id=trade.decision_id,
                    mode="live", market_id=trade.market_id, market_title=trade.market_title,
                    token_id=trade.token_id, direction=trade.direction,
                    status=TradeStatus.REJECTED.value,
                    error_message=str(resp["error"]),
                )

            order_id = resp.get("orderID", resp.get("id", str(resp))) if isinstance(resp, dict) else str(resp)
            fill_price = trade.price or 0.5
            fill_size = trade.size or (trade.amount_usd / fill_price if trade.amount_usd else 0)

            logger.info(f"LIVE ORDER: {trade.direction} {fill_size:.2f} @ {fill_price:.4f} — {trade.market_title[:40]}")

            return TradeResult(
                trade_id=str(uuid.uuid4()),
                order_id=order_id,
                decision_id=trade.decision_id,
                mode="live",
                market_id=trade.market_id,
                market_title=trade.market_title,
                token_id=trade.token_id,
                direction=trade.direction,
                order_type=trade.order_type,
                requested_price=trade.price,
                fill_price=round(fill_price, 4),
                requested_size=trade.size or 0,
                filled_size=round(fill_size, 2),
                fill_percentage=100.0,
                amount_usd=round(fill_size * fill_price, 2),
                fees_usd=round(fill_size * fill_price * 0.003, 4),  # ~30bps
                slippage_bps=0,
                status=TradeStatus.FILLED.value,
                timestamp=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.error(f"Live execution failed: {e}")
            # Retry once after 2 seconds
            try:
                await asyncio.sleep(2)
                return await self._execute_live(trade)
            except Exception as e2:
                return TradeResult(
                    decision_id=trade.decision_id, mode="live",
                    market_id=trade.market_id, market_title=trade.market_title,
                    status=TradeStatus.ERROR.value, error_message=str(e2),
                )

    async def _execute_limit_order(self, trade: TradeRequest, side) -> dict:
        """Place a limit order."""
        order_args = {
            "price": round(trade.price, 4) if trade.price else 0.5,
            "size": round(trade.size, 2) if trade.size else 1.0,
            "side": side,
            "token_id": trade.token_id,
        }
        signed = self.client.create_order(order_args)
        return self.client.post_order(signed)

    async def _execute_market_order(self, trade: TradeRequest, side) -> dict:
        """Place a market (FOK) order."""
        from py_clob_client.clob_types import MarketOrderArgs, OrderType as ClobOrderType
        mo = MarketOrderArgs(
            token_id=trade.token_id,
            amount=trade.amount_usd or 10,
            side=side,
        )
        signed = self.client.create_market_order(mo)
        return self.client.post_order(signed, order_type=ClobOrderType.FOK)

    async def cancel_order(self, order_id: str) -> bool:
        if self.client:
            try:
                self.client.cancel(order_id)
                return True
            except Exception as e:
                logger.error(f"Cancel failed: {e}")
        return False

    async def cancel_all_orders(self) -> bool:
        if self.client:
            try:
                self.client.cancel_all()
                logger.warning("ALL ORDERS CANCELLED")
                return True
            except Exception as e:
                logger.error(f"Cancel all failed: {e}")
        return False

    async def get_balance(self) -> float:
        if self.client:
            try:
                bal = self.client.get_balance()
                return float(bal) if not isinstance(bal, dict) else float(bal.get("balance", 0))
            except Exception:
                return 0.0
        return 0.0

    async def get_open_orders(self) -> list:
        if self.client:
            try:
                return self.client.get_orders() or []
            except Exception:
                return []
        return []
