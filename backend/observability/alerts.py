"""
Alert system — Telegram and Discord notifications.

Sends alerts for:
  - Trade executions
  - Signal triggers (entropy, jet, whale)
  - Risk limit warnings
  - System errors
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class TelegramAlert:
    """Send alerts via Telegram bot."""

    API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured Telegram chat."""
        if not self.enabled:
            logger.debug(f"Telegram disabled — would send: {message[:100]}")
            return False

        session = await self._get_session()
        url = self.API.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }
        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return True
                logger.warning(f"Telegram API error: {resp.status}")
                return False
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    async def trade_alert(
        self, strategy: str, side: str, market: str, size: float, price: float
    ) -> None:
        msg = (
            f"<b>TRADE</b> [{strategy}]\n"
            f"{side} ${size:.2f} @ {price:.4f}\n"
            f"<i>{market[:80]}</i>"
        )
        await self.send(msg)

    async def signal_alert(self, strategy: str, market: str, details: str) -> None:
        msg = (
            f"<b>SIGNAL</b> [{strategy}]\n"
            f"<i>{market[:80]}</i>\n"
            f"{details}"
        )
        await self.send(msg)

    async def risk_alert(self, message: str) -> None:
        msg = f"<b>RISK WARNING</b>\n{message}"
        await self.send(msg)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
