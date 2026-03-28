"""
Prediction calibration tracker.

Records (predicted_probability, actual_outcome) pairs and computes
calibration metrics to measure whether your estimates are well-calibrated.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CalibrationRecord:
    predicted: float
    actual: int  # 1 = YES resolved, 0 = NO resolved


@dataclass
class CalibrationTracker:
    """Track and evaluate prediction calibration over time."""

    records: list[CalibrationRecord] = field(default_factory=list)

    def add(self, predicted: float, actual: int) -> None:
        self.records.append(CalibrationRecord(predicted=predicted, actual=actual))

    def brier_score(self) -> float:
        """
        Brier score: mean squared error of probabilistic predictions.
        Lower is better. 0 = perfect, 0.25 = coin flip baseline.
        """
        if not self.records:
            return 0.25
        return sum((r.predicted - r.actual) ** 2 for r in self.records) / len(
            self.records
        )

    def calibration_bins(self, n_bins: int = 10) -> list[dict]:
        """
        Group predictions into bins and compare predicted vs. actual rates.

        Returns list of {bin_center, avg_predicted, avg_actual, count}.
        """
        bins: dict[int, list[CalibrationRecord]] = {i: [] for i in range(n_bins)}
        for r in self.records:
            idx = min(int(r.predicted * n_bins), n_bins - 1)
            bins[idx].append(r)

        result = []
        for i in range(n_bins):
            group = bins[i]
            if not group:
                continue
            result.append(
                {
                    "bin_center": (i + 0.5) / n_bins,
                    "avg_predicted": sum(r.predicted for r in group) / len(group),
                    "avg_actual": sum(r.actual for r in group) / len(group),
                    "count": len(group),
                }
            )
        return result

    @property
    def total_predictions(self) -> int:
        return len(self.records)

    @property
    def win_rate(self) -> float:
        """Fraction of predictions where you were on the correct side."""
        if not self.records:
            return 0.0
        correct = sum(
            1
            for r in self.records
            if (r.predicted >= 0.5 and r.actual == 1)
            or (r.predicted < 0.5 and r.actual == 0)
        )
        return correct / len(self.records)
