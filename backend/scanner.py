#!/usr/bin/env python3
"""
Live market entropy scanner.

Pulls active markets from the Gamma API and prints the entropy
score table with all quant metrics.

Usage:
    python -m backend.scanner
    python -m backend.scanner --limit 30 --min-liquidity 50000
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from backend.data_layer.gamma_client import GammaClient
from backend.quant.entropy import market_entropy, kl_divergence, score_market


HEADER = (
    f"{'#':>3}  "
    f"{'Market Question':<60s}  "
    f"{'Mkt':>6}  "
    f"{'Mdl':>6}  "
    f"{'H(p)':>6}  "
    f"{'KL':>7}  "
    f"{'f*':>6}  "
    f"{'f/4':>6}  "
    f"{'R':>6}  "
    f"{'Size$':>7}  "
    f"{'Action':>8}  "
    f"{'Edge':>8}"
)
SEPARATOR = "─" * len(HEADER)


def simple_model_estimate(market_price: float) -> float:
    """
    Placeholder model: adds a small contrarian bias.

    In production, this would be replaced by the AI ensemble (Strategy 4).
    The bias simulates a model that slightly disagrees with the crowd,
    creating a non-zero KL divergence for demonstration.
    """
    # Contrarian nudge: push probability toward 0.5 by ~8%
    nudge = (0.5 - market_price) * 0.16
    return max(0.05, min(0.95, market_price + nudge))


async def run_scanner(limit: int = 20, min_liquidity: float = 25_000, bankroll: float = 10_000):
    client = GammaClient()

    print("\n" + "═" * 100)
    print("  POLYMARKET ENTROPY SCANNER — Live Market Intelligence")
    print("═" * 100)
    print(f"  Fetching top {limit} markets (min liquidity: ${min_liquidity:,.0f})...\n")

    try:
        markets = await client.get_active_markets(
            min_liquidity=min_liquidity, limit=limit
        )
    except Exception as e:
        print(f"  ERROR: Failed to fetch markets: {e}")
        print("  (This may be a network issue. The scanner works with live Gamma API data.)")
        # Fall back to demo data
        print("\n  Showing DEMO data with synthetic markets:\n")
        markets = None
    finally:
        await client.close()

    if not markets:
        # Demo mode with synthetic examples
        demo_markets = [
            ("demo-1", "Will Fed cut rates by 50bps at next meeting?", 0.350),
            ("demo-2", "Will BTC exceed $100k by end of Q2?", 0.420),
            ("demo-3", "Will Trump win the presidential election?", 0.550),
            ("demo-4", "Will SpaceX Starship reach orbit this month?", 0.280),
            ("demo-5", "Will the US enter a recession in 2026?", 0.180),
            ("demo-6", "Will AI pass the Turing test by 2027?", 0.150),
            ("demo-7", "Will Ethereum flip Bitcoin market cap?", 0.080),
            ("demo-8", "Will there be a ceasefire in Ukraine by June?", 0.320),
            ("demo-9", "Will Apple release AR glasses in 2026?", 0.250),
            ("demo-10", "Will the S&P 500 close above 6000 this year?", 0.620),
            ("demo-11", "Will a Category 5 hurricane hit the US this season?", 0.310),
            ("demo-12", "Will the next Supreme Court justice be liberal?", 0.440),
            ("demo-13", "Will Netflix stock exceed $1000?", 0.190),
            ("demo-14", "Will China invade Taiwan by 2027?", 0.070),
            ("demo-15", "Will the UK rejoin the EU single market?", 0.120),
            ("demo-16", "Will Dogecoin reach $1?", 0.060),
            ("demo-17", "Will the global population reach 9 billion by 2030?", 0.750),
            ("demo-18", "Will a lab-grown meat product outsell beef?", 0.090),
            ("demo-19", "Will commercial fusion power be achieved by 2030?", 0.110),
            ("demo-20", "Will the US adopt a CBDC by 2028?", 0.200),
        ]

        print(HEADER)
        print(SEPARATOR)

        scored_markets = []
        for i, (mid, question, price) in enumerate(demo_markets):
            model_p = simple_model_estimate(price)
            scored = score_market(
                market_id=mid,
                question=question,
                market_price=price,
                model_probability=model_p,
                bankroll=bankroll,
            )
            scored_markets.append(scored)

        # Sort by KL divergence
        scored_markets.sort(key=lambda s: s.kl_div_bits, reverse=True)

        for i, s in enumerate(scored_markets):
            print(f"{i+1:>3}  {s}")

        print(SEPARATOR)
        actionable = [s for s in scored_markets if s.recommended_action.value != "HOLD"]
        print(f"\n  Total markets scanned: {len(scored_markets)}")
        print(f"  Actionable signals:    {len(actionable)}")
        if actionable:
            total_size = sum(s.position_size_usdc for s in actionable)
            print(f"  Total suggested size:  ${total_size:.2f}")
            print(f"  Bankroll:              ${bankroll:,.2f}")
        return

    # Live mode
    print(HEADER)
    print(SEPARATOR)

    scored_markets = []
    for m in markets:
        model_p = simple_model_estimate(m.yes_price)
        scored = score_market(
            market_id=m.id,
            question=m.question,
            market_price=m.yes_price,
            model_probability=model_p,
            bankroll=bankroll,
        )
        scored_markets.append(scored)

    scored_markets.sort(key=lambda s: s.kl_div_bits, reverse=True)

    for i, s in enumerate(scored_markets):
        print(f"{i+1:>3}  {s}")

    print(SEPARATOR)
    actionable = [s for s in scored_markets if s.recommended_action.value != "HOLD"]
    print(f"\n  Total markets scanned: {len(scored_markets)}")
    print(f"  Actionable signals:    {len(actionable)}")
    if actionable:
        total_size = sum(s.position_size_usdc for s in actionable)
        print(f"  Total suggested size:  ${total_size:.2f}")
        print(f"  Bankroll:              ${bankroll:,.2f}")


def main():
    parser = argparse.ArgumentParser(description="Polymarket Entropy Scanner")
    parser.add_argument("--limit", type=int, default=20, help="Number of markets to scan")
    parser.add_argument("--min-liquidity", type=float, default=25000, help="Minimum liquidity filter")
    parser.add_argument("--bankroll", type=float, default=10000, help="Bankroll for Kelly sizing")
    args = parser.parse_args()

    asyncio.run(run_scanner(
        limit=args.limit,
        min_liquidity=args.min_liquidity,
        bankroll=args.bankroll,
    ))


if __name__ == "__main__":
    main()
