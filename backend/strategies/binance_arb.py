"""
Binance vs Polymarket crypto price arbitrage.

Polymarket hosts many binary markets of the form "Will BTC reach $X
by Y?" / "Will ETH hit $Z by Q2?". The underlying asset trades on
Binance in real-time. When Polymarket's implied probability lags the
Binance spot price, there's a tradeable gap with NO AI cost and a
closed-form fair value.

Fair-value model (v1, deliberately conservative):
  - Parse target price + direction from the question text
  - Look up current Binance spot for the asset
  - If spot is already past the target in the bet's direction with
    enough time left, the event is near-certain → fair_probability ≈ 0.95
  - If spot is far from the target with short time remaining, the event
    is near-impossible → fair_probability ≈ 0.05
  - In between, use a log-normal drift model: the probability that S_T
    exceeds K given current spot S_0 and daily realized vol σ,
    assuming zero drift:
        P(S_T > K) = Φ( (ln(S_0/K)) / (σ·√T) )

All sizing goes through the shared EV gate + Kelly helpers. All
intents are rejected if the edge doesn't beat fees + half-spread +
slippage. No mock data anywhere.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Optional

from backend.data_layer.binance_client import ASSET_TO_SYMBOL, get_binance_client, BinanceTicker
from backend.quant.regime import classify as classify_regime
from backend.quant.sizing import ev_gate_passes, kelly_size_usdc, regime_allows_strategy
from backend.strategies.base import (
    MarketState,
    OrderIntent,
    OrderType,
    Side,
    Strategy,
    StrategyName,
)

logger = logging.getLogger(__name__)


# Regex for detecting crypto-price questions.
# Captures: (asset_alias)(above/below/over/under/hit/reach)(price)
# Accepts "BTC > $100k", "will Bitcoin reach $150,000", "ETH above $5k" etc.
_PRICE_PATTERN = re.compile(
    r"(?ix)"                                    # case-insensitive, verbose
    r"(?P<asset>bitcoin|btc|ethereum|ether|eth|solana|sol|ripple|xrp|"
    r"dogecoin|doge|cardano|ada|polkadot|dot|chainlink|link|"
    r"avalanche|avax|polygon|matic|binance\s+coin|bnb)\b"
    r".{0,60}?"
    r"(?P<dir>above|over|reach|hit|exceed|greater\s+than|"
    r"below|under|less\s+than)"
    r".{0,20}?"
    r"\$?(?P<target>\d[\d,]*\.?\d*)\s*"
    r"(?P<unit>k|K|m|M|thousand|million)?"
)


@dataclass
class ParsedCryptoMarket:
    asset: str         # normalized lowercase alias, e.g. "btc"
    symbol: str        # Binance pair, e.g. "BTCUSDT"
    target_price: float
    direction: str     # "above" or "below"


def parse_crypto_market(question: str) -> Optional[ParsedCryptoMarket]:
    """Extract crypto-price target info from a Polymarket question string."""
    if not question:
        return None
    m = _PRICE_PATTERN.search(question)
    if not m:
        return None
    asset_raw = (m.group("asset") or "").lower().replace("  ", " ").strip()
    symbol = ASSET_TO_SYMBOL.get(asset_raw)
    if not symbol:
        return None

    # Parse target number
    target_str = (m.group("target") or "").replace(",", "")
    try:
        target = float(target_str)
    except ValueError:
        return None

    # Apply unit suffix (k, m)
    unit = (m.group("unit") or "").lower()
    if unit in ("k", "thousand"):
        target *= 1_000
    elif unit in ("m", "million"):
        target *= 1_000_000

    if target <= 0:
        return None

    direction_word = (m.group("dir") or "").lower()
    if any(w in direction_word for w in ("below", "under", "less")):
        direction = "below"
    else:
        direction = "above"

    return ParsedCryptoMarket(
        asset=asset_raw,
        symbol=symbol,
        target_price=target,
        direction=direction,
    )


def fair_probability(
    ticker: BinanceTicker,
    target: float,
    direction: str,
    hours_to_close: float,
) -> Optional[float]:
    """
    Compute the fair probability that the binary event resolves YES,
    given the current spot, target, direction, and time remaining.

    Uses a log-normal random-walk model with the Binance 24h-range-based
    realized vol. Zero drift assumption (risk-neutral pricing without
    a cost-of-carry term — conservative for short horizons).

    Returns None if we can't price it (e.g. target <= 0, time = 0).
    """
    if ticker is None or ticker.price <= 0 or target <= 0:
        return None
    if hours_to_close <= 0:
        # Event resolved or resolving now — return near-certainty in the
        # direction spot already confirms.
        if direction == "above":
            return 0.995 if ticker.price >= target else 0.005
        else:
            return 0.995 if ticker.price <= target else 0.005

    days = hours_to_close / 24.0
    sigma_daily = ticker.realized_vol_24h
    if sigma_daily <= 0:
        # Fallback: assume a very small daily move ≈ 2%
        sigma_daily = 0.02
    sigma_T = sigma_daily * math.sqrt(days)

    # Cap sigma to avoid numerical issues on very long horizons
    sigma_T = min(sigma_T, 2.0)

    if sigma_T <= 0:
        return None

    # P(S_T > K) under zero-drift log-normal = Φ((ln(S_0/K)) / σ_T)
    # where Φ is the standard normal CDF.
    try:
        z = math.log(ticker.price / target) / sigma_T
    except (ValueError, ZeroDivisionError):
        return None

    from statistics import NormalDist
    cdf_above = NormalDist().cdf(z)
    if direction == "above":
        p = cdf_above
    else:
        p = 1.0 - cdf_above

    # Clamp to realistic bounds — never claim certainty
    return max(0.01, min(0.99, p))


class BinanceArb(Strategy):
    """Polymarket ↔ Binance crypto price arbitrage."""

    name = StrategyName.BINANCE_ARB

    def __init__(
        self,
        min_edge: float = 0.05,
        min_liquidity: float = 2000,
        bankroll: float = 300.0,
        kelly_fraction_mult: float = 0.25,
        max_trade_usdc: float = 4.0,
    ) -> None:
        self.min_edge = min_edge
        self.min_liquidity = min_liquidity
        self.bankroll = bankroll
        self.kelly_fraction_mult = kelly_fraction_mult
        self.max_trade_usdc = max_trade_usdc
        self._client = get_binance_client()

    async def evaluate_batch(self, markets: list[MarketState]) -> list[OrderIntent]:
        """Scan all markets for crypto-price arbs in one pass."""
        # Pre-parse every market so we can fetch Binance tickers only
        # for the symbols we actually need.
        candidates: list[tuple[MarketState, ParsedCryptoMarket]] = []
        for m in markets:
            if m.liquidity < self.min_liquidity:
                continue
            if m.yes_price < 0.02 or m.yes_price > 0.98:
                continue
            if m.hours_to_close <= 0 or m.hours_to_close > 24 * 90:
                continue  # skip expired or >90 days (tails too fat)
            parsed = parse_crypto_market(m.question)
            if parsed is None:
                continue
            candidates.append((m, parsed))

        if not candidates:
            logger.debug("Binance arb: no crypto-price candidates this cycle")
            return []

        # One batch fetch for all needed Binance symbols
        needed_symbols = list({p.symbol for _, p in candidates})
        tickers = await self._client.get_all_tickers(symbols=needed_symbols)
        if not tickers:
            logger.warning("Binance arb: no ticker data available")
            return []

        intents: list[OrderIntent] = []
        rej_no_ticker = 0
        rej_no_fair = 0
        rej_edge = 0
        rej_regime = 0
        rej_ev = 0

        for market, parsed in candidates:
            ticker = tickers.get(parsed.symbol)
            if ticker is None:
                rej_no_ticker += 1
                continue

            fair = fair_probability(
                ticker=ticker,
                target=parsed.target_price,
                direction=parsed.direction,
                hours_to_close=market.hours_to_close,
            )
            if fair is None:
                rej_no_fair += 1
                continue

            edge = abs(fair - market.yes_price)
            if edge < self.min_edge:
                rej_edge += 1
                continue

            # Regime gate
            regime_call = classify_regime(market)
            if not regime_allows_strategy(regime_call.regime, self.name):
                rej_regime += 1
                continue

            # Side + market price for the token we're buying
            if fair > market.yes_price:
                side = Side.YES
                price = market.yes_price
                fair_for_side = fair
                mkt_for_side = market.yes_price
            else:
                side = Side.NO
                price = market.no_price
                fair_for_side = 1.0 - fair
                mkt_for_side = market.no_price

            # EV gate (fees + spread + slippage)
            if not ev_gate_passes(
                fair_probability=fair_for_side,
                market_price=mkt_for_side,
                spread=market.spread,
            ):
                rej_ev += 1
                continue

            # Kelly sizing
            size = kelly_size_usdc(
                fair_probability=fair_for_side,
                market_price=mkt_for_side,
                bankroll=self.bankroll,
                kelly_fraction_multiplier=self.kelly_fraction_mult,
                max_trade_usdc=self.max_trade_usdc,
            )
            if size <= 0:
                continue

            intent = OrderIntent(
                strategy=self.name,
                market_id=market.market_id,
                condition_id=market.condition_id,
                question=market.question,
                side=side,
                order_type=OrderType.LIMIT,
                price=price,
                size_usdc=size,
                confidence=min(0.95, edge * 5),
                reason=(
                    f"BinanceArb: {parsed.asset.upper()} spot={ticker.price:,.2f} "
                    f"target=${parsed.target_price:,.2f} {parsed.direction} "
                    f"→ fair={fair:.3f} vs mkt={market.yes_price:.3f} "
                    f"edge={edge:.3f} ({market.hours_to_close:.0f}h left)"
                ),
                kelly_fraction=size / self.bankroll if self.bankroll > 0 else 0,
            )
            intents.append(intent)

        logger.info(
            f"Binance arb cycle: {len(candidates)} crypto markets → "
            f"{len(intents)} intents (rej: no_ticker={rej_no_ticker} "
            f"no_fair={rej_no_fair} edge={rej_edge} regime={rej_regime} ev={rej_ev})"
        )
        return intents
