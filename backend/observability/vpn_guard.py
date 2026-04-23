"""
VPN Health Guard — optional proxy connectivity check.

When VPN_REQUIRED=False (default): all methods return healthy immediately
and no network checks are performed. The app starts and runs without any
proxy configured.

When VPN_REQUIRED=True (opt-in): verifies that PROXY_URL is reachable on
startup and monitors connectivity every check_interval seconds at runtime.
Users are responsible for configuring their own VPN or proxy at the OS
level — this guard only checks that the configured proxy responds.

Flow (VPN_REQUIRED=True only):
  1. On startup: check IP via proxy → must not be VPS IP
  2. If check fails: log error, return False (caller decides whether to exit)
  3. Every 5 min: re-check proxy connectivity
  4. On drop: retry 5x at 30s, then invoke on_drop_callback
  5. If no recovery: enter safe halt mode
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class VPNStatus:
    healthy: bool
    ip: str = ""
    country: str = ""
    org: str = ""
    error: str = ""


class VPNGuard:
    """Optional proxy connectivity guard. No-op when VPN_REQUIRED=False."""

    def __init__(
        self,
        proxy_url: str = "",
        check_url: str = "https://ipinfo.io/json",
        required: bool = False,
        check_interval: int = 300,
        vps_ip: str = "",
    ) -> None:
        self.proxy_url = proxy_url
        self.check_url = check_url
        self.required = required
        self.check_interval = check_interval
        self.vps_ip = vps_ip
        self._healthy = False
        self._last_status: Optional[VPNStatus] = None
        self._monitor_task: Optional[asyncio.Task] = None

    @property
    def healthy(self) -> bool:
        if not self.required:
            return True
        return self._healthy

    @property
    def last_status(self) -> Optional[VPNStatus]:
        return self._last_status

    async def check(self) -> VPNStatus:
        """Check VPN status by querying IP through the proxy."""
        if not self.proxy_url:
            if self.required:
                status = VPNStatus(healthy=False, error="No PROXY_URL configured but VPN_REQUIRED=true")
                self._last_status = status
                self._healthy = False
                return status
            status = VPNStatus(healthy=True, error="VPN not required")
            self._last_status = status
            self._healthy = True
            return status

        try:
            # Support both HTTP and SOCKS5 proxy URLs
            proxy_kwarg = {}
            connector = aiohttp.TCPConnector()

            if self.proxy_url.startswith("socks"):
                try:
                    from aiohttp_socks import ProxyConnector
                    connector = ProxyConnector.from_url(self.proxy_url)
                except ImportError:
                    logger.warning("aiohttp-socks not installed for SOCKS5 proxy")
            elif self.proxy_url.startswith("http"):
                # HTTP proxy — pass per-request
                proxy_kwarg["proxy"] = self.proxy_url

            async with aiohttp.ClientSession(
                connector=connector,
                trust_env=False,
            ) as session:
                async with session.get(
                    self.check_url,
                    timeout=aiohttp.ClientTimeout(total=10),
                    **proxy_kwarg,
                ) as resp:
                    data = await resp.json()

            ip = data.get("ip", "")
            country = data.get("country", "")
            org = data.get("org", "")

            if self.vps_ip and ip == self.vps_ip:
                status = VPNStatus(
                    healthy=False, ip=ip, country=country, org=org,
                    error="VPN leaking — IP matches VPS real IP",
                )
                self._healthy = False
                self._last_status = status
                logger.error(f"VPN LEAK DETECTED: IP={ip} matches VPS IP")
                return status

            status = VPNStatus(healthy=True, ip=ip, country=country, org=org)
            self._healthy = True
            self._last_status = status
            logger.info(f"VPN OK: IP={ip}, country={country}, org={org}")
            return status

        except Exception as e:
            status = VPNStatus(healthy=False, error=f"VPN check failed: {e}")
            self._healthy = False
            self._last_status = status
            logger.error(f"VPN check exception: {e}")
            return status

    async def startup_gate(self) -> bool:
        """
        Run on startup. Returns True if safe to trade, False if not.

        If VPN_REQUIRED and check fails: the bot should exit.
        """
        if not self.required:
            logger.info("VPN not required — skipping startup gate")
            return True

        logger.info("VPN startup gate: checking proxy connection...")
        status = await self.check()

        if status.healthy:
            logger.info(
                f"VPN startup gate PASSED: masked IP={status.ip}, "
                f"country={status.country}"
            )
            return True

        logger.critical(f"VPN startup gate FAILED: {status.error}")
        return False

    async def _monitor_loop(self, on_drop_callback=None) -> None:
        """Background loop that checks VPN every check_interval seconds."""
        while True:
            await asyncio.sleep(self.check_interval)
            status = await self.check()

            if not status.healthy and on_drop_callback:
                logger.critical(f"VPN DROP DETECTED: {status.error}")

                # Retry 5 times at 30s intervals
                recovered = False
                for attempt in range(1, 6):
                    logger.warning(f"VPN recovery attempt {attempt}/5...")
                    await asyncio.sleep(30)
                    retry = await self.check()
                    if retry.healthy:
                        logger.info(f"VPN recovered on attempt {attempt}: IP={retry.ip}")
                        recovered = True
                        break

                if not recovered:
                    logger.critical("VPN UNRECOVERABLE — entering safe halt mode")
                    await on_drop_callback()

    def start_monitor(self, on_drop_callback=None) -> None:
        """Start the background VPN monitor."""
        if not self.required:
            return

        async def _start():
            self._monitor_task = asyncio.create_task(
                self._monitor_loop(on_drop_callback)
            )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._monitor_loop(on_drop_callback))
        except RuntimeError:
            # No running loop — will be started later
            self._monitor_task = None

    async def stop_monitor(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
