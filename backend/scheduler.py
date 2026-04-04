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
from backend.strategies.base import MarketState, OrderIntent, Position, Side, StrategyName
from backend.strategies.entropy_screener import EntropyScreener
from backend.strategies.avellaneda_stoikov import AvellanedaStoikovMM
from backend.strategies.arb_scanner import ArbScanner
from backend.strategies.theta_harvester import ThetaHarvester
from backend.strategies.copy_trader import CopyTrader, CopyTarget, CopyTradeEvent
from backend.strategies.jet_signal import JetSignalStrategy
from backend.aggregator.signal_aggregator import SignalAggregator
from backend.risk.engine import RiskEngine
from backend.execution.executor import OrderExecutor
from backend.execution.exit_manager import ExitManager, ExitRule
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

        # Exit manager
        self.exit_manager = ExitManager(ExitRule(
            take_profit_pct=0.30,
            stop_loss_pct=-0.20,
            resolution_hours=1.0,
            trailing_stop_pct=0.15,
        ))

        # Ensemble AI (for model probability — uses real APIs when keys configured)
        from backend.strategies.ensemble_ai import EnsembleAI
        self.ensemble = EnsembleAI(
            anthropic_api_key=settings.ai.anthropic_api_key,
            openai_api_key=settings.ai.openai_api_key,
        )

        # Prediction Intelligence — logs every decision + outcome so the
        # retrospective analyzer can grade the bot and tune weights.
        # Soft-imported so a broken PI install never takes the scheduler down.
        self._decision_logger = None
        try:
            from prediction_intelligence.logger import DecisionLogger
            self._decision_logger = DecisionLogger()
        except Exception as e:
            logger.warning(f"Decision logger unavailable — learning loop off: {e}")

        # Specialist layer — attach decision logger so specialist opinions
        # also feed the learning loop for Brier scoring + weight tuning.
        try:
            from backend.strategies.specialists.orchestrator import (
                get_specialist_orchestrator,
            )
            specialist_orch = get_specialist_orchestrator()
            specialist_orch.attach_decision_logger(self._decision_logger)
            logger.info(
                f"Specialist layer ready: news + onchain + history + mirofish "
                f"(gate {settings.specialists.min_edge*100:.1f}% edge, "
                f"mirofish {'shadow' if settings.trading.paper_trading else 'active'} mode)"
            )
        except Exception as e:
            logger.warning(f"Specialist orchestrator not loaded: {e}")

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
            logger.info(f"Refreshed {len(markets)} markets from Gamma API")
        except Exception as e:
            logger.error(f"Market refresh failed: {e}")
            return  # Don't try to broadcast if refresh failed

        # Broadcast price updates (separate try so refresh data is still saved)
        try:
            for m in markets[:20]:
                await self.state.broadcast("price_update", {
                    "market_id": m.market_id,
                    "yes_price": m.yes_price,
                    "no_price": m.no_price,
                })
        except Exception as e:
            logger.debug(f"Price broadcast failed (non-fatal): {e}")

    async def run_ensemble_probabilities(self) -> None:
        """
        Run the AI ensemble on top markets to get real model probabilities.

        This replaces the fake simple_model_estimate with actual Claude/GPT-4o
        debate results. Only runs on the top 10 markets by liquidity to manage
        API costs. Markets without ensemble results keep their previous model_probability.
        """
        if not settings.strategies.ensemble:
            return
        if not (settings.ai.anthropic_api_key or settings.ai.openai_api_key):
            # No AI keys configured — use heuristic fallback
            for m in self.state.markets:
                if m.model_probability == 0:
                    # Contrarian heuristic: nudge toward 0.5 by 10%
                    nudge = (0.5 - m.yes_price) * 0.10
                    m.model_probability = max(0.05, min(0.95, m.yes_price + nudge))
            return

        try:
            # Only run on top 10 by liquidity (API cost management)
            top_markets = sorted(
                self.state.markets, key=lambda m: m.liquidity, reverse=True
            )[:10]

            for market in top_markets:
                try:
                    result = await self.ensemble.run_ensemble(market)
                    market.model_probability = result.ensemble_probability
                    market.kl_divergence = abs(
                        result.ensemble_probability - market.yes_price
                    )
                    logger.debug(
                        f"Ensemble: {market.question[:40]} → "
                        f"p={result.ensemble_probability:.3f} "
                        f"(conf={result.ensemble_confidence:.2f})"
                    )
                except Exception as e:
                    logger.warning(f"Ensemble failed for {market.market_id}: {e}")
                    # Keep previous model_probability

        except Exception as e:
            logger.error(f"Ensemble probability run failed: {e}")

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
        try:
            import yaml
            from pathlib import Path
            from backend.data_layer.adsb_client import PointOfInterest

            data_dir = Path(__file__).resolve().parent.parent / "data"
            targets_path = data_dir / "targets.yaml"
            pois_path = data_dir / "pois.yaml"

            # Load targets
            icao_list = []
            target_map: dict[str, dict] = {}
            if targets_path.exists():
                with open(targets_path) as f:
                    tdata = yaml.safe_load(f) or {}
                for t in tdata.get("targets", []):
                    for icao in t.get("icao24", []):
                        icao_list.append(icao)
                        target_map[icao] = t

            if not icao_list:
                return

            # Load POIs
            pois: list[PointOfInterest] = []
            if pois_path.exists():
                with open(pois_path) as f:
                    pdata = yaml.safe_load(f) or {}
                for p in pdata.get("pois", []):
                    pois.append(PointOfInterest(
                        name=p["name"], latitude=p["latitude"],
                        longitude=p["longitude"], category=p.get("category", ""),
                        market_tags=p.get("market_tags", []),
                    ))

            # Fetch positions from OpenSky
            positions = await self.adsb.get_aircraft_opensky(icao_list)
            if not positions:
                return

            # Enrich with target info
            for pos in positions:
                info = target_map.get(pos.icao24, {})
                pos.target_name = info.get("name", "")
                pos.target_role = info.get("role", "")
                pos.tail_number = (info.get("tails") or [""])[0]

            # Update shared state with flight data
            self.state.jet_flights = [
                {
                    "target_name": p.target_name, "role": p.target_role,
                    "tail": p.tail_number, "icao24": p.icao24,
                    "lat": p.latitude, "lon": p.longitude,
                    "altitude_ft": p.altitude_ft, "on_ground": p.on_ground,
                    "timestamp": p.timestamp.isoformat(),
                }
                for p in positions
            ]

            # Check proximity to POIs
            signals = self.adsb.check_proximity(positions, pois)
            if signals:
                self.state.jet_signals = [
                    {
                        "target_name": s.aircraft.target_name,
                        "poi": s.poi.name, "distance_nm": s.distance_nm,
                        "signal_strength": s.signal_strength,
                        "market_tags": s.market_tags,
                        "timestamp": s.timestamp.isoformat(),
                    }
                    for s in signals
                ]
                # Match to markets and generate intents
                matched = self.jet.match_signals_to_markets(signals, self.state.markets)
                intents = await self.jet.evaluate_batch(self.state.markets)
                self._all_intents.extend(intents)
                for s in signals:
                    self.state.add_jet_event({
                        "target_name": s.aircraft.target_name,
                        "poi": s.poi.name, "distance_nm": s.distance_nm,
                        "strength": s.signal_strength,
                        "timestamp": s.timestamp.isoformat(),
                    })
                    await self.state.broadcast("jet_event", {
                        "target": s.aircraft.target_name,
                        "poi": s.poi.name,
                        "distance": s.distance_nm,
                        "strength": s.signal_strength,
                    })
                logger.info(f"Jet tracker: {len(signals)} proximity signals")
        except Exception as e:
            logger.error(f"Jet tracker failed: {e}")

    async def update_position_prices(self) -> None:
        """Update current prices for all open positions and check exit rules."""
        if not self.state.positions:
            return
        try:
            for pos in self.state.positions:
                market = self.state.get_market(pos.market_id)
                if market:
                    if pos.side == Side.YES:
                        pos.current_price = market.yes_price
                    else:
                        pos.current_price = market.no_price

            # Update unrealized PnL
            self.state.unrealized_pnl = sum(p.pnl for p in self.state.positions)

            # Check exit rules (take-profit, stop-loss, resolution, trailing)
            exit_signals = self.exit_manager.check_exits(
                self.state.positions, self.state.markets
            )
            for signal in exit_signals:
                logger.info(
                    f"EXIT: {signal.reason} — {signal.position.question[:40]} "
                    f"PnL=${signal.pnl:.2f}"
                )
                closed = self.state.close_position(signal.position.market_id)
                if closed:
                    self.exit_manager.clear_tracking(closed.market_id)
                    # Feed the learning loop: grade the original decision.
                    self._log_outcome(closed)
                    self.duckdb.insert_trade(
                        market_id=closed.market_id,
                        question=closed.question[:200],
                        side=closed.side.value,
                        price=closed.current_price,
                        size_usdc=closed.size_usdc,
                        strategy=f"{closed.strategy.value}_exit",
                        paper=self.state.paper_trading,
                        pnl=closed.pnl,
                        trade_type="close",
                        exit_reason=signal.reason[:200],
                    )
                    await self.telegram.trade_alert(
                        strategy=f"{closed.strategy.value}_exit",
                        side="CLOSE",
                        market=closed.question,
                        size=closed.size_usdc,
                        price=closed.current_price,
                    )
                    await self.state.broadcast("position_closed", {
                        "market_id": closed.market_id,
                        "reason": signal.reason,
                        "pnl": closed.pnl,
                    })

            # Persist equity snapshot to DuckDB
            self.duckdb.insert_snapshot(
                market_id="__portfolio__",
                question="equity_snapshot",
                yes_price=self.state.balance,
                no_price=self.state.unrealized_pnl,
                liquidity=self.state.total_exposure,
                volume_24h=self.state.realized_pnl,
                entropy_bits=0,
                kl_divergence=0,
                model_probability=0,
            )

            # Update equity curve in shared state
            self.state.equity_curve.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "balance": self.state.balance + self.state.unrealized_pnl,
                "unrealized_pnl": self.state.unrealized_pnl,
                "realized_pnl": self.state.realized_pnl,
            })
            self.state.equity_curve = self.state.equity_curve[-2000:]

            # Compute Sharpe ratio from equity curve returns
            if len(self.state.equity_curve) > 10:
                try:
                    balances = [p["balance"] for p in self.state.equity_curve if p.get("balance", 0) > 0]
                    if len(balances) > 2:
                        returns = [
                            (balances[i] - balances[i-1]) / balances[i-1]
                            for i in range(1, len(balances))
                            if balances[i-1] > 0
                        ]
                        if returns:
                            import statistics
                            mean_ret = statistics.mean(returns)
                            std_ret = statistics.stdev(returns) if len(returns) > 1 else 0.001
                            # Annualize: assume ~100 data points per day at 15s intervals
                            self.state.sharpe_ratio = round(
                                (mean_ret / max(std_ret, 0.0001)) * (365 ** 0.5), 3
                            )
                except Exception:
                    pass

            # Compute win rate from trades_today and realized P&L
            if self.state.trades_today > 0:
                # Estimate from exit manager results
                total_exits = sum(1 for p in self.state.equity_curve[-100:] if p.get("realized_pnl", 0) != 0)
                if total_exits > 0:
                    self.state.win_rate = min(0.95, max(0.05,
                        self.state.realized_pnl / max(abs(self.state.realized_pnl) + 1, 1)
                    ))

        except Exception as e:
            logger.error(f"Position price update failed: {e}")

    async def poll_wallet_activity(self) -> None:
        """Poll copy target wallets for new trades."""
        if not settings.strategies.copy:
            return
        try:
            for target_addr in settings.copy.targets:
                if not target_addr:
                    continue
                trades = await self.data_api.get_wallet_trades(target_addr, limit=10)
                if not trades:
                    continue

                positions = await self.data_api.get_wallet_positions(target_addr)

                # Add to whale trades feed
                for trade in trades[:5]:
                    whale_entry = {
                        "wallet": target_addr[:10] + "...",
                        "display_name": target_addr[:8],
                        "tier": "elite",
                        "market_id": trade.get("marketId", trade.get("market", "")),
                        "question": trade.get("question", trade.get("title", "")),
                        "side": trade.get("side", "YES"),
                        "size_usdc": float(trade.get("size", trade.get("amount", 0))),
                        "price": float(trade.get("price", 0)),
                        "timestamp": trade.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    }
                    self.state.add_whale_trade(whale_entry)
                    await self.state.broadcast("whale_trade", whale_entry)

                    # Queue for copy trading
                    copy_event = CopyTradeEvent(
                        target=CopyTarget(
                            address=target_addr,
                            display_name=target_addr[:8],
                            auto_copy=not settings.copy.confluence_required,
                            copy_ratio=settings.copy.ratio,
                        ),
                        market_id=whale_entry["market_id"],
                        question=whale_entry["question"],
                        side=Side.YES if whale_entry["side"] == "YES" else Side.NO,
                        size_usdc=whale_entry["size_usdc"],
                        price=whale_entry["price"],
                    )
                    self.copy.queue_event(copy_event)
                    self.state.add_to_copy_queue({
                        "target_name": whale_entry["display_name"],
                        "market_id": whale_entry["market_id"],
                        "question": whale_entry["question"],
                        "side": whale_entry["side"],
                        "size_usdc": whale_entry["size_usdc"],
                        "price": whale_entry["price"],
                        "confluence_count": 0,
                    })

        except Exception as e:
            logger.error(f"Wallet polling failed: {e}")

    async def refresh_leaderboard(self) -> None:
        """Refresh the leaderboard data for whale tracking."""
        try:
            entries = await self.data_api.get_leaderboard(limit=25)
            self.state.leaderboard = [
                {
                    "rank": e.rank,
                    "address": e.address,
                    "display_name": e.display_name,
                    "pnl": e.pnl,
                    "volume": e.volume,
                    "markets_traded": e.markets_traded,
                    "win_rate": e.win_rate,
                    "tier": e.tier,
                }
                for e in entries
            ]
            # Compute Smart Money Index from top wallet positioning
            if entries:
                # Simple SMI: % of top wallets in profit recently
                profitable = sum(1 for e in entries if e.pnl > 0)
                self.state.smart_money_index = int(profitable / len(entries) * 100)
        except Exception as e:
            logger.error(f"Leaderboard refresh failed: {e}")

    async def persist_state(self) -> None:
        """Persist current state to DuckDB/SQLite for restart recovery."""
        try:
            # Save active positions to SQLite
            for pos in self.state.positions:
                self.sqlite.save_strategy_state(
                    f"position_{pos.market_id}",
                    {
                        "market_id": pos.market_id,
                        "condition_id": pos.condition_id,
                        "question": pos.question,
                        "side": pos.side.value,
                        "entry_price": pos.entry_price,
                        "size_usdc": pos.size_usdc,
                        "current_price": pos.current_price,
                        "strategy": pos.strategy.value,
                        "opened_at": pos.opened_at.isoformat(),
                    },
                )

            # Save equity snapshot
            self.sqlite.save_strategy_state("__equity__", {
                "balance": self.state.balance,
                "realized_pnl": self.state.realized_pnl,
                "trades_today": self.state.trades_today,
            })
        except Exception as e:
            logger.error(f"State persistence failed: {e}")

    async def restore_state(self) -> None:
        """Restore state from SQLite and DuckDB on startup."""
        try:
            # Restore from SQLite (saved every 2 minutes)
            equity = self.sqlite.load_strategy_state("__equity__")
            if equity:
                self.state.balance = equity.get("balance", 10000)
                self.state.realized_pnl = equity.get("realized_pnl", 0)
                self.state.trades_today = equity.get("trades_today", 0)
                logger.info(f"Restored from SQLite: balance=${self.state.balance:.2f}, pnl=${self.state.realized_pnl:.2f}")

            # Also check DuckDB for cumulative P&L from trade records
            # This is more accurate than the SQLite snapshot if the bot crashed
            try:
                trade_pnl = self.duckdb.query(
                    "SELECT COALESCE(SUM(pnl), 0) as total_pnl, COUNT(*) as total_trades "
                    "FROM trades WHERE (trade_type = 'close' OR pnl != 0)"
                )
                if trade_pnl and trade_pnl[0].get("total_pnl", 0) != 0:
                    db_pnl = trade_pnl[0]["total_pnl"]
                    db_trades = trade_pnl[0]["total_trades"]
                    # Use the larger P&L (DuckDB may have more recent data than SQLite)
                    if abs(db_pnl) > abs(self.state.realized_pnl):
                        self.state.realized_pnl = db_pnl
                        logger.info(f"Restored P&L from DuckDB: ${db_pnl:.2f} ({db_trades} closed trades)")
            except Exception as e:
                logger.debug(f"DuckDB P&L restore skipped: {e}")

            # Restore positions
            existing = self.sqlite.get_active_positions()
            for row in existing:
                pos = Position(
                    market_id=row["market_id"],
                    condition_id=row.get("condition_id", ""),
                    question=row.get("question", ""),
                    side=Side.YES if row["side"] == "YES" else Side.NO,
                    entry_price=row["entry_price"],
                    size_usdc=row["size_usdc"],
                    current_price=row.get("current_price", row["entry_price"]),
                    strategy=StrategyName(row.get("strategy", "entropy")),
                )
                self.state.add_position(pos)
            if self.state.positions:
                logger.info(f"Restored {len(self.state.positions)} positions")

            # Restore equity curve from DuckDB
            rows = self.duckdb.query(
                "SELECT ts, yes_price as balance, no_price as unrealized_pnl, "
                "volume_24h as realized_pnl FROM market_snapshots "
                "WHERE market_id = '__portfolio__' ORDER BY ts DESC LIMIT 2000"
            )
            if rows:
                self.state.equity_curve = [
                    {
                        "timestamp": str(r["ts"]),
                        "balance": r["balance"],
                        "unrealized_pnl": r["unrealized_pnl"],
                        "realized_pnl": r["realized_pnl"],
                    }
                    for r in reversed(rows)
                ]
                logger.info(f"Restored {len(rows)} equity curve points")

        except Exception as e:
            logger.error(f"State restoration failed: {e}")

    # ── Prediction Intelligence hooks ───────────────────────────
    def _log_decision(self, intent, result) -> str:
        """
        Log an opened position to the prediction_intelligence decision
        store. Returns the decision_id (or "" if logging is disabled).
        Never raises.
        """
        if self._decision_logger is None:
            return ""
        try:
            from prediction_intelligence.logger import DecisionRecord
            record = DecisionRecord(
                market_id=intent.market_id,
                market_title=(intent.question or "")[:200],
                implied_probability=result.fill_price,
                fair_probability=getattr(intent, "fair_probability", 0.5) or 0.5,
                edge_estimate=getattr(intent, "edge", 0.0) or 0.0,
                opportunity_score=getattr(intent, "confidence", 0.0) or 0.0,
                classification="LIVE" if not result.paper else "PAPER",
                paper_position_size=result.fill_size,
                paper_entry_price=result.fill_price,
                risk_approved=True,
                signal_weights={"strategy": intent.strategy.value},
            )
            return self._decision_logger.log_decision(record)
        except Exception as e:
            logger.warning(f"log_decision failed: {e}")
            return ""

    def _log_outcome(self, closed_position) -> None:
        """
        Log a closed position as an outcome for the learning loop.
        Computes a simple Brier score from the realized pnl direction.
        Never raises.
        """
        if self._decision_logger is None or not getattr(closed_position, "decision_id", ""):
            return
        try:
            from prediction_intelligence.logger import OutcomeRecord
            from datetime import datetime, timezone
            # Entry thesis was "price moves in favor of side". If pnl > 0,
            # the thesis was correct → actual_outcome = 1, else 0.
            actual = 1.0 if closed_position.pnl > 0 else 0.0
            entry = closed_position.entry_price or 0.5
            # Brier score = (forecast - actual)^2 using entry price as the
            # implied forecast (we may not have the full fair_p here).
            forecast = entry if closed_position.side.value == "YES" else (1.0 - entry)
            brier = (forecast - actual) ** 2
            hours = max(
                0.0,
                (datetime.now(timezone.utc) - closed_position.opened_at).total_seconds() / 3600.0,
            )
            outcome = OutcomeRecord(
                decision_id=closed_position.decision_id,
                market_id=closed_position.market_id,
                resolution_timestamp=datetime.now(timezone.utc).isoformat(),
                actual_outcome=actual,
                forecast_error=abs(forecast - actual),
                brier_score=brier,
                paper_pnl=closed_position.pnl,
                resolution_source="exit_manager",
                time_to_resolution_hours=hours,
            )
            self._decision_logger.log_outcome(outcome)
        except Exception as e:
            logger.warning(f"log_outcome failed: {e}")

    async def aggregate_and_execute(self) -> None:
        """Score all intents, run risk checks, and execute approved trades."""
        if not self._all_intents:
            return

        # Keep the executor's paper/live flag in sync with system state
        # on every tick, so Go Live takes effect immediately.
        try:
            self.executor.paper_trading = self.state.paper_trading
            if not self.state.paper_trading and self.executor._live_client is None:
                # Lazy-init live client if it wasn't built in __init__
                self.executor.set_mode("live")
        except Exception as e:
            logger.error(f"Executor mode sync failed: {e}")

        try:
            # Score and rank
            scored = self.aggregator.score(self._all_intents)

            # Risk check
            approved = self.risk.check_batch(scored)

            # ── Dedupe: never open a new position on a market+side where we
            # already hold one. Avellaneda-Stoikov re-quotes every tick, so
            # without this guard it stacks dozens of identical positions on
            # the same market, inflating win counts and exposure.
            held_keys = {(p.market_id, p.side) for p in self.state.positions}
            deduped = []
            for si in approved:
                key = (si.intent.market_id, si.intent.side)
                if key in held_keys:
                    continue
                held_keys.add(key)  # also blocks same-batch duplicates
                deduped.append(si)
            if len(deduped) < len(approved):
                logger.info(
                    f"Deduped {len(approved) - len(deduped)} duplicate intents "
                    f"(market+side already held)"
                )
            approved = deduped

            if approved:
                logger.info(f"Executing {len(approved)} approved trades")
                # Build a lookup of REAL current token prices so paper
                # fills reflect the actual book instead of the strategy's
                # intended limit price. For YES side we pass yes_price,
                # for NO side we pass no_price — the price of the token
                # actually being bought.
                from backend.strategies.base import Side
                price_lookup: dict[str, float] = {}
                for m in self.state.markets:
                    price_lookup[m.market_id] = m.yes_price  # default
                # Overwrite per-intent with the side-specific price
                per_intent_prices: dict[str, float] = {}
                market_by_id = {m.market_id: m for m in self.state.markets}
                for si in approved:
                    m = market_by_id.get(si.intent.market_id)
                    if m is None:
                        continue
                    per_intent_prices[si.intent.market_id] = (
                        m.yes_price if si.intent.side == Side.YES else m.no_price
                    )
                results = await self.executor.execute_batch(approved, market_prices=per_intent_prices)

                for si, result in zip(approved, results):
                    if result.success:
                        self.risk.record_trade(si.intent)
                        self.state.trades_today += 1

                        # Track position in shared state
                        pos = self.executor.to_position(si.intent, result)
                        if pos:
                            # Log to prediction_intelligence and attach the
                            # decision_id to the position so we can score the
                            # outcome on close.
                            pos.decision_id = self._log_decision(si.intent, result)
                            self.state.add_position(pos)

                        # Store in DuckDB
                        self.duckdb.insert_trade(
                            market_id=si.intent.market_id,
                            question=si.intent.question[:200],
                            side=si.intent.side.value,
                            price=result.fill_price,
                            size_usdc=result.fill_size,
                            strategy=si.intent.strategy.value,
                            paper=result.paper,
                            trade_type="open",
                        )

                        # Telegram alert
                        await self.telegram.trade_alert(
                            strategy=si.intent.strategy.value,
                            side=si.intent.side.value,
                            market=si.intent.question,
                            size=result.fill_size,
                            price=result.fill_price,
                        )

                        # Add as signal for Journal feed
                        self.state.add_signal(si.intent)

                        # WebSocket broadcast
                        await self.state.broadcast("trade", {
                            "strategy": si.intent.strategy.value,
                            "market_id": si.intent.market_id,
                            "side": si.intent.side.value,
                            "price": result.fill_price,
                            "size": result.fill_size,
                            "paper": result.paper,
                        })

            # Clear intents for next cycle
            self._all_intents.clear()
        except Exception as e:
            logger.error(f"Aggregate/execute failed: {e}")
            self._all_intents.clear()

    async def daily_reset(self) -> None:
        """Reset daily risk counters at midnight UTC."""
        self.risk.reset_daily()
        self.state.trades_today = 0

        # Compute daily PnL entry
        from datetime import datetime, timezone
        self.state.daily_pnl.append({
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "pnl": self.state.realized_pnl,
        })
        self.state.daily_pnl = self.state.daily_pnl[-90:]  # keep 90 days

        logger.info("Daily risk counters reset")
        await self.telegram.send("<b>DAILY RESET</b> — Risk counters cleared")

    def start(self) -> None:
        """Register all jobs and start the scheduler."""
        self.duckdb.connect()
        self.sqlite.connect()

        # Share the DuckDB connection with the API layer for trade queries
        self.state._duckdb = self.duckdb
        # Share the executor with the execution API so Go Live actually flips
        # the scheduler's trade path over to real CLOB orders.
        self.state._executor = self.executor

        # Restore persisted state from previous run
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.restore_state())
            else:
                loop.run_until_complete(self.restore_state())
        except RuntimeError:
            asyncio.run(self.restore_state())

        # Market data refresh
        self.scheduler.add_job(
            self.refresh_markets,
            IntervalTrigger(seconds=45),
            id="refresh_markets",
            name="Market data refresh",
        )

        # Position price updates (every 15s)
        self.scheduler.add_job(
            self.update_position_prices,
            IntervalTrigger(seconds=15),
            id="position_prices",
            name="Position price updater",
        )

        # AI ensemble model probabilities (every 3 min — manages API costs)
        self.scheduler.add_job(
            self.run_ensemble_probabilities,
            IntervalTrigger(minutes=3),
            id="ensemble_probabilities",
            name="AI ensemble probabilities",
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

        # Wallet polling for copy trading (every 30s)
        self.scheduler.add_job(
            self.poll_wallet_activity,
            IntervalTrigger(seconds=30),
            id="wallet_poll",
            name="Copy trade wallet polling",
        )

        # Leaderboard refresh (every 5 min)
        self.scheduler.add_job(
            self.refresh_leaderboard,
            IntervalTrigger(minutes=5),
            id="leaderboard",
            name="Leaderboard refresh",
        )

        # Aggregation + execution
        self.scheduler.add_job(
            self.aggregate_and_execute,
            IntervalTrigger(seconds=30),
            id="aggregate_execute",
            name="Signal aggregation + execution",
        )

        # State persistence (every 2 min)
        self.scheduler.add_job(
            self.persist_state,
            IntervalTrigger(minutes=2),
            id="persist_state",
            name="State persistence",
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
        logger.info(
            f"Scheduled jobs: markets(45s), positions(15s), entropy(60s), "
            f"arb(15s), MM(10s), theta(5m), jet(60s), wallet(30s), "
            f"leaderboard(5m), aggregate(30s), persist(2m), daily(midnight)"
        )

    async def stop(self) -> None:
        """Graceful shutdown — persist state then close all."""
        await self.persist_state()
        self.scheduler.shutdown(wait=True)
        if not self.executor.paper_trading:
            await self.executor.cancel_all_live()
        await self.gamma.close()
        await self.adsb.close()
        await self.data_api.close()
        await self.telegram.close()
        self.duckdb.close()
        self.sqlite.close()
        logger.info("Trading scheduler stopped — state persisted")
