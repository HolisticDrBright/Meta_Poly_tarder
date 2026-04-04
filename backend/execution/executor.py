"""
Order executor — handles the CLOB order lifecycle.

Paper trading: simulates fills at the intent price.
Live trading: places real orders via the Polymarket CLOB API
using py-clob-client for EIP-712 signing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from backend.strategies.base import OrderIntent, Position, ScoredIntent, Side

logger = logging.getLogger(__name__)

# CLOB API constants
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet


@dataclass
class ExecutionResult:
    success: bool
    order_id: str = ""
    fill_price: float = 0.0
    fill_size: float = 0.0
    paper: bool = True
    error: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CLOBLiveClient:
    """
    Live CLOB order client using py-clob-client for EIP-712 signing.

    Compatible with py-clob-client >= 0.5.0.
    The client handles:
      - API key derivation (HMAC signing)
      - Order creation with EIP-712 signatures
      - Order posting, cancellation, and listing

    Requires:
      pip install py-clob-client
      POLYMARKET_PRIVATE_KEY and POLYMARKET_WALLET_ADDRESS in .env
    """

    def __init__(self, private_key: str, wallet_address: str, signature_type: int = 0) -> None:
        self._client: Any = None
        self._private_key = private_key
        self._wallet_address = wallet_address
        self._signature_type = signature_type
        self._initialized = False
        from backend.data_layer.rate_limiter import CLOB_LIMITER
        self._limiter = CLOB_LIMITER

    def _ensure_client(self) -> Any:
        """Lazy-init the py-clob-client to avoid import errors if not installed."""
        if self._client is not None:
            return self._client
        try:
            from py_clob_client.client import ClobClient

            self._client = ClobClient(
                host=CLOB_HOST,
                key=self._private_key,
                chain_id=CHAIN_ID,
                signature_type=self._signature_type,
            )
            # Derive HMAC API credentials for authenticated endpoints.
            # derive_api_key() returns ApiCreds which set_api_creds() stores.
            creds = self._client.derive_api_key()
            self._client.set_api_creds(creds)
            self._initialized = True
            logger.info(
                f"CLOB live client initialized "
                f"(wallet={self._wallet_address[:10]}..., chain={CHAIN_ID})"
            )
            return self._client
        except ImportError:
            logger.error(
                "py-clob-client not installed. Run: pip install py-clob-client"
            )
            raise
        except Exception as e:
            logger.error(f"CLOB client init failed: {e}")
            raise

    async def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> dict:
        """
        Place a limit order on the CLOB.

        The py-clob-client create_order() expects:
          OrderArgs: token_id, price, size, side (BUY/SELL)

        BUY = buying the token (YES or NO)
        SELL = selling the token

        For prediction markets:
          Betting YES at 0.35 → BUY the YES token at 0.35
          Betting NO at 0.65 → BUY the NO token at 0.65
        """
        await self._limiter.acquire()
        client = self._ensure_client()
        try:
            from py_clob_client.order_builder.constants import BUY

            # Both YES and NO bets are BUY orders on different token IDs.
            # The token_id determines which outcome you're buying.
            order_args = {
                "token_id": token_id,
                "price": round(price, 4),  # CLOB requires max 4 decimal places
                "size": round(size, 2),
                "side": BUY,
            }

            order = client.create_order(order_args)
            result = client.post_order(order)
            logger.info(f"LIVE ORDER placed: {side} {size} @ {price} — {result}")

            # Normalize the response — py-clob-client returns varying formats
            if isinstance(result, dict):
                return result
            return {"orderID": str(result), "success": True}

        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return {"error": str(e)}

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order."""
        await self._limiter.acquire()
        client = self._ensure_client()
        try:
            result = client.cancel(order_id)
            logger.info(f"Order cancelled: {order_id}")
            return result if isinstance(result, dict) else {"success": True}
        except Exception as e:
            logger.error(f"Cancel failed: {e}")
            return {"error": str(e)}

    async def get_open_orders(self) -> list[dict]:
        """Get all open orders for this wallet."""
        client = self._ensure_client()
        try:
            return client.get_orders()
        except Exception as e:
            logger.error(f"Get orders failed: {e}")
            return []

    async def cancel_all(self) -> dict:
        """Emergency: cancel all open orders."""
        client = self._ensure_client()
        try:
            result = client.cancel_all()
            logger.warning(f"ALL ORDERS CANCELLED: {result}")
            return result
        except Exception as e:
            logger.error(f"Cancel all failed: {e}")
            return {"error": str(e)}


class OrderExecutor:
    """Executes orders against the Polymarket CLOB."""

    def __init__(
        self,
        paper_trading: bool = True,
        private_key: str = "",
        wallet_address: str = "",
        signature_type: int = 0,
    ) -> None:
        self.paper_trading = paper_trading
        self._paper_fills: list[ExecutionResult] = []
        self._live_client: Optional[CLOBLiveClient] = None
        # Stash credentials so we can lazily spin up the live client when
        # the user flips Paper → Live at runtime.
        self._private_key = private_key
        self._wallet_address = wallet_address
        self._signature_type = signature_type

        if not paper_trading and private_key:
            self._live_client = CLOBLiveClient(
                private_key=private_key,
                wallet_address=wallet_address,
                signature_type=signature_type,
            )

    def set_mode(self, mode: str) -> dict:
        """
        Flip between paper and live at runtime.

        Returns a small status dict so callers (the /api/v1/execution/mode
        endpoint) can surface the result to the dashboard.
        """
        mode = (mode or "").lower()
        if mode not in ("paper", "live"):
            return {"ok": False, "error": f"invalid mode: {mode}"}

        if mode == "paper":
            self.paper_trading = True
            return {"ok": True, "mode": "paper"}

        # Going live — require signing credentials.
        if not self._private_key:
            return {
                "ok": False,
                "error": "POLYMARKET_PRIVATE_KEY not configured — cannot go live",
            }

        if self._live_client is None:
            self._live_client = CLOBLiveClient(
                private_key=self._private_key,
                wallet_address=self._wallet_address,
                signature_type=self._signature_type,
            )
        self.paper_trading = False
        return {"ok": True, "mode": "live"}

    async def execute(self, scored: ScoredIntent) -> ExecutionResult:
        """Execute a single scored intent."""
        intent = scored.intent

        if not scored.approved:
            return ExecutionResult(
                success=False, error="Not approved by risk engine"
            )

        if self.paper_trading:
            return self._paper_fill(intent)
        else:
            return await self._live_fill(intent)

    def _paper_fill(self, intent: OrderIntent) -> ExecutionResult:
        """Simulate a fill for paper trading."""
        result = ExecutionResult(
            success=True,
            order_id=f"PAPER-{intent.market_id[:8]}-{datetime.now(timezone.utc).timestamp():.0f}",
            fill_price=intent.price,
            fill_size=intent.size_usdc,
            paper=True,
        )
        self._paper_fills.append(result)
        logger.info(
            f"PAPER FILL: {intent.strategy.value} {intent.side.value} "
            f"${intent.size_usdc:.2f} @ {intent.price:.4f} — {intent.question[:50]}"
        )
        return result

    async def _live_fill(self, intent: OrderIntent) -> ExecutionResult:
        """Place a real order on the Polymarket CLOB with signing."""
        if not self._live_client:
            logger.error("Live client not configured — check POLYMARKET_PRIVATE_KEY")
            return ExecutionResult(success=False, error="Live client not configured")

        try:
            result = await self._live_client.place_limit_order(
                token_id=intent.condition_id,
                side=intent.side.value,
                price=intent.price,
                size=intent.size_usdc,
            )

            if "error" in result:
                return ExecutionResult(success=False, error=result["error"], paper=False)

            order_id = result.get("orderID", result.get("id", "unknown"))
            logger.info(
                f"LIVE FILL: {intent.strategy.value} {intent.side.value} "
                f"${intent.size_usdc:.2f} @ {intent.price:.4f} — order_id={order_id}"
            )
            return ExecutionResult(
                success=True,
                order_id=str(order_id),
                fill_price=intent.price,
                fill_size=intent.size_usdc,
                paper=False,
            )
        except Exception as e:
            logger.error(f"Live execution failed: {e}")
            return ExecutionResult(success=False, error=str(e), paper=False)

    async def execute_batch(
        self, scored_intents: list[ScoredIntent]
    ) -> list[ExecutionResult]:
        results = []
        for si in scored_intents:
            result = await self.execute(si)
            results.append(result)
        return results

    async def cancel_all_live(self) -> None:
        """Emergency: cancel all live orders."""
        if self._live_client:
            await self._live_client.cancel_all()

    def to_position(self, intent: OrderIntent, result: ExecutionResult) -> Optional[Position]:
        """Convert a fill to a tracked position."""
        if not result.success:
            return None
        return Position(
            market_id=intent.market_id,
            condition_id=intent.condition_id,
            question=intent.question,
            side=intent.side,
            entry_price=result.fill_price,
            size_usdc=result.fill_size,
            current_price=result.fill_price,
            strategy=intent.strategy,
        )

    @property
    def paper_fill_count(self) -> int:
        return len(self._paper_fills)
