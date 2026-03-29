"""
Polymarket CLOB WebSocket client with automatic reconnection.

The CLOB API provides:
- Real-time order book updates (L2 depth)
- Trade stream
- Price updates

WebSocket endpoint: wss://ws-subscriptions-clob.polymarket.com/ws/market
REST endpoint: https://clob.polymarket.com
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)

CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CLOB_REST_URL = "https://clob.polymarket.com"


@dataclass
class OrderBookLevel:
    price: float
    size: float


@dataclass
class OrderBook:
    market_id: str
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 1.0

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def bid_depth(self) -> float:
        return sum(level.size for level in self.bids)

    @property
    def ask_depth(self) -> float:
        return sum(level.size for level in self.asks)


@dataclass
class Trade:
    market_id: str
    price: float
    size: float
    side: str  # "BUY" or "SELL"
    timestamp: datetime


class CLOBWebSocketClient:
    """Real-time WebSocket client with automatic reconnection and exponential backoff."""

    MAX_RECONNECT_DELAY = 60  # seconds
    INITIAL_RECONNECT_DELAY = 1  # seconds

    def __init__(self, url: str = CLOB_WS_URL) -> None:
        self.url = url
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._subscribed_markets: set[str] = set()
        self._order_books: dict[str, OrderBook] = {}
        self._callbacks: dict[str, list[Callable]] = {
            "book_update": [],
            "trade": [],
            "price_change": [],
            "connected": [],
            "disconnected": [],
        }
        self._running = False
        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
        self._reconnect_task: Optional[asyncio.Task] = None

    def on(self, event: str, callback: Callable) -> None:
        """Register a callback for an event type."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    async def connect(self) -> None:
        """Establish WebSocket connection."""
        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(
                self.url,
                heartbeat=30,
                timeout=aiohttp.ClientTimeout(total=15),
            )
            self._running = True
            self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
            logger.info("Connected to CLOB WebSocket")
            for cb in self._callbacks["connected"]:
                await cb() if asyncio.iscoroutinefunction(cb) else cb()
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            raise

    async def subscribe(self, market_id: str) -> None:
        """Subscribe to order book updates for a market."""
        if self._ws is None or self._ws.closed:
            logger.warning("Cannot subscribe — not connected")
            return
        msg = {"type": "subscribe", "market": market_id}
        await self._ws.send_json(msg)
        self._subscribed_markets.add(market_id)
        self._order_books[market_id] = OrderBook(market_id=market_id)
        logger.debug(f"Subscribed to market {market_id}")

    async def unsubscribe(self, market_id: str) -> None:
        if self._ws is None or self._ws.closed:
            return
        msg = {"type": "unsubscribe", "market": market_id}
        await self._ws.send_json(msg)
        self._subscribed_markets.discard(market_id)

    async def _resubscribe_all(self) -> None:
        """Re-subscribe to all markets after reconnection."""
        for market_id in list(self._subscribed_markets):
            try:
                await self.subscribe(market_id)
            except Exception as e:
                logger.warning(f"Resubscribe failed for {market_id}: {e}")

    async def listen(self) -> None:
        """Main message loop with automatic reconnection."""
        while self._running:
            try:
                if self._ws is None or self._ws.closed:
                    await self.connect()
                    await self._resubscribe_all()

                async for msg in self._ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            await self._handle_message(data)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON: {msg.data[:100]}")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        logger.warning(f"WebSocket closed/error: {msg.type}")
                        break
                    elif msg.type == aiohttp.WSMsgType.PING:
                        if self._ws and not self._ws.closed:
                            await self._ws.pong()

            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                logger.warning(f"WebSocket error: {e}")

            if self._running:
                # Reconnect with exponential backoff
                for cb in self._callbacks["disconnected"]:
                    await cb() if asyncio.iscoroutinefunction(cb) else cb()

                logger.info(
                    f"Reconnecting in {self._reconnect_delay}s "
                    f"(max {self.MAX_RECONNECT_DELAY}s)..."
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self.MAX_RECONNECT_DELAY
                )

    async def _handle_message(self, data: dict[str, Any]) -> None:
        """Route message to appropriate handler."""
        msg_type = data.get("type", data.get("event_type", ""))

        if msg_type in ("book", "book_update"):
            market_id = data.get("market", data.get("asset_id", ""))
            book = self._order_books.get(market_id)
            if book:
                self._update_book(book, data)
                for cb in self._callbacks["book_update"]:
                    await cb(book) if asyncio.iscoroutinefunction(cb) else cb(book)

        elif msg_type == "trade":
            trade = Trade(
                market_id=data.get("market", ""),
                price=float(data.get("price", 0)),
                size=float(data.get("size", 0)),
                side=data.get("side", "BUY"),
                timestamp=datetime.now(timezone.utc),
            )
            for cb in self._callbacks["trade"]:
                await cb(trade) if asyncio.iscoroutinefunction(cb) else cb(trade)

        elif msg_type == "price_change":
            for cb in self._callbacks["price_change"]:
                await cb(data) if asyncio.iscoroutinefunction(cb) else cb(data)

    def _update_book(self, book: OrderBook, data: dict) -> None:
        """Apply incremental book update."""
        if "bids" in data:
            book.bids = [
                OrderBookLevel(price=float(b[0]), size=float(b[1]))
                for b in data["bids"]
                if float(b[1]) > 0
            ]
            book.bids.sort(key=lambda x: x.price, reverse=True)
        if "asks" in data:
            book.asks = [
                OrderBookLevel(price=float(a[0]), size=float(a[1]))
                for a in data["asks"]
                if float(a[1]) > 0
            ]
            book.asks.sort(key=lambda x: x.price)
        book.timestamp = datetime.now(timezone.utc)

    def get_book(self, market_id: str) -> Optional[OrderBook]:
        return self._order_books.get(market_id)

    async def close(self) -> None:
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()


class CLOBRestClient:
    """REST client for Polymarket CLOB — order placement and book snapshots (rate-limited)."""

    def __init__(self, base_url: str = CLOB_REST_URL) -> None:
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None
        from backend.data_layer.rate_limiter import CLOB_LIMITER
        self._limiter = CLOB_LIMITER

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            from backend.data_layer.proxy import get_proxied_session
            self._session = get_proxied_session()
        return self._session

    async def get_order_book(self, token_id: str) -> dict:
        """Fetch L2 order book snapshot."""
        await self._limiter.acquire()
        session = await self._get_session()
        url = f"{self.base_url}/book"
        async with session.get(url, params={"token_id": token_id}) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_midpoint(self, token_id: str) -> float:
        """Fetch current midpoint price."""
        await self._limiter.acquire()
        session = await self._get_session()
        url = f"{self.base_url}/midpoint"
        async with session.get(url, params={"token_id": token_id}) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return float(data.get("mid", 0.5))

    async def get_spread(self, token_id: str) -> dict:
        """Fetch current bid-ask spread."""
        await self._limiter.acquire()
        session = await self._get_session()
        url = f"{self.base_url}/spread"
        async with session.get(url, params={"token_id": token_id}) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
