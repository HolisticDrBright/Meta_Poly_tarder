"""
Closed-loop weight learning.

Reads graded outcomes from prediction_intelligence.db, computes
per-strategy / per-specialist / per-model performance, and writes
updated weights to data/active_weights.json. The running aggregator,
specialist orchestrator, and ensemble AI then READ that file each
cycle and use the learned weights in place of their hardcoded defaults.

This is what actually closes the loop — without this module the
analyzer runs, writes reports, but no running code changes behavior.

Design rules:
  1. Only update a weight if the strategy has ≥ MIN_OUTCOMES_PER_KEY
     scored outcomes (default 20). Small samples stay on defaults.
  2. Smooth evolution: clamp each cycle's weight change to
     MAX_CHANGE_PER_CYCLE (default 0.08) of the previous value.
  3. Bounded: every weight clamped to [MIN_WEIGHT, MAX_WEIGHT]
     (default [0.02, 0.50]) so no signal can be driven to zero or
     dominate the fusion.
  4. Never raises — all errors caught and logged.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


WEIGHTS_FILE = Path("data/active_weights.json")
MIN_OUTCOMES_PER_KEY = 20
MAX_CHANGE_PER_CYCLE = 0.08
MIN_WEIGHT = 0.02
MAX_WEIGHT = 0.50


# ── Defaults — used until the loop has enough data to learn ────────

DEFAULT_STRATEGY_WEIGHTS: dict[str, float] = {
    "entropy": 0.25,
    "binance_arb": 0.20,
    "arb": 0.15,
    "ensemble_ai": 0.15,
    "theta": 0.10,
    "jet": 0.05,
    "copy": 0.05,
    "avellaneda": 0.05,
}

DEFAULT_SPECIALIST_WEIGHTS: dict[str, float] = {
    "news": 0.20,
    "history": 0.15,
    "onchain": 0.10,
    "mirofish": 0.05,
}

DEFAULT_MODEL_WEIGHTS: dict[str, float] = {
    "claude": 0.55,
    "gpt4": 0.45,
}


# ── File cache (mtime-invalidated) ────────────────────────────────

_cache: dict[str, Any] = {}
_cache_mtime: float = 0.0


def _read_file() -> dict:
    """Read active_weights.json with mtime-based caching."""
    global _cache, _cache_mtime
    try:
        if not WEIGHTS_FILE.exists():
            return {}
        mtime = WEIGHTS_FILE.stat().st_mtime
        if _cache and mtime == _cache_mtime:
            return _cache
        data = json.loads(WEIGHTS_FILE.read_text())
        _cache = data if isinstance(data, dict) else {}
        _cache_mtime = mtime
        return _cache
    except Exception as e:
        logger.debug(f"learning.weights._read_file failed: {e}")
        return {}


def _write_file_atomic(data: dict) -> bool:
    """Write the full weights file atomically. Never raises."""
    try:
        WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = WEIGHTS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, WEIGHTS_FILE)
        # Invalidate in-process cache so next read picks up the new file
        global _cache, _cache_mtime
        _cache = {}
        _cache_mtime = 0.0
        return True
    except Exception as e:
        logger.warning(f"learning.weights._write_file_atomic failed: {e}")
        return False


# ── Public read API — called by the running strategies ────────────

def get_strategy_weights() -> dict[str, float]:
    """Returns per-strategy weights for the SignalAggregator.

    Starts from DEFAULT_STRATEGY_WEIGHTS, overlays any learned weights
    from active_weights.json['strategy_weights'], returns the merged
    dict. Defaults are guaranteed to exist even if the file is missing.
    """
    data = _read_file()
    learned = data.get("strategy_weights") or {}
    merged = dict(DEFAULT_STRATEGY_WEIGHTS)
    for k, v in learned.items():
        try:
            n = float(v)
            if MIN_WEIGHT <= n <= MAX_WEIGHT:
                merged[k] = n
        except (TypeError, ValueError):
            continue
    return merged


def get_specialist_weights() -> dict[str, float]:
    """Returns per-specialist fusion weights for the orchestrator."""
    data = _read_file()
    learned = data.get("specialist_weights") or {}
    merged = dict(DEFAULT_SPECIALIST_WEIGHTS)
    for k, v in learned.items():
        try:
            n = float(v)
            if MIN_WEIGHT <= n <= MAX_WEIGHT:
                merged[k] = n
        except (TypeError, ValueError):
            continue
    return merged


def get_model_weights() -> dict[str, float]:
    """Returns per-outer-model weights (Claude vs GPT-4o) for the ensemble."""
    data = _read_file()
    learned = data.get("model_weights") or {}
    merged = dict(DEFAULT_MODEL_WEIGHTS)
    for k, v in learned.items():
        try:
            n = float(v)
            if MIN_WEIGHT <= n <= MAX_WEIGHT:
                merged[k] = n
        except (TypeError, ValueError):
            continue
    return merged


# ── Learning pass — computes new weights from graded outcomes ─────

def _softmax(vals: dict[str, float], temperature: float = 0.5) -> dict[str, float]:
    """Convert raw scores to a normalized weight distribution.

    Lower temperature = sharper (winner takes more).
    Higher temperature = flatter (more evenly distributed).
    """
    if not vals:
        return {}
    import math
    # Scale scores by 1/temperature then exp
    max_v = max(vals.values())
    exp_vals = {k: math.exp((v - max_v) / max(temperature, 1e-6)) for k, v in vals.items()}
    total = sum(exp_vals.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in exp_vals.items()}


def _clamp_evolution(
    current: dict[str, float],
    new: dict[str, float],
    max_change: float = MAX_CHANGE_PER_CYCLE,
) -> dict[str, float]:
    """Limit how much any single weight can move per cycle.

    Prevents a bad day of outcomes from obliterating a strategy's
    weight in one step. Target weights are mixed in at (max_change)
    each cycle.
    """
    result: dict[str, float] = {}
    keys = set(current.keys()) | set(new.keys())
    for k in keys:
        cur = current.get(k, MIN_WEIGHT)
        tgt = new.get(k, cur)
        delta = tgt - cur
        capped = max(-max_change, min(max_change, delta))
        result[k] = max(MIN_WEIGHT, min(MAX_WEIGHT, cur + capped))
    # Re-normalize to sum to 1
    total = sum(result.values())
    if total > 0:
        result = {k: v / total for k, v in result.items()}
    return result


def compute_strategy_weights(decision_logger) -> Optional[dict[str, float]]:
    """Compute new per-strategy weights from graded outcomes.

    Queries decision_log JOIN outcome_log grouped by the `strategy`
    field that scheduler._log_decision stores in signal_weights.
    Returns a merged weight dict, or None if there isn't enough data.
    """
    if decision_logger is None:
        return None
    try:
        conn = decision_logger._ensure_conn()
        # signal_weights is a JSON string containing {"strategy": "entropy", ...}
        # DuckDB can extract a JSON field via json_extract_string
        rows = conn.execute("""
            SELECT
                json_extract_string(d.signal_weights, '$.strategy') as strategy,
                COUNT(*) as n,
                AVG(o.brier_score) as avg_brier,
                AVG(o.paper_pnl) as avg_pnl,
                SUM(CASE WHEN o.paper_pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as hit_rate
            FROM decision_log d
            JOIN outcome_log o ON d.decision_id = o.decision_id
            WHERE json_extract_string(d.signal_weights, '$.strategy') IS NOT NULL
            GROUP BY strategy
            HAVING COUNT(*) >= ?
        """, [MIN_OUTCOMES_PER_KEY]).fetchall()

        if not rows:
            logger.debug(
                f"learning.weights: no strategies yet have {MIN_OUTCOMES_PER_KEY}+ graded outcomes"
            )
            return None

        # Score each strategy by a blend of hit rate and (neg) Brier score
        # Higher score = better. Clamp to reasonable range.
        scores: dict[str, float] = {}
        summary_parts = []
        for row in rows:
            strat, n, avg_brier, avg_pnl, hit_rate = row
            if not strat:
                continue
            brier = float(avg_brier or 0.25)
            hit = float(hit_rate or 0.5)
            pnl = float(avg_pnl or 0.0)
            # Score: reward accuracy (low brier) + hit rate + positive pnl
            # Scale pnl to a 0-1 band by clipping at ±$2 per trade
            pnl_norm = max(-1.0, min(1.0, pnl / 2.0))
            score = (0.25 - brier) * 4.0 + (hit - 0.5) * 2.0 + pnl_norm
            scores[strat] = score
            summary_parts.append(
                f"{strat}(n={n},brier={brier:.3f},hit={hit:.2f},pnl=${pnl:+.2f}→score={score:+.2f})"
            )

        if not scores:
            return None

        logger.info(f"learning.weights: strategy scores — {', '.join(summary_parts)}")

        # Convert scores to a target weight distribution via softmax
        target = _softmax(scores, temperature=0.5)
        # Merge with defaults for strategies that don't have enough data yet
        merged_target = dict(DEFAULT_STRATEGY_WEIGHTS)
        merged_target.update(target)

        # Clamp evolution so a single bad cycle can't obliterate anything
        current = get_strategy_weights()
        smoothed = _clamp_evolution(current, merged_target)

        return smoothed
    except Exception as e:
        logger.warning(f"learning.weights.compute_strategy_weights failed: {e}")
        return None


def run_learning_pass(decision_logger) -> dict[str, Any]:
    """Run a full learning pass: compute new weights, write them, log result.

    Returns a summary dict for logging / the API response.
    Never raises.
    """
    result: dict[str, Any] = {
        "updated": False,
        "reason": "",
        "strategy_weights": None,
    }
    try:
        new_weights = compute_strategy_weights(decision_logger)
        if new_weights is None:
            result["reason"] = "not enough graded outcomes per strategy yet"
            return result

        current_file = _read_file()
        existing = current_file.get("strategy_weights") or {}

        # Only write if weights meaningfully changed
        meaningful_change = False
        for k, v in new_weights.items():
            if abs(v - existing.get(k, DEFAULT_STRATEGY_WEIGHTS.get(k, 0.05))) > 0.005:
                meaningful_change = True
                break

        if not meaningful_change:
            result["reason"] = "no meaningful change since last update"
            result["strategy_weights"] = new_weights
            return result

        # Preserve other sections of the file (specialist_weights, model_weights, etc)
        new_file = dict(current_file)
        new_file["strategy_weights"] = new_weights
        new_file["strategy_weights_updated_at"] = time.time()
        if _write_file_atomic(new_file):
            result["updated"] = True
            result["strategy_weights"] = new_weights
            result["reason"] = "written to data/active_weights.json"
            deltas = {
                k: round(new_weights[k] - existing.get(k, DEFAULT_STRATEGY_WEIGHTS.get(k, 0.05)), 4)
                for k in new_weights
            }
            top_changes = sorted(deltas.items(), key=lambda x: -abs(x[1]))[:5]
            logger.info(
                "learning.weights: DEPLOYED new strategy weights — "
                f"top changes: {', '.join(f'{k}{v:+.3f}' for k, v in top_changes)}"
            )
        else:
            result["reason"] = "file write failed"
    except Exception as e:
        logger.warning(f"learning.weights.run_learning_pass failed: {e}")
        result["reason"] = f"error: {e}"
    return result
