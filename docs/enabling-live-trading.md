# Enabling Live Trading

## Jurisdiction notice

Live trading on Polymarket is **not available to US persons** per Polymarket's terms of service. By enabling live trading you confirm that you have determined live participation is legal in your jurisdiction and that you accept full responsibility for compliance with applicable laws.

## Prerequisites

- A funded Polymarket account with USDC on Polygon
- Your wallet's private key (`POLYMARKET_PRIVATE_KEY`)
- Your proxy wallet address (`POLYMARKET_WALLET_ADDRESS`)

## Steps

### 1. Set the live-trading flags in `.env`

```env
PAPER_TRADING=false
POLYMARKET_LIVE=true
```

Both flags must be set. Either flag alone is not sufficient — the executor checks both independently before placing any real order.

### 2. Add your wallet credentials

```env
POLYMARKET_PRIVATE_KEY=0x<your_private_key>
POLYMARKET_WALLET_ADDRESS=0x<your_proxy_wallet>
SIGNATURE_TYPE=0
```

`SIGNATURE_TYPE=0` is correct for most wallets. Use `1` for Gnosis Safe.

### 3. Fund your account

Deposit USDC on Polygon to your Polymarket proxy wallet. The minimum recommended balance is set by `MIN_BALANCE_USDC` (default `10`). The risk engine will halt trading if the balance drops below this threshold.

### 4. Review risk parameters

```env
MAX_TRADE_SIZE_USDC=15        # Maximum size per individual order
MAX_PORTFOLIO_EXPOSURE=0.75   # Maximum fraction of capital deployed at once
MAX_DAILY_LOSS_PCT=0.15       # Kill switch: halt if daily drawdown exceeds this
MAX_SINGLE_MARKET_PCT=0.10    # Maximum allocation to any single market
```

Tighten these before going live if you're starting with a small bankroll.

### 5. Start the system

```bash
docker compose up
```

The startup banner logs confirm your live-trading state:

```
════════════════════════════════════════════════════════════
  POLYMARKET INTELLIGENCE SYSTEM
  Paper trading:    False
  Live trading:     True
  VPN required:     False
════════════════════════════════════════════════════════════
```

## Reverting to paper mode

Set either flag back to disable live order placement immediately:

```env
PAPER_TRADING=true
# or
POLYMARKET_LIVE=false
```

A restart is required for the change to take effect.

## VPN / proxy

This repository does not bundle or require a VPN. If your jurisdiction or network requires routing traffic through a proxy, configure it at the OS or network level. You may optionally set `PROXY_URL` and `VPN_REQUIRED=true` in `.env` to enable the built-in proxy guard — see `.env.example` for details.
