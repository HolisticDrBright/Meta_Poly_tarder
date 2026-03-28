"""
Error Taxonomy + Postmortem — classify every loss so the system
doesn't repeat the same mistakes.

After 20+ errors of the same type:
  - Increase the weight of the corresponding hard block
  - Add the pattern to the Red Team's default objection list
  - Reduce Kelly fraction in the category where errors cluster
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from statistics import mean
from typing import Optional


class ErrorType(str, Enum):
    RULES_MISREAD = "rules_misread"
    FAKE_CATALYST = "fake_catalyst"
    OVERCONFIDENCE = "overconfidence"
    BASE_RATE_IGNORED = "base_rate_ignored"
    CROWDING_TRAP = "crowding_trap"
    EXECUTION_SLIPPAGE = "execution_slippage"
    TIMING_ERROR = "timing_error"
    CORRELATION_IGNORED = "correlation_ignored"
    ORACLE_SURPRISE = "oracle_surprise"


ERROR_DESCRIPTIONS = {
    ErrorType.RULES_MISREAD: "Traded the headline, missed actual resolution criteria",
    ErrorType.FAKE_CATALYST: "Reacted to narrative without genuine new information",
    ErrorType.OVERCONFIDENCE: "Model probability was too extreme; edge was smaller",
    ErrorType.BASE_RATE_IGNORED: "Ignored historical frequency; recency bias",
    ErrorType.CROWDING_TRAP: "Entered a crowded position just as consensus broke",
    ErrorType.EXECUTION_SLIPPAGE: "Edge existed but spread/slippage ate the return",
    ErrorType.TIMING_ERROR: "Right direction, wrong timing; theta worked against us",
    ErrorType.CORRELATION_IGNORED: "Multiple positions on same underlying event",
    ErrorType.ORACLE_SURPRISE: "Resolution source behaved unexpectedly",
}


@dataclass
class Postmortem:
    """Post-trade analysis after market resolution."""

    trade_id: str
    market_id: str
    strategy: str
    model_prob: float
    market_price_at_entry: float
    outcome: float  # 1.0 = YES, 0.0 = NO
    pnl: float
    brier_score: float
    error_type: Optional[ErrorType] = None
    notes: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def was_loss(self) -> bool:
        return self.pnl < 0


@dataclass
class ForecastRecord:
    """Stored after every market resolution for Brier tracking."""

    market_id: str
    prediction_date: datetime
    model_probability: float
    market_price_at_entry: float
    edge_type: str
    strategy_source: str
    outcome: float
    brier_score: float


class ErrorTracker:
    """Track errors and learn from mistakes."""

    def __init__(self) -> None:
        self.postmortems: list[Postmortem] = []
        self.forecast_records: list[ForecastRecord] = []

    def add_postmortem(self, pm: Postmortem) -> None:
        self.postmortems.append(pm)

    def add_forecast(self, record: ForecastRecord) -> None:
        self.forecast_records.append(record)

    def error_counts(self) -> Counter:
        """Count errors by type."""
        return Counter(
            pm.error_type.value
            for pm in self.postmortems
            if pm.error_type and pm.was_loss
        )

    def repeat_offenders(self, threshold: int = 20) -> list[ErrorType]:
        """Error types that have occurred >= threshold times."""
        counts = self.error_counts()
        return [
            ErrorType(err)
            for err, count in counts.items()
            if count >= threshold
        ]

    def strategy_brier_scores(self) -> dict[str, float]:
        """Average Brier score per strategy."""
        by_strategy: dict[str, list[float]] = defaultdict(list)
        for r in self.forecast_records:
            by_strategy[r.strategy_source].append(r.brier_score)
        return {
            strategy: mean(scores)
            for strategy, scores in by_strategy.items()
            if len(scores) >= 5
        }

    def recalibrated_weights(self) -> dict[str, float]:
        """
        Compute strategy weights from Brier scores.

        Lower Brier = better → higher weight.
        """
        brier = self.strategy_brier_scores()
        if not brier:
            return {}
        # Invert: weight = 1 / (brier + 0.01)
        raw = {k: 1.0 / (v + 0.01) for k, v in brier.items()}
        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()}

    def kelly_adjustment(self, strategy: str, base_kelly: float = 0.25) -> float:
        """
        Reduce Kelly fraction for strategies that cluster errors.

        After 20+ errors, reduce by 25%. After 50+, reduce by 50%.
        """
        loss_count = sum(
            1 for pm in self.postmortems
            if pm.strategy == strategy and pm.was_loss
        )
        if loss_count >= 50:
            return base_kelly * 0.5
        if loss_count >= 20:
            return base_kelly * 0.75
        return base_kelly
