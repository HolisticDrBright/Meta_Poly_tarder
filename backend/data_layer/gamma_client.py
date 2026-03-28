"""
Polymarket Gamma API client — market discovery and metadata.

Gamma API is the REST API for browsing markets, getting metadata,
prices, and historical data. No authentication required.

Endpoints:
  GET https://gamma-api.polymarket.com/markets
  GET https://gamma-api.polymarket.com/markets/{id}
  GET https://gamma-api.polymarket.com/events
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"


@dataclass
class GammaMarket:
    """Parsed market from Gamma API response."""

    id: str
    condition_id: str
    question: str
    category: str
    end_date: Optional[datetime]
    active: bool
    closed: bool
    liquidity: float
    volume: float
    volume_24h: float
    yes_price: float
    no_price: float
    best_bid: float
    best_ask: float
    spread: float
    outcomes: list[str]
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> GammaMarket:
        """Parse a market dict from the Gamma API.

        The Gamma API returns outcomePrices in multiple formats:
          - JSON string: '[\"0.35\",\"0.65\"]' or '[0.35,0.65]'
          - Already a list: [0.35, 0.65] or ["0.35", "0.65"]
          - Absent entirely
        """
        yes_price = 0.5
        no_price = 0.5
        raw_prices = data.get("outcomePrices")
        if raw_prices is not None:
            try:
                if isinstance(raw_prices, str):
                    import json
                    parsed = json.loads(raw_prices)
                elif isinstance(raw_prices, list):
                    parsed = raw_prices
                else:
                    parsed = [0.5, 0.5]
                if len(parsed) >= 2:
                    yes_price = float(parsed[0])
                    no_price = float(parsed[1])
                elif len(parsed) == 1:
                    yes_price = float(parsed[0])
                    no_price = 1.0 - yes_price
            except (json.JSONDecodeError, ValueError, TypeError):
                yes_price = 0.5
                no_price = 0.5

        best_bid = float(data.get("bestBid") or yes_price)
        best_ask = float(data.get("bestAsk") or yes_price)

        end_str = data.get("endDate") or data.get("end_date_iso")
        end_date = None
        if end_str:
            try:
                end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return cls(
            id=str(data.get("id", "")),
            condition_id=str(data.get("conditionId", data.get("condition_id", ""))),
            question=data.get("question", ""),
            category=data.get("groupItemTitle", data.get("category", "Other")),
            end_date=end_date,
            active=data.get("active", True),
            closed=data.get("closed", False),
            liquidity=float(data.get("liquidity", 0)),
            volume=float(data.get("volume", 0)),
            volume_24h=float(data.get("volume24hr", 0)),
            yes_price=yes_price,
            no_price=no_price,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=best_ask - best_bid,
            outcomes=data.get("outcomes", ["Yes", "No"]),
            raw=data,
        )


class GammaClient:
    """Async client for the Polymarket Gamma API."""

    def __init__(self, base_url: str = GAMMA_BASE) -> None:
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        session = await self._get_session()
        url = f"{self.base_url}{path}"
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
        order: str = "liquidity",
        ascending: bool = False,
    ) -> list[GammaMarket]:
        """Fetch markets sorted by liquidity (descending by default)."""
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
        }
        data = await self._get("/markets", params=params)
        if isinstance(data, list):
            return [GammaMarket.from_api(m) for m in data]
        return []

    async def get_market(self, market_id: str) -> Optional[GammaMarket]:
        """Fetch a single market by ID."""
        try:
            data = await self._get(f"/markets/{market_id}")
            if data:
                return GammaMarket.from_api(data)
        except Exception as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
        return None

    async def get_active_markets(
        self,
        min_liquidity: float = 25_000,
        limit: int = 50,
    ) -> list[GammaMarket]:
        """Get active markets filtered by minimum liquidity."""
        markets = await self.get_markets(limit=limit, active=True, closed=False)
        return [m for m in markets if m.liquidity >= min_liquidity]

    async def get_events(self, limit: int = 50) -> list[dict]:
        """Fetch events (groups of related markets)."""
        return await self._get("/events", params={"limit": limit})
