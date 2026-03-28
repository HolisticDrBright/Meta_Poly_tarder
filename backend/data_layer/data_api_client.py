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
        pnl = float(data.get("pnl", data.get("profit", 0)))
        volume = float(data.get("volume", 0))

        # tier based on PnL
        if pnl > 100_000:
            tier = "legendary"
        elif pnl > 25_000:
            tier = "elite"
        elif pnl > 5_000:
            tier = "pro"
        else:
            tier = "rising"

        return cls(
            rank=rank,
            address=data.get("address", data.get("proxyWallet", "")),
            display_name=data.get("displayName", data.get("username", "")),
            pnl=pnl,
            volume=volume,
            markets_traded=int(data.get("marketsTraded", data.get("numMarkets", 0))),
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
    """Async client for the Polymarket Data API."""

    def __init__(self, base_url: str = DATA_API_BASE) -> None:
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get(self, path: str, params: dict | None = None) -> Any:
        session = await self._get_session()
        url = f"{self.base_url}{path}"
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_leaderboard(self, limit: int = 25) -> list[LeaderboardEntry]:
        """Fetch top traders from the leaderboard."""
        try:
            data = await self._get("/leaderboard", params={"limit": limit})
            entries = data if isinstance(data, list) else data.get("results", [])
            return [
                LeaderboardEntry.from_api(entry, rank=i + 1)
                for i, entry in enumerate(entries[:limit])
            ]
        except Exception as e:
            logger.error(f"Leaderboard fetch failed: {e}")
            return []

    async def get_wallet_positions(self, address: str) -> list[WalletPosition]:
        """Fetch open positions for a wallet address."""
        try:
            data = await self._get(f"/positions", params={"address": address})
            positions = data if isinstance(data, list) else data.get("positions", [])
            return [
                WalletPosition(
                    market_id=p.get("marketId", p.get("market", "")),
                    question=p.get("question", ""),
                    side=p.get("side", "YES"),
                    size=float(p.get("size", 0)),
                    avg_price=float(p.get("avgPrice", 0)),
                    current_price=float(p.get("currentPrice", 0)),
                    pnl=float(p.get("pnl", 0)),
                )
                for p in positions
            ]
        except Exception as e:
            logger.error(f"Wallet positions fetch failed for {address}: {e}")
            return []

    async def get_wallet_trades(
        self, address: str, limit: int = 50
    ) -> list[dict]:
        """Fetch recent trades for a wallet."""
        try:
            return await self._get(
                f"/trades", params={"address": address, "limit": limit}
            )
        except Exception as e:
            logger.error(f"Wallet trades fetch failed: {e}")
            return []

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
