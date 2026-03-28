"""
Async rate limiter with token bucket algorithm.

Prevents API bans by enforcing per-client request limits.
Each API client gets its own limiter instance.

Default limits (conservative):
  Gamma API:    2 req/s  (no auth, strict)
  CLOB REST:    5 req/s  (authed)
  Data API:     2 req/s  (no auth)
  OpenSky:      1 req/10s (free tier)
  ADS-B Exchange: 1 req/s (paid)
  Binance:      10 req/s (generous)
  NewsAPI:      1 req/s  (free tier: 100/day)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RateLimiter:
    """Token bucket rate limiter."""

    name: str
    max_tokens: float          # bucket capacity
    refill_rate: float         # tokens per second
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _total_waits: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        self._tokens = self.max_tokens
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.max_tokens, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    async def acquire(self, tokens: float = 1.0) -> None:
        """Wait until enough tokens are available, then consume them."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # Calculate wait time
                deficit = tokens - self._tokens
                wait_time = deficit / self.refill_rate
                self._total_waits += 1
                if self._total_waits % 50 == 0:
                    logger.debug(
                        f"Rate limiter [{self.name}]: {self._total_waits} waits so far"
                    )
                # Release lock while sleeping
                self._lock.release()
                try:
                    await asyncio.sleep(wait_time)
                finally:
                    await self._lock.acquire()

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self._tokens


# Pre-configured limiters for each API
GAMMA_LIMITER = RateLimiter(name="gamma", max_tokens=3, refill_rate=2.0)
CLOB_LIMITER = RateLimiter(name="clob", max_tokens=5, refill_rate=5.0)
DATA_API_LIMITER = RateLimiter(name="data_api", max_tokens=3, refill_rate=2.0)
OPENSKY_LIMITER = RateLimiter(name="opensky", max_tokens=1, refill_rate=0.1)
ADSBX_LIMITER = RateLimiter(name="adsbx", max_tokens=2, refill_rate=1.0)
BINANCE_LIMITER = RateLimiter(name="binance", max_tokens=10, refill_rate=10.0)
NEWS_LIMITER = RateLimiter(name="news", max_tokens=2, refill_rate=1.0)
