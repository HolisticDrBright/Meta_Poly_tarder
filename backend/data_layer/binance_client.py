"""
Binance REST client for spot prices.

Used by the Binance arb strategy to detect when Polymarket's binary
"will BTC hit $X by Y" markets lag the real Binance spot price. Zero
AI cost, zero authentication — public price endpoint.

Uses the shared proxy factory (proxy.py). When PROXY_URL is unset,
sessions connect directly with no proxy.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from backend.data_layer.proxy import get_proxy_url

logger = logging.getLogger(__name__)


BINANCE_BASE = "https://api.binance.com"
PRICE_ENDPOINT = f"{BINANCE_BASE}/api/v3/ticker/price"
# 24h ticker endpoint gives us price + 24h range which is useful for
# a crude realized-vol estimate without pulling historical klines.
TICKER_24H_ENDPOINT = f"{BINANCE_BASE}/api/v3/ticker/24hr"


# Map of asset name aliases (as they appear in Polymarket questions) to
# Binance spot symbols. Extend as new crypto markets appear.
ASSET_TO_SYMBOL: dict[str, str] = {
    "bitcoin": "BTCUSDT",
    "btc": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "ether": "ETHUSDT",
    "eth": "ETHUSDT",
    "solana": "SOLUSDT",
    "sol": "SOLUSDT",
    "ripple": "XRPUSDT",
    "xrp": "XRPUSDT",
    "dogecoin": "DOGEUSDT",
    "doge": "DOGEUSDT",
    "cardano": "ADAUSDT",
    "ada": "ADAUSDT",
    "polkadot": "DOTUSDT",
    "dot": "DOTUSDT",
    "chainlink": "LINKUSDT",
    "link": "LINKUSDT",
    "avalanche": "AVAXUSDT",
    "avax": "AVAXUSDT",
    "polygon": "MATICUSDT",
    "matic": "MATICUSDT",
    "binance coin": "BNBUSDT",
    "bnb": "BNBUSDT",
}


@dataclass
class BinanceTicker:
    """Price + 24h range for one asset."""
    symbol: str
    price: float
    high_24h: float
    low_24h: float
    price_change_pct_24h: float
    fetched_at: float = field(default_factory=time.time)

    @property
    def realized_vol_24h(self) -> float:
        """Crude daily realized-vol proxy from the 24h high-low range.

        For a log-normal asset, the Parkinson estimator approximates
        daily sigma as (ln(high/low)) / (2·sqrt(ln 2)) ≈ 0.6 · ln(H/L).
        """
        import math
        if self.low_24h <= 0 or self.high_24h <= 0:
            return 0.0
        return 0.6 * math.log(self.high_24h / self.low_24h)


class BinanceClient:
    """Thin async client for Binance public spot endpoints."""

    def __init__(self, cache_ttl_sec: float = 10.0) -> None:
        self.cache_ttl = cache_ttl_sec
        self._session: Optional[aiohttp.ClientSession] = None
        # Flat cache keyed by symbol. Every request always fetches the
        # full default symbol set (all known crypto pairs) so the cache
        # stays consistent regardless of which subset any given caller
        # asks for.
        self._cache: dict[str, BinanceTicker] = {}
        self._cache_fetched_at: float = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_all_tickers(self, symbols: Optional[list[str]] = None) -> dict[str, BinanceTicker]:
        """Fetch 24h ticker data for all default crypto symbols. Cached.

        The `symbols` parameter is accepted for API compatibility but is
        ignored for the actual fetch — we always fetch the full default
        set so the cache is complete regardless of which caller asked
        for which subset. The caller gets back a dict filtered to their
        requested subset (or the full set if None).

        This prevents a subtle bug where the first caller asking for
        just BTCUSDT would cache only that, and the next caller asking
        for [BTCUSDT, ETHUSDT] would get a stale cache missing ETH.
        """
        now = time.time()
        default_symbols = sorted(set(ASSET_TO_SYMBOL.values()))

        # If cache is fresh, serve from it (filtered to requested subset)
        if self._cache and (now - self._cache_fetched_at) < self.cache_ttl:
            if symbols is None:
                return dict(self._cache)
            return {s: self._cache[s] for s in symbols if s in self._cache}

        session = await self._get_session()
        proxy = get_proxy_url()

        # Binance /api/v3/ticker/24hr requires `symbol=X` for one item
        # and `symbols=["A","B"]` for multiple. Passing `symbols=["X"]`
        # for a single item is documented to work but some deployments
        # return 400. Always fetch the full set to sidestep this.
        tickers: dict[str, BinanceTicker] = {}
        try:
            import json as _json
            if len(default_symbols) == 1:
                params = {"symbol": default_symbols[0]}
            else:
                params = {"symbols": _json.dumps(default_symbols)}
            async with session.get(TICKER_24H_ENDPOINT, params=params, proxy=proxy) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        f"Binance ticker fetch HTTP {resp.status}: {body[:200]}"
                    )
                    # Keep stale cache rather than dropping to empty
                    if symbols is None:
                        return dict(self._cache)
                    return {s: self._cache[s] for s in symbols if s in self._cache}
                data = await resp.json()
            if not isinstance(data, list):
                data = [data]
            for row in data:
                try:
                    t = BinanceTicker(
                        symbol=row["symbol"],
                        price=float(row["lastPrice"]),
                        high_24h=float(row["highPrice"]),
                        low_24h=float(row["lowPrice"]),
                        price_change_pct_24h=float(row["priceChangePercent"]),
                    )
                    tickers[t.symbol] = t
                except (KeyError, ValueError, TypeError) as e:
                    logger.debug(f"Skipping malformed ticker row: {e}")
        except Exception as e:
            logger.warning(f"Binance ticker fetch failed: {e}")
            # Serve stale cache on network errors
            if symbols is None:
                return dict(self._cache)
            return {s: self._cache[s] for s in symbols if s in self._cache}

        self._cache = tickers
        self._cache_fetched_at = now
        logger.info(f"Binance: fetched {len(tickers)} tickers")

        # Return filtered to requested subset (or full)
        if symbols is None:
            return dict(tickers)
        return {s: tickers[s] for s in symbols if s in tickers}

    async def get_price(self, symbol: str) -> Optional[float]:
        """Get just the current price for one symbol. Uses the cached batch."""
        tickers = await self.get_all_tickers()
        t = tickers.get(symbol)
        return t.price if t else None


# Module-level singleton
_client: Optional[BinanceClient] = None


def get_binance_client() -> BinanceClient:
    global _client
    if _client is None:
        _client = BinanceClient()
    return _client
