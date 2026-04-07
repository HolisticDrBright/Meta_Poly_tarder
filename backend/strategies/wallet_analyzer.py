"""
Wallet Pattern Analyzer — smart money signal from high-performing wallets.

Instead of direct copy-trading, this module:
  1. Monitors a curated list of historically profitable wallets
  2. Tracks their patterns (position frequency, direction, sizing)
  3. When multiple wallets cluster on the same market+side, emits a
     SMART_MONEY signal that boosts confidence in the Signal Aggregator

The signal is confirming, not standalone — it bumps the score of
existing strategy intents rather than generating new trades.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Curated seed list of historically profitable Polymarket wallets.
# Format: (address_prefix, display_name)
# These are public blockchain addresses — no private data.
SEED_WALLETS = [
    ("0x1234", "swisstony"),
    ("0x5678", "Theo4"),
    ("0x9abc", "Len9311238"),
]


@dataclass
class WalletActivity:
    """Aggregated activity from one wallet on one market."""
    wallet: str
    display_name: str
    market_id: str
    side: str  # "YES" or "NO"
    total_size: float = 0.0
    trade_count: int = 0
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SmartMoneySignal:
    """A directional signal from clustered whale activity."""
    market_id: str
    side: str
    wallet_count: int  # how many wallets agree
    total_size: float  # combined USDC from all wallets
    confidence: float  # 0-1 based on wallet count + size
    wallets: list[str] = field(default_factory=list)


class WalletPatternAnalyzer:
    """Analyzes wallet activity patterns and emits smart money signals."""

    def __init__(
        self,
        min_wallets_for_signal: int = 2,
        lookback_hours: int = 24,
        min_size_usdc: float = 50.0,
    ) -> None:
        self.min_wallets = min_wallets_for_signal
        self.lookback_hours = lookback_hours
        self.min_size = min_size_usdc

        # wallet_address -> list of recent activity
        self._activity: dict[str, list[WalletActivity]] = defaultdict(list)
        # wallet_address -> pattern stats
        self._wallet_stats: dict[str, dict] = {}

    def record_activity(
        self,
        wallet: str,
        display_name: str,
        market_id: str,
        side: str,
        size_usdc: float,
    ) -> None:
        """Record a trade from a monitored wallet."""
        now = datetime.now(timezone.utc)

        # Find existing activity for this wallet+market+side
        existing = None
        for a in self._activity[wallet]:
            if a.market_id == market_id and a.side == side:
                existing = a
                break

        if existing:
            existing.total_size += size_usdc
            existing.trade_count += 1
            existing.last_seen = now
        else:
            self._activity[wallet].append(WalletActivity(
                wallet=wallet,
                display_name=display_name,
                market_id=market_id,
                side=side,
                total_size=size_usdc,
                first_seen=now,
                last_seen=now,
                trade_count=1,
            ))

        # Update wallet stats
        if wallet not in self._wallet_stats:
            self._wallet_stats[wallet] = {
                "display_name": display_name,
                "total_trades": 0,
                "categories": defaultdict(int),
                "avg_size": 0.0,
                "total_volume": 0.0,
            }
        stats = self._wallet_stats[wallet]
        stats["total_trades"] += 1
        stats["total_volume"] += size_usdc
        stats["avg_size"] = stats["total_volume"] / max(1, stats["total_trades"])

        logger.debug(
            f"Smart money: {display_name} {side} ${size_usdc:.0f} on {market_id[:12]}.. "
            f"(cumulative: ${existing.total_size if existing else size_usdc:.0f})"
        )

    def _prune_old(self) -> None:
        """Remove activity older than lookback window."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)
        for wallet in list(self._activity.keys()):
            self._activity[wallet] = [
                a for a in self._activity[wallet]
                if a.last_seen >= cutoff
            ]
            if not self._activity[wallet]:
                del self._activity[wallet]

    def get_signals(self) -> list[SmartMoneySignal]:
        """Analyze current wallet activity and return smart money signals.

        A signal fires when >= min_wallets wallets are positioned on
        the same market+side within the lookback window.
        """
        self._prune_old()

        # Group by (market_id, side) -> list of wallet activities
        clusters: dict[tuple[str, str], list[WalletActivity]] = defaultdict(list)
        for wallet, activities in self._activity.items():
            for a in activities:
                if a.total_size >= self.min_size:
                    clusters[(a.market_id, a.side)].append(a)

        signals = []
        for (market_id, side), activities in clusters.items():
            if len(activities) >= self.min_wallets:
                total_size = sum(a.total_size for a in activities)
                wallet_names = [a.display_name for a in activities]

                # Confidence: 0.5 base + 0.1 per wallet + size bonus
                confidence = min(1.0, 0.5 + 0.1 * len(activities) + min(0.2, total_size / 5000))

                signals.append(SmartMoneySignal(
                    market_id=market_id,
                    side=side,
                    wallet_count=len(activities),
                    total_size=total_size,
                    confidence=confidence,
                    wallets=wallet_names,
                ))

                logger.info(
                    f"Smart money signal: {len(activities)} wallets ({', '.join(wallet_names)}) "
                    f"cluster {side} on {market_id[:12]}.. "
                    f"(${total_size:.0f} total, conf={confidence:.2f})"
                )

        return signals

    def boost_intent_score(
        self,
        market_id: str,
        side: str,
        base_score: float,
    ) -> tuple[float, Optional[SmartMoneySignal]]:
        """Boost an existing intent's score if smart money agrees.

        Returns (boosted_score, signal_or_none).
        Called by the SignalAggregator during scoring.
        """
        for signal in self.get_signals():
            if signal.market_id == market_id and signal.side == side:
                # Boost by up to 30% based on wallet confidence
                boost = signal.confidence * 0.30
                boosted = min(1.0, base_score * (1.0 + boost))
                logger.debug(
                    f"Smart money boost: {market_id[:12]}.. {side} "
                    f"score {base_score:.3f} → {boosted:.3f} "
                    f"(+{boost:.1%} from {signal.wallet_count} wallets)"
                )
                return boosted, signal
        return base_score, None

    def get_wallet_stats(self) -> list[dict]:
        """Return pattern statistics for all monitored wallets."""
        return [
            {
                "address": addr,
                **stats,
                "active_positions": len(self._activity.get(addr, [])),
            }
            for addr, stats in self._wallet_stats.items()
        ]
