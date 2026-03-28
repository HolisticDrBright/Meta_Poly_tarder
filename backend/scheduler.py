"""
Background task scheduler — orchestrates all strategies on intervals.

Uses APScheduler to run:
  - Market data refresh (every 45s)
  - Entropy screening (every 60s)
  - Arb scanning (every 15s for crypto markets)
  - Whale tracking (every 30s)
  - Jet tracking (every 60s)
  - A-S market maker quotes (every 10s)
  - Theta harvester (every 5 min)
  - Signal aggregation + execution (every 30s)
  - Daily risk reset (midnight UTC)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from backend.config import settings
from backend.data_layer.gamma_client import GammaClient
from backend.data_layer.adsb_client import ADSBClient
from backend.data_layer.data_api_client import DataAPIClient
from backend.data_layer.storage import DuckDBStorage, SQLiteState
from backend.strategies.base import MarketState, OrderIntent, Side
from backend.strategies.entropy_screener import EntropyScreener
from backend.strategies.avellaneda_stoikov import AvellanedaStoikovMM
from backend.strategies.arb_scanner import ArbScanner
from backend.strategies.theta_harvester import ThetaHarvester
from backend.strategies.copy_trader import CopyTrader, CopyTarget, CopyTradeEvent
from backend.strategies.jet_signal import JetSignalStrategy
from backend.aggregator.signal_aggregator import SignalAggregator
from backend.risk.engine import RiskEngine
from backend.execution.executor import OrderExecutor
from backend.observability.alerts import TelegramAlert
from backend.quant.entropy import market_entropy

logger = logging.getLogger(__name__)


class TradingScheduler:
    """Orchestrates all strategies on scheduled intervals."""

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler()
        self.gamma = GammaClient()
        self.adsb = ADSBClient(
            opensky_user=settings.ai.anthropic_api_key and "",
            opensky_pass="",
        )
        self.data_api = DataAPIClient()

        # Strategies
        self.entropy = EntropyScreener(
            entropy_threshold=settings.quant.entropy_threshold,
            efficiency_max=settings.quant.entropy_efficiency_max,
            kelly_fraction=settings.quant.kelly_fraction,
            bankroll=10000,
            max_trade_usdc=settings.risk.max_trade_size_usdc,
        )
        self.avellaneda = AvellanedaStoikovMM(
            gamma=settings.quant.as_gamma,
            kappa=settings.quant.as_kappa,
            session_hours=settings.quant.as_session_hours,
            vpin_threshold=settings.quant.vpin_pause_threshold,
        )
        self.arb = ArbScanner(min_arb_edge=settings.quant.min_arb_edge)
        self.theta = ThetaHarvester()
        self.copy = CopyTrader(
            default_ratio=settings.copy.ratio,
            max_size_usdc=settings.copy.max_size_usdc,
            confluence_required=settings.copy.confluence_required,
        )
        self.jet = JetSignalStrategy(adsb_client=self.adsb)

        # Infrastructure
        self.aggregator = SignalAggregator()
        self.risk = RiskEngine(
            max_portfolio_exposure=settings.risk.max_portfolio_exposure,
            max_single_market_pct=settings.risk.max_single_market_pct,
            max_daily_loss_pct=settings.risk.max_daily_loss_pct,
            max_trade_size_usdc=settings.risk.max_trade_size_usdc,
            min_balance_usdc=settings.risk.min_balance_usdc,
            paper_trading=settings.trading.paper_trading,
        )
        self.executor = OrderExecutor(
            paper_trading=settings.trading.paper_trading,
            private_key=settings.trading.private_key,
            wallet_address=settings.trading.wallet_address,
            signature_type=settings.trading.signature_type,
        )
        self.telegram = TelegramAlert(
            bot_token=settings.alerts.telegram_bot_token,
            chat_id=settings.alerts.telegram_chat_id,
        )
        self.duckdb = DuckDBStorage()
        self.sqlite = SQLiteState()

        # Shared state (accessible by API routes)
        from backend.state import system_state
        self.state = system_state
        self.state.paper_trading = settings.trading.paper_trading

        # Local accumulator
        self._all_intents: list[OrderIntent] = []

    def _gamma_to_market_state(self, gm) -> MarketState:
        """Convert GammaMarket to MarketState."""
        return MarketState(
            market_id=gm.id,
            condition_id=gm.condition_id,
            question=gm.question,
            category=gm.category,
            yes_price=gm.yes_price,
            no_price=gm.no_price,
            mid_price=(gm.yes_price + gm.no_price) / 2,
            spread=gm.spread,
            best_bid=gm.best_bid,
            best_ask=gm.best_ask,
            bid_depth=0,
            ask_depth=0,
            liquidity=gm.liquidity,
            volume_24h=gm.volume_24h,
            end_date=gm.end_date,
            active=gm.active,
            entropy_bits=market_entropy(gm.yes_price),
        )

    async def refresh_markets(self) -> None:
        """Fetch latest market data from Gamma API."""
        try:
            gamma_markets = await self.gamma.get_active_markets(min_liquidity=10000, limit=100)
            markets = [self._gamma_to_market_state(gm) for gm in gamma_markets]
            self.state.update_markets(markets)
            logger.debug(f"Refreshed {len(markets)} markets")

            # Broadcast price updates
            for m in markets[:20]:
                await self.state.broadcast("price_update", {
                    "market_id": m.market_id,
                    "yes_price": m.yes_price,
                    "no_price": m.no_price,
                })
        except Exception as e:
            logger.error(f"Market refresh failed: {e}")

    async def run_entropy_screener(self) -> None:
        """Run entropy screener on all markets."""
        if not settings.strategies.entropy or not self.state.markets:
            return
        try:
            intents = await self.entropy.evaluate_batch(self.state.markets)
            self._all_intents.extend(intents)
            for intent in intents:
                self.state.add_signal(intent)
            if intents:
                logger.info(f"Entropy screener: {len(intents)} signals")
                for intent in intents[:3]:
                    await self.telegram.signal_alert(
                        "entropy", intent.question, intent.reason
                    )
                    await self.state.broadcast("signal", {
                        "strategy": "entropy", "market_id": intent.market_id,
                        "side": intent.side.value, "confidence": intent.confidence,
                    })
        except Exception as e:
            logger.error(f"Entropy screener failed: {e}")

    async def run_arb_scanner(self) -> None:
        """Scan for YES+NO arbitrage opportunities."""
        if not settings.strategies.arb or not self.state.markets:
            return
        try:
            intents = await self.arb.evaluate_batch(self.state.markets)
            self._all_intents.extend(intents)
            for intent in intents:
                self.state.add_signal(intent)
            if intents:
                logger.info(f"Arb scanner: {len(intents)} opportunities")
                for intent in intents[:2]:
                    await self.telegram.signal_alert(
                        "arb", intent.question, intent.reason
                    )
        except Exception as e:
            logger.error(f"Arb scanner failed: {e}")

    async def run_avellaneda_mm(self) -> None:
        """Update A-S market maker quotes."""
        if not settings.strategies.avellaneda or not self.state.markets:
            return
        try:
            intents = await self.avellaneda.evaluate_batch(self.state.markets)
            self._all_intents.extend(intents)
        except Exception as e:
            logger.error(f"A-S MM failed: {e}")

    async def run_theta_harvester(self) -> None:
        """Run theta decay harvester."""
        if not settings.strategies.theta or not self.state.markets:
            return
        try:
            intents = await self.theta.evaluate_batch(self.state.markets)
            self._all_intents.extend(intents)
            if intents:
                logger.info(f"Theta harvester: {len(intents)} signals")
        except Exception as e:
            logger.error(f"Theta harvester failed: {e}")

    async def run_jet_tracker(self) -> None:
        """Poll ADS-B data and check for jet signals."""
        if not settings.strategies.jet:
            return
        # Jet tracking requires ADS-B credentials configured
        logger.debug("Jet tracker poll (requires ADS-B credentials)")

    async def aggregate_and_execute(self) -> None:
        """Score all intents, run risk checks, and execute approved trades."""
        if not self._all_intents:
            return

        try:
            # Score and rank
            scored = self.aggregator.score(self._all_intents)

            # Risk check
            approved = self.risk.check_batch(scored)

            if approved:
                logger.info(f"Executing {len(approved)} approved trades")
                results = await self.executor.execute_batch(approved)

                for si, result in zip(approved, results):
                    if result.success:
                        self.risk.record_trade(si.intent)
                        # Store in DuckDB
                        self.duckdb.insert_trade(
                            market_id=si.intent.market_id,
                            side=si.intent.side.value,
                            price=result.fill_price,
                            size_usdc=result.fill_size,
                            strategy=si.intent.strategy.value,
                            paper=result.paper,
                        )
                        # Telegram alert
                        await self.telegram.trade_alert(
                            strategy=si.intent.strategy.value,
                            side=si.intent.side.value,
                            market=si.intent.question,
                            size=result.fill_size,
                            price=result.fill_price,
                        )

            # Clear intents for next cycle
            self._all_intents.clear()
        except Exception as e:
            logger.error(f"Aggregate/execute failed: {e}")
            self._all_intents.clear()

    async def daily_reset(self) -> None:
        """Reset daily risk counters at midnight UTC."""
        self.risk.reset_daily()
        logger.info("Daily risk counters reset")
        await self.telegram.send("<b>DAILY RESET</b> — Risk counters cleared")

    def start(self) -> None:
        """Register all jobs and start the scheduler."""
        self.duckdb.connect()
        self.sqlite.connect()

        # Market data refresh
        self.scheduler.add_job(
            self.refresh_markets,
            IntervalTrigger(seconds=45),
            id="refresh_markets",
            name="Market data refresh",
        )

        # Strategy jobs
        self.scheduler.add_job(
            self.run_entropy_screener,
            IntervalTrigger(seconds=60),
            id="entropy_screener",
            name="Entropy screener",
        )
        self.scheduler.add_job(
            self.run_arb_scanner,
            IntervalTrigger(seconds=15),
            id="arb_scanner",
            name="Arb scanner",
        )
        self.scheduler.add_job(
            self.run_avellaneda_mm,
            IntervalTrigger(seconds=10),
            id="avellaneda_mm",
            name="A-S market maker",
        )
        self.scheduler.add_job(
            self.run_theta_harvester,
            IntervalTrigger(minutes=5),
            id="theta_harvester",
            name="Theta harvester",
        )
        self.scheduler.add_job(
            self.run_jet_tracker,
            IntervalTrigger(seconds=60),
            id="jet_tracker",
            name="Jet tracker",
        )

        # Aggregation + execution
        self.scheduler.add_job(
            self.aggregate_and_execute,
            IntervalTrigger(seconds=30),
            id="aggregate_execute",
            name="Signal aggregation + execution",
        )

        # Daily reset
        self.scheduler.add_job(
            self.daily_reset,
            CronTrigger(hour=0, minute=0, timezone="UTC"),
            id="daily_reset",
            name="Daily risk reset",
        )

        self.scheduler.start()
        mode = "PAPER" if settings.trading.paper_trading else "LIVE"
        logger.info(f"Trading scheduler started [{mode} MODE]")
        logger.info(
            f"Active strategies: "
            f"entropy={settings.strategies.entropy}, "
            f"avellaneda={settings.strategies.avellaneda}, "
            f"arb={settings.strategies.arb}, "
            f"theta={settings.strategies.theta}, "
            f"jet={settings.strategies.jet}, "
            f"copy={settings.strategies.copy}"
        )

    async def stop(self) -> None:
        """Graceful shutdown."""
        self.scheduler.shutdown(wait=True)
        if not self.executor.paper_trading:
            await self.executor.cancel_all_live()
        await self.gamma.close()
        await self.adsb.close()
        await self.data_api.close()
        await self.telegram.close()
        self.duckdb.close()
        self.sqlite.close()
        logger.info("Trading scheduler stopped")
