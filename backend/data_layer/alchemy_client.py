"""
Alchemy Polygon PoS client.

Reads real on-chain data from Polygon mainnet (chain 137) — the network
where Polymarket's Conditional Token Framework, exchange contract, and
USDC collateral all live.

Used by the On-Chain Flow specialist to surface:
  - Large USDC transfers into/out of the Polymarket exchange contract
  - CTF position mints / burns for a given condition_id
  - Whale wallet activity (balance changes on tracked addresses)

Only real chain data — no fallbacks, no synthetic data. If the HTTP call
fails, methods return empty structures and the specialist skips the market.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

from backend.config import settings

logger = logging.getLogger(__name__)

# Polymarket contracts on Polygon PoS (chain 137)
POLYMARKET_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # CTF exchange
POLYMARKET_NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
POLYMARKET_CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"        # ConditionalTokens
USDC_E_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"         # bridged USDC


@dataclass
class OnChainFlow:
    """A single on-chain transfer observed relevant to a market."""
    tx_hash: str
    block_number: int
    from_addr: str
    to_addr: str
    token: str  # "USDC" or "CTF"
    amount_usd: float
    direction: str  # "deposit" | "withdrawal" | "position"
    timestamp: Optional[int] = None


@dataclass
class OnChainSnapshot:
    """Aggregated on-chain picture for a market / window."""
    market_id: str
    condition_id: str
    window_blocks: int
    total_inflow_usd: float = 0.0
    total_outflow_usd: float = 0.0
    net_flow_usd: float = 0.0
    large_transfers: list[OnChainFlow] = None
    unique_addresses: int = 0

    def __post_init__(self) -> None:
        if self.large_transfers is None:
            self.large_transfers = []


class AlchemyPolygonClient:
    """Thin async wrapper over Alchemy's JSON-RPC + enhanced APIs."""

    def __init__(self, url: Optional[str] = None) -> None:
        self.url = url or settings.specialists.alchemy_polygon_url
        self._session: Optional[aiohttp.ClientSession] = None

    def is_configured(self) -> bool:
        return bool(self.url and "alchemy.com" in self.url)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        """Single JSON-RPC call. Returns `result` or raises."""
        if not self.is_configured():
            raise RuntimeError("Alchemy URL not configured")
        session = await self._get_session()
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with session.post(self.url, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if "error" in data:
                raise RuntimeError(f"Alchemy RPC error: {data['error']}")
            return data.get("result")

    async def get_block_number(self) -> int:
        """Current Polygon head block."""
        result = await self._rpc("eth_blockNumber", [])
        return int(result, 16) if result else 0

    async def get_asset_transfers(
        self,
        from_block: int,
        to_block: int | str = "latest",
        from_address: Optional[str] = None,
        to_address: Optional[str] = None,
        contract_addresses: Optional[list[str]] = None,
        category: Optional[list[str]] = None,
        max_count: int = 100,
    ) -> list[dict]:
        """
        Alchemy Transfers API — batched token movement lookups.
        Docs: https://docs.alchemy.com/reference/alchemy-getassettransfers
        """
        params: dict[str, Any] = {
            "fromBlock": hex(from_block),
            "toBlock": "latest" if to_block == "latest" else hex(to_block),
            "category": category or ["erc20", "erc1155"],
            "withMetadata": True,
            "excludeZeroValue": True,
            "maxCount": hex(min(max_count, 1000)),
        }
        if from_address:
            params["fromAddress"] = from_address
        if to_address:
            params["toAddress"] = to_address
        if contract_addresses:
            params["contractAddresses"] = contract_addresses

        result = await self._rpc("alchemy_getAssetTransfers", [params])
        return (result or {}).get("transfers", []) if result else []

    async def get_exchange_flows(
        self,
        window_blocks: int = 1800,  # ~60 min on Polygon (2s blocks)
        min_usd: float = 5000.0,
    ) -> OnChainSnapshot:
        """
        Snapshot of recent USDC flows into/out of the Polymarket exchange.
        A high inflow = whales arming up; a high outflow = taking profit.
        """
        head = await self.get_block_number()
        if head == 0:
            return OnChainSnapshot(market_id="", condition_id="", window_blocks=window_blocks)
        from_block = max(0, head - window_blocks)

        # Pull USDC transfers touching the exchange in either direction.
        inflows, outflows = [], []
        try:
            inflows = await self.get_asset_transfers(
                from_block=from_block,
                to_address=POLYMARKET_EXCHANGE,
                contract_addresses=[USDC_E_POLYGON],
                category=["erc20"],
                max_count=200,
            )
            outflows = await self.get_asset_transfers(
                from_block=from_block,
                from_address=POLYMARKET_EXCHANGE,
                contract_addresses=[USDC_E_POLYGON],
                category=["erc20"],
                max_count=200,
            )
        except Exception as e:
            logger.warning(f"Alchemy exchange flow fetch failed: {e}")
            return OnChainSnapshot(market_id="", condition_id="", window_blocks=window_blocks)

        snap = OnChainSnapshot(
            market_id="",
            condition_id="",
            window_blocks=window_blocks,
        )
        addrs: set[str] = set()

        for t in inflows:
            usd = float(t.get("value") or 0)
            if usd <= 0:
                continue
            snap.total_inflow_usd += usd
            addrs.add((t.get("from") or "").lower())
            if usd >= min_usd:
                snap.large_transfers.append(OnChainFlow(
                    tx_hash=t.get("hash", ""),
                    block_number=int(t.get("blockNum", "0x0"), 16),
                    from_addr=t.get("from", ""),
                    to_addr=t.get("to", ""),
                    token="USDC",
                    amount_usd=usd,
                    direction="deposit",
                ))

        for t in outflows:
            usd = float(t.get("value") or 0)
            if usd <= 0:
                continue
            snap.total_outflow_usd += usd
            addrs.add((t.get("to") or "").lower())
            if usd >= min_usd:
                snap.large_transfers.append(OnChainFlow(
                    tx_hash=t.get("hash", ""),
                    block_number=int(t.get("blockNum", "0x0"), 16),
                    from_addr=t.get("from", ""),
                    to_addr=t.get("to", ""),
                    token="USDC",
                    amount_usd=usd,
                    direction="withdrawal",
                ))

        snap.net_flow_usd = snap.total_inflow_usd - snap.total_outflow_usd
        snap.unique_addresses = len(addrs)
        # Keep only the largest N transfers for prompt compactness
        snap.large_transfers.sort(key=lambda f: f.amount_usd, reverse=True)
        snap.large_transfers = snap.large_transfers[:15]
        return snap


# Module-level singleton so we reuse the HTTP session.
_client: Optional[AlchemyPolygonClient] = None


def get_alchemy_client() -> AlchemyPolygonClient:
    global _client
    if _client is None:
        _client = AlchemyPolygonClient()
    return _client
