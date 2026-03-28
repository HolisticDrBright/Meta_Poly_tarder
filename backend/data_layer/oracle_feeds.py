"""
Oracle price feeds — Binance spot prices for crypto prediction markets.

Used to detect when Polymarket's implied probability is lagging the
underlying asset move (the polyrec/txbabaxyz edge for 15-min markets).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class PriceFeed:
    symbol: str
    price: float
    timestamp: float


class OracleFeedClient:
    """Fetch real-time crypto prices from Binance."""

    BINANCE_API = "https://api.binance.com/api/v3"

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        from backend.data_layer.rate_limiter import BINANCE_LIMITER
        self._limiter = BINANCE_LIMITER

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_price(self, symbol: str = "BTCUSDT") -> Optional[PriceFeed]:
        """Fetch current spot price."""
        await self._limiter.acquire()
        session = await self._get_session()
        try:
            async with session.get(
                f"{self.BINANCE_API}/ticker/price",
                params={"symbol": symbol},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                return PriceFeed(
                    symbol=data["symbol"],
                    price=float(data["price"]),
                    timestamp=0,
                )
        except Exception as e:
            logger.error(f"Binance price fetch failed for {symbol}: {e}")
            return None

    async def get_prices(self, symbols: list[str]) -> dict[str, PriceFeed]:
        """Fetch multiple spot prices."""
        await self._limiter.acquire()
        results = {}
        session = await self._get_session()
        try:
            async with session.get(
                f"{self.BINANCE_API}/ticker/price",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                price_map = {d["symbol"]: float(d["price"]) for d in data}
                for sym in symbols:
                    if sym in price_map:
                        results[sym] = PriceFeed(
                            symbol=sym, price=price_map[sym], timestamp=0
                        )
        except Exception as e:
            logger.error(f"Binance bulk price fetch failed: {e}")
        return results

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
