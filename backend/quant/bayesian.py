"""
Real-time Bayesian probability updating for prediction markets.

Combines prior estimates with incoming signals (whale trades, volume
spikes, jet events, news) to maintain a posterior probability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SignalType(str, Enum):
    WHALE_POSITION = "whale_position"
    VOLUME_SPIKE = "volume_spike"
    JET_SIGNAL = "jet_signal"
    NEWS_EVENT = "news_event"
    COPY_TRADE = "copy_trade"


# Default nudge magnitudes per signal type
DEFAULT_NUDGES: dict[SignalType, float] = {
    SignalType.WHALE_POSITION: 0.05,
    SignalType.VOLUME_SPIKE: 0.03,
    SignalType.JET_SIGNAL: 0.10,
    SignalType.NEWS_EVENT: 0.07,
    SignalType.COPY_TRADE: 0.04,
}


@dataclass
class BayesianTracker:
    """Tracks a posterior probability for a single market."""

    market_id: str
    prior: float
    posterior: float = 0.0
    updates: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.posterior = self.prior

    def update(
        self,
        signal_type: SignalType,
        direction: float,
        strength: float = 1.0,
    ) -> float:
        """
        Apply a Bayesian-style nudge to the posterior.

        Parameters
        ----------
        signal_type : SignalType   Category of signal.
        direction   : float        +1.0 for YES, -1.0 for NO.
        strength    : float        Multiplier on default nudge (0-2).

        Returns
        -------
        float   Updated posterior probability.
        """
        nudge = DEFAULT_NUDGES.get(signal_type, 0.03) * strength * direction
        self.posterior = max(0.01, min(0.99, self.posterior + nudge))
        self.updates.append(
            {
                "signal": signal_type.value,
                "direction": direction,
                "strength": strength,
                "nudge": nudge,
                "posterior": self.posterior,
            }
        )
        return self.posterior

    @property
    def drift(self) -> float:
        """Change from prior to current posterior."""
        return self.posterior - self.prior

    @property
    def should_reevaluate(self) -> bool:
        """True if posterior has drifted > 5% from prior."""
        return abs(self.drift) > 0.05
