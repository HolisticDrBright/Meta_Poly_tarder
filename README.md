# Meta Poly Tarder

Quantitative prediction-market trading engine for Polymarket. Runs multi-strategy signal fusion (entropy, Avellaneda-Stoikov market making, arb, ensemble AI, jet-tracker, copy trading) with a real-time dashboard.

## Compliance & jurisdiction

This repository defaults to paper-trading mode for research purposes. Live trading on Polymarket is not available to US persons per Polymarket's terms of service. Users are responsible for determining whether live trading is legal in their jurisdiction. This repository does not bundle, require, or assist with VPN configuration or geographic routing.

## Quick start

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY and POLYMARKET_* credentials
docker compose up
```

The app starts in paper-trading mode. No VPN or proxy is required.

## Architecture

```
backend/
  config.py              — typed settings from .env
  main.py                — FastAPI app + WebSocket streaming
  scheduler.py           — strategy loop (runs every 15s)
  strategies/            — entropy, A-S, arb, ensemble, jet, copy, theta
  aggregator/            — signal fusion + Kelly sizing
  execution/             — paper ledger + live CLOB client
  learning/              — Brier score, hit rate, weight evolution
  data_layer/            — Gamma API, Data API, Binance, CLOB WebSocket
  agents/                — resolution-rule classifiers
  observability/         — logging, Telegram alerts, optional VPN guard
frontend/
  Next.js dashboard      — 13 panels, WebSocket live feed
prediction_intelligence/ — specialist layer (news, on-chain, swarm)
```

## Configuration

See `.env.example` for all available options. Key flags:

| Variable | Default | Description |
|---|---|---|
| `PAPER_TRADING` | `true` | Routes fills to the paper ledger instead of live CLOB |
| `POLYMARKET_LIVE` | `false` | Secondary hard gate for live order placement |
| `VPN_REQUIRED` | `false` | Optional — configure VPN at OS level if desired |

To enable live trading, see [docs/enabling-live-trading.md](docs/enabling-live-trading.md).

## Strategies

| Strategy | Description |
|---|---|
| Entropy | Trades markets where price deviates from fair-value entropy estimate |
| Avellaneda-Stoikov | Market-making quotes around the mid; captures bid/ask spread |
| Arb | Cross-market arbitrage on correlated outcomes |
| Binance Arb | Detects Polymarket crypto markets lagging Binance spot price |
| Ensemble | Multi-model AI probability fusion (Claude + GPT-4o) |
| Jet Tracker | Trades political outcome markets on private jet movement signals |
| Copy | Follows top leaderboard wallets with configurable ratio |
| Theta | Time-decay plays on markets approaching resolution |

## Running tests

```bash
pip install -r requirements.txt
pytest tests/
```
