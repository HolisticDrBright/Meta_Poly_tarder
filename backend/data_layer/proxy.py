"""
Proxy-aware aiohttp session factory.

All HTTP clients in the system should use get_proxied_session()
instead of creating raw aiohttp.ClientSession(). This ensures
all traffic goes through the SOCKS5 proxy when VPN_REQUIRED=true.

If aiohttp-socks is not installed or PROXY_URL is not set,
falls back to a direct connection.
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_proxy_url: Optional[str] = None
_vpn_required: bool = False


def configure_proxy(proxy_url: str = "", vpn_required: bool = False) -> None:
    """Call once at startup to set the global proxy config."""
    global _proxy_url, _vpn_required
    _proxy_url = proxy_url if proxy_url else None
    _vpn_required = vpn_required
    if _proxy_url:
        logger.info(f"Proxy configured: {_proxy_url}")
    elif _vpn_required:
        logger.warning("VPN_REQUIRED=true but no PROXY_URL set!")


def get_connector() -> aiohttp.BaseConnector:
    """
    Get an aiohttp connector that routes through the proxy if configured.

    Returns a ProxyConnector (aiohttp-socks) when proxy is set,
    or a regular TCPConnector as fallback.
    """
    if _proxy_url:
        try:
            from aiohttp_socks import ProxyConnector
            return ProxyConnector.from_url(_proxy_url)
        except ImportError:
            if _vpn_required:
                raise RuntimeError(
                    "VPN_REQUIRED=true but aiohttp-socks is not installed. "
                    "Run: pip install aiohttp-socks"
                )
            logger.warning("aiohttp-socks not installed — using direct connection")

    return aiohttp.TCPConnector()


def get_proxied_session(**kwargs) -> aiohttp.ClientSession:
    """
    Create an aiohttp session that routes through the proxy.

    Usage:
        session = get_proxied_session()
        async with session.get(url) as resp:
            ...
        await session.close()

    IMPORTANT: trust_env=False prevents DNS leak through system proxy.
    """
    connector = get_connector()
    return aiohttp.ClientSession(
        connector=connector,
        trust_env=False,  # Critical: prevents DNS leak
        **kwargs,
    )
