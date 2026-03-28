"""
Strategy 5: Jet Tracker Signal Strategy

Not a standalone trading strategy — acts as a signal PROVIDER that
feeds into the entropy screener, AI debate floor, and signal aggregator.

Signal weights:
  STRONG  (< 10nm from POI, weekend):  +0.15 boost
  MODERATE (10-30nm, weekday):          +0.08 boost
  WEAK    (30-50nm, no timing premium): +0.04 boost

PDUFA integration: cross-reference active pharma markets against
FDA PDUFA dates for compound signals.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.data_layer.adsb_client import ADSBClient, JetSignal, PointOfInterest
from backend.strategies.base import (
    MarketState,
    OrderIntent,
    OrderType,
    Side,
    Strategy,
    StrategyName,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

SIGNAL_BOOST = {
    "strong": 0.15,
    "moderate": 0.08,
    "weak": 0.04,
}


@dataclass
class JetMarketSignal:
    """A jet signal matched to a prediction market."""

    jet_signal: JetSignal
    market_id: str
    question: str
    probability_boost: float
    is_pdufa_compound: bool = False


class JetSignalStrategy(Strategy):
    """Jet tracking signal provider."""

    name = StrategyName.JET

    def __init__(
        self,
        adsb_client: Optional[ADSBClient] = None,
        pdufa_path: Path = DATA_DIR / "pdufa_calendar.json",
        manual_confirm: bool = True,
    ) -> None:
        self.adsb_client = adsb_client
        self.manual_confirm = manual_confirm
        self._pdufa_dates: dict[str, str] = {}
        self._active_signals: list[JetMarketSignal] = []
        self._load_pdufa(pdufa_path)

    def _load_pdufa(self, path: Path) -> None:
        if path.exists():
            try:
                with open(path) as f:
                    self._pdufa_dates = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load PDUFA calendar: {e}")

    def _is_weekend(self) -> bool:
        return datetime.now(timezone.utc).weekday() >= 5

    def compute_boost(self, signal: JetSignal) -> float:
        """Compute probability boost from a jet signal."""
        base = SIGNAL_BOOST.get(signal.signal_strength, 0.04)
        if self._is_weekend() and signal.signal_strength == "strong":
            base *= 1.25  # weekend premium
        return base

    def check_pdufa_compound(
        self, signal: JetSignal, market_question: str
    ) -> bool:
        """Check if signal + market creates a PDUFA compound signal."""
        for drug, date_str in self._pdufa_dates.items():
            if drug.lower() in market_question.lower():
                try:
                    pdufa_date = datetime.fromisoformat(date_str)
                    days_to_pdufa = (pdufa_date - datetime.now(timezone.utc)).days
                    if 0 <= days_to_pdufa <= 14:
                        return True
                except ValueError:
                    pass
        return False

    def match_signals_to_markets(
        self,
        signals: list[JetSignal],
        markets: list[MarketState],
    ) -> list[JetMarketSignal]:
        """Cross-reference jet signals with active markets."""
        matched = []
        for sig in signals:
            for market in markets:
                # Match via market_tags on the POI
                q_lower = market.question.lower()
                if any(tag.lower() in q_lower for tag in sig.market_tags):
                    boost = self.compute_boost(sig)
                    is_compound = self.check_pdufa_compound(sig, market.question)
                    if is_compound:
                        boost *= 2.0  # PDUFA compound = max sizing
                    matched.append(
                        JetMarketSignal(
                            jet_signal=sig,
                            market_id=market.market_id,
                            question=market.question,
                            probability_boost=boost,
                            is_pdufa_compound=is_compound,
                        )
                    )
        self._active_signals = matched
        return matched

    async def evaluate(self, market_state: MarketState) -> Optional[OrderIntent]:
        """Check if any active jet signal applies to this market."""
        for sig in self._active_signals:
            if sig.market_id == market_state.market_id:
                boost = sig.probability_boost
                if boost < 0.04:
                    continue

                side = Side.YES  # jet signals are generally bullish for the event
                price = market_state.yes_price
                size = 25.0 if sig.is_pdufa_compound else 15.0

                return OrderIntent(
                    strategy=self.name,
                    market_id=market_state.market_id,
                    condition_id=market_state.condition_id,
                    question=market_state.question,
                    side=side,
                    order_type=OrderType.LIMIT,
                    price=price,
                    size_usdc=size,
                    confidence=min(boost / 0.15, 1.0),
                    reason=(
                        f"JET: {sig.jet_signal.aircraft.target_name} → "
                        f"{sig.jet_signal.poi.name} "
                        f"({sig.jet_signal.distance_nm:.1f}nm, "
                        f"{sig.jet_signal.signal_strength})"
                        f"{' [PDUFA COMPOUND]' if sig.is_pdufa_compound else ''}"
                    ),
                )
        return None

    async def evaluate_batch(self, markets: list[MarketState]) -> list[OrderIntent]:
        intents = []
        for m in markets:
            intent = await self.evaluate(m)
            if intent:
                intents.append(intent)
        return intents

    @property
    def active_signals(self) -> list[JetMarketSignal]:
        return self._active_signals
