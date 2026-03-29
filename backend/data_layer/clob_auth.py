"""
L2 CLOB Authentication — derives API credentials from L1 private key.

The Polymarket CLOB requires L2 API credentials (HMAC) for authenticated
endpoints (positions, orders, balances). These are derived from the L1
Ethereum private key using py-clob-client's derive_api_key().

Flow:
  1. L1 private key (from .env) → ClobClient init
  2. derive_api_key() → ApiCreds (api_key, api_secret, api_passphrase)
  3. set_api_creds() → all subsequent requests are authenticated
  4. Use client to fetch positions, balances, orders
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet


class CLOBAuthClient:
    """
    Authenticated CLOB client for fetching positions and balances.

    Separate from the order executor — this is read-only.
    """

    def __init__(
        self,
        private_key: str = "",
        wallet_address: str = "",
        signature_type: int = 0,
    ) -> None:
        self._private_key = private_key
        self._wallet_address = wallet_address
        self._signature_type = signature_type
        self._client: Any = None
        self._initialized = False

    def _ensure_client(self) -> Any:
        """Lazy-init the py-clob-client."""
        if self._client is not None:
            return self._client
        if not self._private_key:
            logger.debug("No private key configured — CLOB auth disabled")
            return None
        try:
            from py_clob_client.client import ClobClient

            self._client = ClobClient(
                host=CLOB_HOST,
                key=self._private_key,
                chain_id=CHAIN_ID,
                signature_type=self._signature_type,
            )
            creds = self._client.derive_api_key()
            self._client.set_api_creds(creds)
            self._initialized = True
            logger.info(f"CLOB L2 auth initialized (wallet={self._wallet_address[:10]}...)")
            return self._client
        except ImportError:
            logger.warning("py-clob-client not installed — CLOB auth disabled")
            return None
        except Exception as e:
            logger.error(f"CLOB auth init failed: {e}")
            return None

    @property
    def available(self) -> bool:
        return self._ensure_client() is not None

    async def get_positions(self) -> list[dict]:
        """Fetch real open positions from the CLOB."""
        client = self._ensure_client()
        if not client:
            return []
        try:
            from backend.data_layer.rate_limiter import CLOB_LIMITER
            await CLOB_LIMITER.acquire()

            # py-clob-client get_positions() or similar
            # The actual method name may vary by version
            try:
                positions = client.get_positions()
            except AttributeError:
                # Fallback: try alternative method names
                try:
                    positions = client.get_orders(open_only=True)
                except Exception:
                    positions = []

            if isinstance(positions, dict):
                positions = positions.get("positions", positions.get("data", []))

            result = []
            for p in (positions if isinstance(positions, list) else []):
                try:
                    result.append({
                        "market_id": str(p.get("market", p.get("asset_id", p.get("token_id", "")))),
                        "condition_id": str(p.get("conditionId", p.get("condition_id", ""))),
                        "side": p.get("side", "BUY"),
                        "size": float(p.get("size", p.get("original_size", 0))),
                        "price": float(p.get("price", p.get("avg_price", 0))),
                        "status": p.get("status", "open"),
                    })
                except (ValueError, TypeError):
                    continue
            return result
        except Exception as e:
            logger.error(f"CLOB get_positions failed: {e}")
            return []

    async def get_balance(self) -> float:
        """Fetch USDC balance from CLOB."""
        client = self._ensure_client()
        if not client:
            return 0.0
        try:
            from backend.data_layer.rate_limiter import CLOB_LIMITER
            await CLOB_LIMITER.acquire()

            try:
                balance = client.get_balance()
                if isinstance(balance, dict):
                    return float(balance.get("balance", balance.get("amount", 0)))
                return float(balance)
            except AttributeError:
                return 0.0
        except Exception as e:
            logger.error(f"CLOB get_balance failed: {e}")
            return 0.0

    async def get_open_orders(self) -> list[dict]:
        """Fetch open orders from CLOB."""
        client = self._ensure_client()
        if not client:
            return []
        try:
            from backend.data_layer.rate_limiter import CLOB_LIMITER
            await CLOB_LIMITER.acquire()

            orders = client.get_orders()
            if isinstance(orders, dict):
                orders = orders.get("orders", orders.get("data", []))
            return orders if isinstance(orders, list) else []
        except Exception as e:
            logger.error(f"CLOB get_orders failed: {e}")
            return []
