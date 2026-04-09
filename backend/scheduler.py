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
import re
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
from backend.strategies.binance_arb import BinanceArb
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

        # Strategies — Kelly sizing uses the real starting capital and
        # the risk engine's per-trade cap, not the legacy $10k/$150 defaults.
        _bankroll = settings.trading.starting_capital
        _max_trade = settings.risk.max_trade_size_usdc
        _kelly_mult = settings.quant.kelly_fraction
        self.entropy = EntropyScreener(
            entropy_threshold=settings.quant.entropy_threshold,
            efficiency_max=settings.quant.entropy_efficiency_max,
            kelly_fraction=_kelly_mult,
            bankroll=_bankroll,
            max_trade_usdc=_max_trade,
            # Lowered from the $25k default to match A-S and Polymarket
            # reality. Typical liquid Polymarket markets are $2k-$20k;
            # a $25k floor limits the screener to a tiny sliver.
            min_liquidity=2000,
        )
        self.avellaneda = AvellanedaStoikovMM(
            gamma=settings.quant.as_gamma,
            kappa=settings.quant.as_kappa,
            session_hours=settings.quant.as_session_hours,
            vpin_threshold=settings.quant.vpin_pause_threshold,
            bankroll=_bankroll,
            kelly_fraction_mult=_kelly_mult,
            max_trade_usdc=_max_trade,
        )
        self.arb = ArbScanner(min_arb_edge=settings.quant.min_arb_edge)
        self.binance_arb = BinanceArb(
            min_liquidity=2000,
            bankroll=_bankroll,
            kelly_fraction_mult=_kelly_mult,
            max_trade_usdc=_max_trade,
        )
        self.theta = ThetaHarvester(
            bankroll=_bankroll,
            kelly_fraction_mult=_kelly_mult,
            max_size_usdc=_max_trade,
        )
        self.copy = CopyTrader(
            default_ratio=settings.copy.ratio,
            max_size_usdc=settings.copy.max_size_usdc,
            confluence_required=settings.copy.confluence_required,
        )

        # Correlation scanner — detects logically related market mispricings
        from backend.strategies.correlation_scanner import MarketCorrelationScanner
        self.correlation_scanner = MarketCorrelationScanner()

        # Wallet pattern analyzer — smart money confirming signal
        from backend.strategies.wallet_analyzer import WalletPatternAnalyzer
        self.wallet_analyzer = WalletPatternAnalyzer()
        self.jet = JetSignalStrategy(adsb_client=self.adsb)

        # Infrastructure
        self.aggregator = SignalAggregator()
        self.aggregator.attach_wallet_analyzer(self.wallet_analyzer)
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

        # Exit manager — swing exits capture model edge, time-decay
        # lowers targets as positions age, trailing stop protects gains.
        self.exit_manager = ExitManager(ExitRule(
            edge_capture_pct=0.60,        # sell when 60% of model edge captured
            take_profit_pct=0.30,         # fallback when no model_probability
            stop_loss_pct=-0.20,
            trailing_stop_pct=0.15,
            age_hours_full_target=2.0,    # first 2h: hold for full target
            age_hours_min_target=24.0,    # by 24h: accept 2% return
            min_profit_to_exit=0.02,
            resolution_hours=1.0,
            resolution_min_profit=0.10,
        ))

        # Ensemble AI (for model probability — uses real APIs when keys configured)
        from backend.strategies.ensemble_ai import EnsembleAI
        self.ensemble = EnsembleAI(
            anthropic_api_key=settings.ai.anthropic_api_key,
            openai_api_key=settings.ai.openai_api_key,
        )
        # Per-market ensemble dedupe: market_id -> last run wall-clock seconds.
        # Prevents the job from re-analyzing the same market every cycle when
        # it always sorts to the top of the priority list.
        self._ensemble_last_run: dict[str, float] = {}
        self._ensemble_dedupe_seconds: int = 1800  # 30 min — don't re-analyze same market too often

        # Prediction Intelligence — shared orchestrator owns DecisionLogger,
        # RetrospectiveAnalyzer, and WeightAdjuster. A single instance is
        # shared with the /api/v1/intelligence endpoints via system_state
        # so both sides use the SAME DuckDB connection to
        # data/prediction_intelligence.db. Before this, scheduler and
        # API each created their own DecisionLogger → two write
        # connections on the same DuckDB file → lock conflict the
        # moment you hit /analysis/trigger → "crashes the site".
        # Soft-imported so a broken PI install never takes the scheduler down.
        self._pi_orchestrator = None
        self._decision_logger = None
        try:
            from prediction_intelligence.orchestrator import LoopOrchestrator
            self._pi_orchestrator = LoopOrchestrator()
            self._decision_logger = self._pi_orchestrator.decision_logger
            # Force connection + schema creation now so we fail loud at
            # startup instead of silently on the first log_decision call.
            self._decision_logger._ensure_conn()
            logger.info(f"Prediction Intelligence DB connected: {self._decision_logger.db_path}")
        except Exception as e:
            logger.warning(f"Intelligence orchestrator unavailable — decision logging off: {e}", exc_info=True)

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
        # Expose the prediction_intelligence orchestrator via system_state
        # so /api/v1/intelligence endpoints reuse the SAME DecisionLogger
        # instance the scheduler uses. Without this, each side opens its
        # own write connection to data/prediction_intelligence.db and
        # the trigger endpoint crashes on the DuckDB lock.
        self.state._pi_orchestrator = self._pi_orchestrator
        # Expose scheduler ref so the settings API can modify risk/exit params live.
        self.state._scheduler = self
        # Wire the real starting capital so the dashboard and ROI calculations
        # use $300 (or whatever STARTING_CAPITAL is set to) instead of the
        # legacy $10k default baked into state.py.
        self.state.starting_capital = settings.trading.starting_capital
        # Reset balance to starting capital when:
        #   - First run (balance is default 10k or 0)
        #   - Switching to live mode (paper balance is meaningless for live)
        #   - Balance drifted below min_balance (stale paper state)
        if (
            self.state.balance <= 0
            or self.state.balance == 10_000.0
            or not settings.trading.paper_trading  # live mode → always reset
        ):
            self.state.balance = settings.trading.starting_capital
            logger.info(f"Balance reset to starting capital: ${settings.trading.starting_capital:.2f}")

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

    # Sports & esports filter. Two tiers:
    #   _SPORTS_ALWAYS: generic sport/league names — substring match is safe
    #   _SPORTS_TEAMS: team names that are also common words (magic, heat,
    #       thunder, rockets, kings, giants, angels, wild, jets, rams, etc.)
    #       — require word-boundary match via regex to avoid false positives
    #       like "Will Magic Mushroom stocks…" or "Thunder Bay port expansion"
    _SPORTS_ALWAYS = frozenset({
        "sports", "nfl", "nba", "mlb", "nhl", "soccer", "tennis",
        "football", "basketball", "baseball", "hockey", "ufc", "mma",
        "boxing", "cricket", "f1", "formula 1", "nascar", "rugby",
        "golf", "pga", "atp", "wta", "la liga", "premier league",
        "serie a", "bundesliga", "champions league", "world cup",
        "esports", "counter-strike", "valorant", "dota", "league of legends",
        "lol:", "overwatch", "call of duty", "fortnite", "rainbow six",
        # Tennis tournaments
        "monte carlo masters", "roland garros", "wimbledon", "us open tennis",
        "australian open", "french open", "indian wells", "miami open",
        "rolex masters",
    })
    _SPORTS_TEAMS = frozenset({
        "yankees", "marlins", "dodgers", "mets", "cubs", "red sox",
        "braves", "astros", "phillies", "padres", "orioles", "guardians",
        "rangers", "royals", "twins", "tigers", "white sox", "reds",
        "brewers", "cardinals", "pirates", "nationals", "diamondbacks",
        "rockies", "giants", "athletics", "rays", "blue jays", "mariners",
        "angels", "lakers", "celtics", "warriors", "bucks", "nuggets",
        "76ers", "sixers", "knicks", "nets", "heat", "bulls", "cavaliers",
        "pacers", "hawks", "raptors", "spurs", "suns", "mavericks",
        "clippers", "grizzlies", "pelicans", "wizards", "pistons",
        "hornets", "timberwolves", "blazers", "kings", "thunder",
        "rockets", "magic", "chiefs", "eagles", "49ers", "ravens",
        "cowboys", "bills", "dolphins", "bengals", "lions", "vikings",
        "packers", "steelers", "chargers", "seahawks", "broncos", "colts",
        "texans", "jaguars", "titans", "saints", "falcons", "panthers",
        "buccaneers", "patriots", "jets", "rams", "bears", "commanders",
        "bruins", "maple leafs", "canadiens", "penguins", "blackhawks",
        "red wings", "flyers", "oilers", "avalanche", "lightning",
        "hurricanes", "predators", "kraken", "canucks", "flames",
        "senators", "sabres", "blue jackets", "wild",
    })
    # Pre-compiled regex for word-boundary team matching.
    # `re` imported at module level — class-body generators can't see
    # class-scoped names in Python 3.
    _TEAM_PATTERN = re.compile(
        r"\b(?:" + "|".join(re.escape(t) for t in _SPORTS_TEAMS) + r")\b",
        re.IGNORECASE,
    )

    @classmethod
    def _is_sports_market(cls, gm) -> bool:
        """True if a GammaMarket is a sports/esports market to filter."""
        cat = (gm.category or "").lower()
        q = (gm.question or "").lower()
        # Tier 1: generic sport keywords — substring match
        for kw in cls._SPORTS_ALWAYS:
            if kw in cat or kw in q:
                return True
        # Tier 2: team names — word-boundary regex to avoid false positives
        if cls._TEAM_PATTERN.search(q) or cls._TEAM_PATTERN.search(cat):
            return True
        # Tier 3: raw Gamma API tags
        raw_tags = gm.raw.get("tags") or []
        if isinstance(raw_tags, list):
            for tag in raw_tags:
                if isinstance(tag, dict):
                    tag = tag.get("label", "")
                tag_lower = str(tag).lower()
                for kw in cls._SPORTS_ALWAYS:
                    if kw in tag_lower:
                        return True
        return False

    async def refresh_markets(self) -> None:
        """Fetch latest market data from Gamma API."""
        try:
            gamma_markets = await self.gamma.get_active_markets(min_liquidity=10000, limit=100)
            # Sports filter — these are the only net-losing category
            pre_filter = len(gamma_markets)
            gamma_markets = [gm for gm in gamma_markets if not self._is_sports_market(gm)]
            n_sports = pre_filter - len(gamma_markets)
            markets = [self._gamma_to_market_state(gm) for gm in gamma_markets]
            self.state.update_markets(markets)
            sports_note = f" ({n_sports} sports filtered)" if n_sports else ""
            logger.info(f"Refreshed {len(markets)} markets from Gamma API{sports_note}")
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

        Replaces fake heuristics with actual Claude/GPT-4o debate results.

        Prioritisation (key fix): rather than "top 10 by liquidity" — which
        always returned the same 2 Iran regime markets and starved every
        other market of analysis — we now score each market by
          score = |model_probability - yes_price| * 2 + log10(liquidity + 1) * 0.1
                  + age_bonus (how stale the last ensemble run is)
        so high-edge + stale markets float to the top and liquidity only
        acts as a tiebreaker.

        Concurrency: runs up to ENSEMBLE_CONCURRENCY markets in parallel
        via asyncio.gather + semaphore. A full cycle of 8 markets now
        completes in ~one round-trip instead of 8 × round-trip serially,
        so the 3-minute APScheduler trigger never skips.

        Dedupe: each market is re-analyzed at most once every
        self._ensemble_dedupe_seconds (10 min) so we don't burn API
        budget re-scoring the same high-edge market every cycle.
        """
        if not settings.strategies.ensemble:
            return
        if not (settings.ai.anthropic_api_key or settings.ai.openai_api_key):
            # No AI keys configured — use heuristic fallback
            for m in self.state.markets:
                if m.model_probability == 0:
                    nudge = (0.5 - m.yes_price) * 0.10
                    m.model_probability = max(0.05, min(0.95, m.yes_price + nudge))
            return

        try:
            import math
            import time

            now = time.time()
            dedupe_s = self._ensemble_dedupe_seconds

            def priority(m) -> float:
                # Edge magnitude (if we already have a model_probability)
                edge = 0.0
                if getattr(m, "model_probability", 0) and m.model_probability > 0:
                    edge = abs(m.model_probability - m.yes_price)
                liq_term = math.log10(max(1.0, float(m.liquidity or 0.0)) + 1.0) * 0.1
                last = self._ensemble_last_run.get(m.market_id, 0.0)
                age_s = max(0.0, now - last)
                # Age bonus saturates at 2× dedupe window
                age_bonus = min(1.0, age_s / max(1.0, dedupe_s * 2)) * 0.5
                # Fee-free categories (geopolitics) get a priority bonus
                # because trades there don't pay taker fees → lower edge needed
                cat = (getattr(m, "category", "") or "").lower()
                fee_bonus = 0.3 if "politic" in cat or "geopolitic" in cat else 0.0
                return edge * 2.0 + liq_term + age_bonus + fee_bonus

            # Filter out markets still in dedupe window, then sort by priority
            eligible = [
                m for m in self.state.markets
                if (now - self._ensemble_last_run.get(m.market_id, 0.0)) >= dedupe_s
            ]
            # Secondary liquidity floor so penny markets don't pollute the list
            eligible = [m for m in eligible if (m.liquidity or 0) >= 500]
            eligible.sort(key=priority, reverse=True)

            # Cap per cycle — keeps API cost predictable
            MAX_PER_CYCLE = 5
            CONCURRENCY = 3
            batch = eligible[:MAX_PER_CYCLE]

            if not batch:
                logger.debug("Ensemble cycle: no eligible markets (all in dedupe window)")
                return

            sem = asyncio.Semaphore(CONCURRENCY)

            async def analyze(market):
                async with sem:
                    try:
                        result = await self.ensemble.run_ensemble(market)
                        self._ensemble_last_run[market.market_id] = time.time()
                        if result.debates and result.ensemble_confidence > 0:
                            # Look up the CURRENT market object by ID —
                            # refresh_markets may have replaced self.state.markets
                            # while this coroutine was awaiting the API, making
                            # the captured `market` reference stale.
                            current = self.state.get_market(market.market_id)
                            if current is None:
                                current = market  # fallback
                            from backend.quant.entropy import kl_divergence as _kl
                            current.model_probability = result.ensemble_probability
                            current.kl_divergence = _kl(
                                result.ensemble_probability, current.yes_price
                            )
                            logger.info(
                                f"Ensemble OK: {market.question[:40]} → "
                                f"p={result.ensemble_probability:.3f} "
                                f"(conf={result.ensemble_confidence:.2f}, "
                                f"models={len(result.debates)})"
                            )
                            return True
                        return False
                    except Exception as e:
                        logger.warning(f"Ensemble failed for {market.market_id}: {e}")
                        return False

            results = await asyncio.gather(*(analyze(m) for m in batch), return_exceptions=True)
            updated = sum(1 for r in results if r is True)
            logger.info(
                f"Ensemble cycle: {updated}/{len(batch)} markets updated "
                f"(eligible={len(eligible)}, concurrency={CONCURRENCY})"
            )

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

    async def run_correlation_scanner(self) -> None:
        """Scan for logically correlated market mispricings."""
        if not self.state.markets:
            return
        try:
            intents = self.correlation_scanner.scan(self.state.markets)
            self._all_intents.extend(intents)
            if intents:
                logger.info(f"Correlation scanner: {len(intents)} arb opportunities")
        except Exception as e:
            logger.error(f"Correlation scanner failed: {e}")

    async def run_avellaneda_mm(self) -> None:
        """Update A-S market maker quotes."""
        if not settings.strategies.avellaneda or not self.state.markets:
            return
        try:
            intents = await self.avellaneda.evaluate_batch(self.state.markets)
            self._all_intents.extend(intents)
        except Exception as e:
            logger.error(f"A-S MM failed: {e}")

    async def run_settlement_watcher(self) -> None:
        """Close positions at the REAL Gamma resolution price when markets resolve.

        Before this watcher existed, the exit_manager's RESOLUTION EXIT
        rule would close positions an hour before actual resolution at
        the pre-resolution mid price, stealing every theta-harvester win.
        Now we query Gamma for each open position's market and detect
        resolution via outcomePrices collapsing to [0,1] or [1,0] and
        closed=True. When that happens, we force-close the position at
        the actual resolution outcome (1.0 for YES-wins, 0.0 for NO-wins)
        so the real payoff is booked.
        """
        if not self.state.positions:
            return
        try:
            # Deduplicate market_ids across positions
            market_ids = list({p.market_id for p in self.state.positions})
            if not market_ids:
                return

            settled: list[tuple[str, float, str]] = []  # (market_id, resolved_yes_price, outcome)
            for mid in market_ids:
                try:
                    gm = await self.gamma.get_market(mid)
                except Exception as e:
                    logger.debug(f"Settlement check {mid[:10]}: gamma fetch failed: {e}")
                    continue
                if gm is None:
                    continue
                # A Gamma market is resolved when closed=True and the
                # outcomePrices collapse to (1.0, 0.0) or (0.0, 1.0).
                if not getattr(gm, "closed", False):
                    continue
                yp = float(getattr(gm, "yes_price", 0.5) or 0.5)
                # Only settle when the resolution is unambiguous
                if yp >= 0.99:
                    settled.append((mid, 1.0, "YES"))
                elif yp <= 0.01:
                    settled.append((mid, 0.0, "NO"))
                # else: closed but prices haven't collapsed → skip for now

            for mid, resolved_yes_price, outcome in settled:
                # Find every position on this market (shouldn't be more
                # than one per side due to dedupe, but handle the list)
                matching = [p for p in self.state.positions if p.market_id == mid]
                for pos in matching:
                    # The settlement price depends on which side we hold.
                    # If we hold YES and it resolved YES → we paid entry,
                    # get back 1.0. If we hold YES and it resolved NO →
                    # worth 0.0. Same logic mirrored for NO positions.
                    if pos.side == Side.YES:
                        final_price = resolved_yes_price
                    else:
                        final_price = 1.0 - resolved_yes_price
                    pos.current_price = final_price
                    realized = pos.pnl  # will use the final_price we just set

                    closed = self.state.close_position(pos.market_id)
                    if not closed:
                        continue
                    self.exit_manager.clear_tracking(closed.market_id)
                    # Notify A-S if that was its position (even though
                    # disabled by default, legacy positions may exist)
                    if closed.strategy == StrategyName.AVELLANEDA:
                        try:
                            self.avellaneda.record_close(
                                market_id=closed.market_id,
                                side=closed.side,
                                size=closed.size_usdc,
                            )
                        except Exception:
                            pass
                    self._log_outcome(closed)
                    self.duckdb.insert_trade(
                        market_id=closed.market_id,
                        question=closed.question[:200],
                        side=closed.side.value,
                        price=closed.current_price,
                        size_usdc=closed.size_usdc,
                        strategy=f"{closed.strategy.value}_settled",
                        paper=self.state.paper_trading,
                        pnl=realized,
                        trade_type="close",
                        exit_reason=f"SETTLED: {outcome}@{final_price:.2f} pnl=${realized:+.2f}",
                    )
                    logger.info(
                        f"SETTLEMENT: {closed.question[:40]} resolved {outcome} → "
                        f"{closed.side.value} pos worth ${realized:+.2f}"
                    )
                    await self.state.broadcast("position_settled", {
                        "market_id": closed.market_id,
                        "outcome": outcome,
                        "pnl": realized,
                    })
        except Exception as e:
            logger.error(f"Settlement watcher failed: {e}")

    async def run_retrospective_analysis(self) -> None:
        """Auto-run the learning loop analyzer every N minutes.

        Trigger: ≥10 new scored outcomes since the last run, OR it's
        been >6 hours since the last run (whichever comes first). This
        means the analyzer only runs when there's enough fresh data to
        be meaningful, and never runs more than once every few minutes.

        Also auto-deploys weight proposals marked high-confidence
        (scored ≥ 200 outcomes AND ≤3 weight changes). Lower-confidence
        proposals are queued in data/weight_proposals/ for manual
        approval via the Intelligence tab.

        Never raises — all errors caught and logged.
        """
        if self._pi_orchestrator is None:
            return
        try:
            orch = self._pi_orchestrator
            scored = orch.decision_logger.get_scored_count()

            # Bootstrap: track the count at first run
            if not hasattr(self, "_pi_last_scored"):
                self._pi_last_scored = 0
            new_since_last = scored - self._pi_last_scored

            if scored < 10:
                logger.debug(
                    f"Learning loop: {scored} scored outcomes (need 10 to start)"
                )
                return
            if new_since_last < 10 and self._pi_last_scored > 0:
                logger.debug(
                    f"Learning loop: only {new_since_last} new outcomes since last run"
                )
                return

            logger.info(
                f"Learning loop: running analysis on {scored} outcomes "
                f"(+{new_since_last} since last run)"
            )
            report = orch.analyzer.run_analysis()
            self._pi_last_scored = scored

            # Try to propose new weights (no-op if <50 outcomes)
            try:
                report_dict = {
                    "scored_outcomes": report.scored_outcomes,
                    "weight_recommendations": report.weight_recommendations,
                    "overall_brier": report.overall_brier,
                }
                proposal = orch.adjuster.propose_weights(report_dict)
                if proposal is not None:
                    if proposal.auto_deploy:
                        deployed = orch.adjuster.deploy_weights(proposal)
                        if deployed:
                            logger.info(
                                f"Learning loop: AUTO-DEPLOYED weight proposal "
                                f"{proposal.proposal_id[:8]} — "
                                f"{len(proposal.weight_deltas)} changes"
                            )
                    else:
                        logger.info(
                            f"Learning loop: new proposal {proposal.proposal_id[:8]} "
                            f"queued for manual review "
                            f"(confidence={proposal.confidence_level}, "
                            f"changes={len(proposal.weight_deltas)})"
                        )
            except Exception as e:
                logger.warning(f"Learning loop proposal step failed: {e}")

            logger.info(
                f"Learning loop complete: report={report.report_id[:8]}, "
                f"brier={report.overall_brier:.4f}, "
                f"optimization={'ON' if report.optimization_ready else 'OFF'}"
            )

            # Closed-loop weight learning — this is what actually makes
            # the system recalibrate. The learning pass queries graded
            # outcomes per strategy, computes new weights via softmax
            # over (hit_rate + neg_brier + avg_pnl), clamps evolution
            # to 8% per cycle, and writes data/active_weights.json.
            # The SignalAggregator, SpecialistOrchestrator, and
            # EnsembleAI all read that file on every cycle — so the
            # next aggregation actually uses the new weights.
            try:
                from backend.learning.weights import run_learning_pass
                pass_result = run_learning_pass(self._decision_logger)
                if pass_result.get("updated"):
                    logger.info(
                        f"Learning loop: closed-loop weights DEPLOYED — "
                        f"{pass_result.get('reason')}"
                    )
                else:
                    logger.debug(
                        f"Learning loop: closed-loop weights NOT updated "
                        f"({pass_result.get('reason')})"
                    )
            except Exception as e:
                logger.warning(f"Learning loop closed-loop pass failed: {e}")
        except Exception as e:
            logger.error(f"Learning loop failed (non-fatal): {e}")

    async def run_binance_arb(self) -> None:
        """Scan for Polymarket-vs-Binance crypto price gaps."""
        if not getattr(settings.strategies, "binance_arb", True) or not self.state.markets:
            return
        try:
            intents = await self.binance_arb.evaluate_batch(self.state.markets)
            self._all_intents.extend(intents)
            for intent in intents:
                self.state.add_signal(intent)
            if intents:
                logger.info(f"Binance arb: {len(intents)} opportunities")
                for intent in intents[:3]:
                    await self.state.broadcast("signal", {
                        "strategy": "binance_arb",
                        "market_id": intent.market_id,
                        "side": intent.side.value,
                        "confidence": intent.confidence,
                    })
        except Exception as e:
            logger.error(f"Binance arb failed: {e}")

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
                # In live mode, place a real SELL order before closing in state
                if not self.state.paper_trading:
                    try:
                        sell_result = await self.executor.live_sell(signal.position)
                        if not sell_result.success:
                            logger.warning(
                                f"EXIT SELL FAILED: {signal.position.question[:30]} — "
                                f"{sell_result.error}. Keeping position open."
                            )
                            continue  # Don't close in state if sell failed
                    except Exception as e:
                        logger.error(f"EXIT SELL exception: {e}. Keeping position open.")
                        continue

                closed = self.state.close_position(signal.position.market_id)
                if closed:
                    self.exit_manager.clear_tracking(closed.market_id)
                    # Notify A-S so its inventory decrements when one
                    # of its positions closes.
                    if closed.strategy == StrategyName.AVELLANEDA:
                        try:
                            self.avellaneda.record_close(
                                market_id=closed.market_id,
                                side=closed.side,
                                size=closed.size_usdc,
                            )
                        except Exception as e:
                            logger.debug(f"A-S record_close failed: {e}")
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
                            # Annualize: equity snapshots every 15s = 5760/day.
                            import math as _m
                            points_per_year = 5760 * 365
                            self.state.sharpe_ratio = round(
                                (mean_ret / max(std_ret, 0.0001)) * _m.sqrt(points_per_year), 3
                            )
                except Exception:
                    pass

            # Win rate from DuckDB trade stats (real resolved trades).
            try:
                stats = self.duckdb.query(
                    "SELECT COUNT(CASE WHEN pnl > 0 THEN 1 END) as wins, "
                    "COUNT(CASE WHEN pnl < 0 THEN 1 END) as losses, "
                    "COALESCE(SUM(pnl), 0) as total_pnl "
                    "FROM trades WHERE trade_type = 'close'"
                )
                if stats:
                    w = int(stats[0].get("wins", 0) or 0)
                    l = int(stats[0].get("losses", 0) or 0)
                    if w + l > 0:
                        self.state.win_rate = w / (w + l)
                    # Sync realized_pnl from DuckDB — the in-memory counter
                    # drifts from rounding across hundreds of open/close cycles.
                    # Only sync P&L, NOT balance — balance is tracked by
                    # add_position/close_position and recalculating it here
                    # every 15s fights with those updates and breaks Sharpe.
                    db_pnl = float(stats[0].get("total_pnl", 0) or 0)
                    if db_pnl != 0:
                        self.state.realized_pnl = db_pnl
            except Exception:
                pass

            # In live mode, periodically sync balance from on-chain wallet
            # so we have ground truth on actual available funds.
            if not self.state.paper_trading:
                try:
                    from backend.data_layer.clob_auth import CLOBAuth
                    auth = getattr(self, "_clob_auth", None)
                    if auth is None:
                        auth = CLOBAuth()
                        self._clob_auth = auth
                    on_chain = await auth.get_balance()
                    if on_chain is not None and on_chain > 0:
                        # Only log if there's a significant drift
                        drift = abs(self.state.balance - on_chain)
                        if drift > 1.0:
                            logger.info(
                                f"Wallet sync: on-chain=${on_chain:.2f} "
                                f"vs in-memory=${self.state.balance:.2f} "
                                f"(drift=${drift:.2f})"
                            )
                            self.state.balance = on_chain
                except Exception as e:
                    logger.debug(f"Wallet balance sync skipped: {e}")

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

                    # Feed wallet pattern analyzer for smart money signal
                    try:
                        self.wallet_analyzer.record_activity(
                            wallet=target_addr,
                            display_name=whale_entry["display_name"],
                            market_id=whale_entry["market_id"],
                            side=whale_entry["side"],
                            size_usdc=whale_entry["size_usdc"],
                        )
                    except Exception:
                        pass

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
                self.state.balance = equity.get("balance", settings.trading.starting_capital)
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

            # Restore positions — append directly WITHOUT calling
            # add_position() which would decrement balance a second time.
            # The balance was already deducted when these positions were
            # originally opened; the SQLite snapshot stores the post-
            # deduction balance. Using add_position here would double-count.
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
                self.state.positions.append(pos)
                self.state.total_exposure += pos.size_usdc
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

        Populates market_theme, regime_label, and edge_classification
        so the retrospective analyzer's per-group breakdowns have
        real data to work with (previously they were all empty strings,
        collapsing every decision into one uncategorized bucket).
        """
        if self._decision_logger is None:
            return ""
        try:
            from prediction_intelligence.logger import DecisionRecord
            from backend.quant.regime import classify as classify_regime

            # Look up the live MarketState so we can classify its regime + theme
            market = next(
                (m for m in self.state.markets if m.market_id == intent.market_id),
                None,
            )
            regime_label = ""
            market_theme = ""
            if market is not None:
                try:
                    regime_label = classify_regime(market).regime.value
                except Exception:
                    pass
                market_theme = (market.category or "")[:50]

            # Edge classification buckets based on Kelly-sized fraction
            # OrderIntent has no 'edge' field — compute from kl_divergence
            # or kelly_fraction so the learning loop gets real edge data.
            edge_est = abs(getattr(intent, "kl_divergence", 0.0) or 0.0)
            if edge_est == 0 and hasattr(intent, "kelly_fraction"):
                edge_est = abs(intent.kelly_fraction or 0.0)
            abs_edge = abs(edge_est)
            if abs_edge >= 0.15:
                edge_classification = "large"
            elif abs_edge >= 0.05:
                edge_classification = "medium"
            elif abs_edge > 0:
                edge_classification = "small"
            else:
                edge_classification = "unknown"

            record = DecisionRecord(
                market_id=intent.market_id,
                market_title=(intent.question or "")[:200],
                market_theme=market_theme,
                implied_probability=result.fill_price,
                fair_probability=getattr(intent, "fair_probability", 0.5) or 0.5,
                edge_estimate=edge_est,
                opportunity_score=getattr(intent, "confidence", 0.0) or 0.0,
                classification="LIVE" if not result.paper else "PAPER",
                edge_classification=edge_classification,
                regime_label=regime_label,
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
        # Atomic swap — capture current intents and reset for next cycle.
        # Prevents race where a strategy job appends between score() and
        # clear(), silently losing signals.
        intents_snapshot, self._all_intents = self._all_intents, []
        if not intents_snapshot:
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
            scored = self.aggregator.score(intents_snapshot)

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
                # Build a per-intent lookup of REAL current token prices
                # so paper fills reflect the actual book instead of the
                # strategy's intended limit price. For YES side pass
                # yes_price, for NO side pass no_price — whichever token
                # is actually being bought.
                from backend.strategies.base import Side
                market_by_id = {m.market_id: m for m in self.state.markets}
                per_intent_prices: dict[str, float] = {}
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

                        # Notify the Avellaneda-Stoikov MM so its
                        # inventory + VPIN trade buckets stay in sync
                        # with actual fills. Without this A-S thinks it's
                        # always flat and keeps quoting the same side.
                        if si.intent.strategy == StrategyName.AVELLANEDA:
                            try:
                                self.avellaneda.record_fill(
                                    market_id=si.intent.market_id,
                                    side=si.intent.side,
                                    price=result.fill_price,
                                    size=result.fill_size,
                                )
                            except Exception as e:
                                logger.debug(f"A-S record_fill failed: {e}")

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

            # Intents already swapped out at the top — no clear needed.
        except Exception as e:
            logger.error(f"Aggregate/execute failed: {e}")

    async def daily_reset(self) -> None:
        """Reset daily risk counters at midnight UTC."""
        self.risk.reset_daily()
        self.state.trades_today = 0

        # Compute daily PnL as today's increment (not cumulative).
        from datetime import datetime, timezone
        baseline = self.risk.state.daily_pnl_baseline or 0.0
        today_pnl = self.state.realized_pnl - baseline
        self.state.daily_pnl.append({
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "pnl": today_pnl,
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

        # Initialize the risk engine's daily-loss baseline from whatever
        # cumulative P&L was persisted. Without this, the first risk
        # check after startup would interpret all prior realized P&L as
        # today's loss and could lock trading permanently on resumption
        # from a drawn-down state.
        try:
            self.risk.state.daily_pnl_baseline = float(
                getattr(self.state, "realized_pnl", 0) or 0
            )
        except Exception:
            self.risk.state.daily_pnl_baseline = 0.0

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

        # AI ensemble model probabilities (every 10 min — reduced from 3 min
        # to cut API costs ~70%. Sonnet→Haiku + GPT-4o→4o-mini saves another
        # 90%. Total: ~$3-5/day instead of ~$77/day.
        self.scheduler.add_job(
            self.run_ensemble_probabilities,
            IntervalTrigger(minutes=10),
            id="ensemble_probabilities",
            name="AI ensemble probabilities",
            max_instances=2,
            coalesce=True,
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
            self.run_binance_arb,
            IntervalTrigger(seconds=15),
            id="binance_arb",
            name="Binance crypto arb",
        )
        self.scheduler.add_job(
            self.run_correlation_scanner,
            IntervalTrigger(seconds=60),
            id="correlation_scanner",
            name="Correlation arb scanner",
        )
        # A-S market maker is off by default — your friend's production data
        # and our own trade log (34 opens / 27 closes averaging ~$0.10 per
        # round trip) both confirm it's a money-losing / breakeven churn
        # strategy on Polymarket's current spreads. Only schedule when
        # explicitly enabled via STRATEGY_AVELLANEDA=true.
        if settings.strategies.avellaneda:
            self.scheduler.add_job(
                self.run_avellaneda_mm,
                IntervalTrigger(seconds=10),
                id="avellaneda_mm",
                name="A-S market maker",
            )
            logger.info("A-S market maker ENABLED via STRATEGY_AVELLANEDA=true")
        else:
            logger.info("A-S market maker DISABLED (STRATEGY_AVELLANEDA=false)")
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

        # Settlement watcher — closes positions at the REAL Gamma
        # resolution price (1.0 or 0.0) when markets resolve.
        self.scheduler.add_job(
            self.run_settlement_watcher,
            IntervalTrigger(seconds=60),
            id="settlement_watcher",
            name="Gamma settlement watcher",
        )

        # Retrospective analyzer — auto-runs the learning loop every
        # 10 minutes. The function itself short-circuits unless there
        # are ≥10 new scored outcomes since the last run, so this is
        # cheap in practice.
        self.scheduler.add_job(
            self.run_retrospective_analysis,
            IntervalTrigger(minutes=10),
            id="retrospective_analysis",
            name="Learning loop (retrospective analyzer)",
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
            f"binance_arb={getattr(settings.strategies, 'binance_arb', True)}, "
            f"theta={settings.strategies.theta}, "
            f"jet={settings.strategies.jet}, "
            f"copy={settings.strategies.copy}"
        )
        mm_label = "MM(10s)" if settings.strategies.avellaneda else "MM(OFF)"
        logger.info(
            f"Scheduled jobs: markets(45s), positions(15s), entropy(60s), "
            f"arb(15s), binance_arb(15s), {mm_label}, theta(5m), jet(60s), "
            f"wallet(30s), leaderboard(5m), aggregate(30s), "
            f"settlement_watcher(60s), pi_analysis(10m), "
            f"persist(2m), daily(midnight)"
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
