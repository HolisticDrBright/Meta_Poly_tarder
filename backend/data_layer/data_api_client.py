"""
Polymarket Data API client — leaderboard, wallet activity, trade history.

The Data API provides user/wallet-level data:
  - Leaderboard rankings
  - Wallet positions and trade history
  - Market activity and volume data

Base URL: https://data-api.polymarket.com
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

DATA_API_BASE = "https://data-api.polymarket.com"


@dataclass
class LeaderboardEntry:
    """A single wallet on the leaderboard."""

    rank: int
    address: str
    display_name: str
    pnl: float
    volume: float
    markets_traded: int
    win_rate: float
    tier: str  # "legendary" | "elite" | "pro" | "rising"

    @classmethod
    def from_api(cls, data: dict[str, Any], rank: int) -> LeaderboardEntry:
        # Fields from Polymarket Data API /v1/leaderboard:
        # rank, proxyWallet, userName, vol, pnl, profileImage, xUsername, verifiedBadge
        pnl = float(data.get("pnl", data.get("profit", 0)) or 0)
        volume = float(data.get("vol", data.get("volume", 0)) or 0)
        api_rank = data.get("rank")

        if pnl > 100_000:
            tier = "legendary"
        elif pnl > 25_000:
            tier = "elite"
        elif pnl > 5_000:
            tier = "pro"
        else:
            tier = "rising"

        return cls(
            rank=int(api_rank) if api_rank else rank,
            address=data.get("proxyWallet", data.get("address", "")),
            display_name=data.get("userName", data.get("displayName", data.get("username", ""))),
            pnl=pnl,
            volume=volume,
            markets_traded=int(data.get("marketsTraded", data.get("numMarkets", 0)) or 0),
            win_rate=float(data.get("winRate", 0)),
            tier=tier,
        )


@dataclass
class WalletPosition:
    """A position held by a wallet."""

    market_id: str
    question: str
    side: str  # "YES" or "NO"
    size: float
    avg_price: float
    current_price: float
    pnl: float


class DataAPIClient:
    """Async client for the Polymarket Data API (rate-limited)."""

    def __init__(self, base_url: str = DATA_API_BASE) -> None:
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None
        from backend.data_layer.rate_limiter import DATA_API_LIMITER
        self._limiter = DATA_API_LIMITER

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            from backend.data_layer.proxy import get_proxied_session
            self._session = get_proxied_session()
        return self._session

    async def _get(self, path: str, params: dict | None = None) -> Any:
        await self._limiter.acquire()
        session = await self._get_session()
        url = f"{self.base_url}{path}"
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_leaderboard(
        self,
        limit: int = 25,
        category: str = "OVERALL",
        time_period: str = "WEEK",
        order_by: str = "PNL",
    ) -> list[LeaderboardEntry]:
        """
        Fetch top traders from the Polymarket leaderboard.

        Endpoint: GET /v1/leaderboard
        Params: category, timePeriod, orderBy, limit, offset
        Response: array of TraderLeaderboardEntry
        """
        try:
            data = await self._get("/v1/leaderboard", params={
                "category": category,
                "timePeriod": time_period,
                "orderBy": order_by,
                "limit": min(limit, 50),
                "offset": 0,
            })
            entries = data if isinstance(data, list) else []
            if entries:
                return [
                    LeaderboardEntry.from_api(entry, rank=i + 1)
                    for i, entry in enumerate(entries[:limit])
                ]
        except Exception as e:
            logger.warning(f"Leaderboard fetch failed: {e}")
        return []

    async def get_wallet_positions(self, address: str) -> list[WalletPosition]:
        """
        Fetch open positions for a wallet address.

        Tries multiple field name conventions since the API format may vary.
        """
        try:
            data = await self._get("/positions", params={"address": address})
            positions = (
                data if isinstance(data, list)
                else data.get("positions", data.get("data", []))
                if isinstance(data, dict) else []
            )
            result = []
            for p in positions:
                try:
                    result.append(WalletPosition(
                        market_id=str(
                            p.get("marketId")
                            or p.get("market_id")
                            or p.get("market")
                            or p.get("conditionId")
                            or ""
                        ),
                        question=p.get("question", p.get("title", "")),
                        side=p.get("side", p.get("outcome", "YES")).upper(),
                        size=float(p.get("size", p.get("amount", p.get("shares", 0)))),
                        avg_price=float(p.get("avgPrice", p.get("avg_price", p.get("price", 0)))),
                        current_price=float(p.get("currentPrice", p.get("current_price", 0))),
                        pnl=float(p.get("pnl", p.get("profit", 0))),
                    ))
                except (ValueError, TypeError) as e:
                    logger.debug(f"Skipping malformed position: {e}")
            return result
        except Exception as e:
            logger.error(f"Wallet positions fetch failed for {address}: {e}")
            return []

    async def get_wallet_trades(
        self, address: str, limit: int = 50
    ) -> list[dict]:
        """
        Fetch recent trades for a wallet.

        Normalizes response into a consistent list of dicts regardless
        of the actual API response shape.
        """
        try:
            data = await self._get(
                "/trades", params={"address": address, "limit": limit}
            )
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return (
                    data.get("trades")
                    or data.get("data")
                    or data.get("results")
                    or []
                )
            return []
        except Exception as e:
            logger.error(f"Wallet trades fetch failed: {e}")
            return []

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
