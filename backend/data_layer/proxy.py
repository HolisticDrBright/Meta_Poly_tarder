"""
Optional proxy aiohttp session factory.

When PROXY_URL is empty (default): get_proxied_session() returns a plain
aiohttp.ClientSession() with no proxy, and get_proxy_url() returns None.
All callers pass proxy=None to aiohttp which means direct connection.

When PROXY_URL is set (opt-in):
  http:// → aiohttp native proxy support (proxy kwarg per-request).
  socks5:// → aiohttp-socks ProxyConnector (requires pip install aiohttp-socks).
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


def _is_http_proxy() -> bool:
    """Check if the configured proxy is HTTP (not SOCKS5)."""
    return _proxy_url is not None and _proxy_url.startswith("http")


def get_proxied_session(**kwargs) -> aiohttp.ClientSession:
    """
    Create an aiohttp session that routes through the proxy.

    HTTP proxy: uses aiohttp's native proxy support (no extra package needed).
    SOCKS5 proxy: uses aiohttp-socks ProxyConnector.
    No proxy: direct connection.
    """
    if _proxy_url and _proxy_url.startswith("socks"):
        # SOCKS5 proxy — needs aiohttp-socks
        try:
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(_proxy_url)
            return aiohttp.ClientSession(
                connector=connector,
                trust_env=False,
                **kwargs,
            )
        except ImportError:
            if _vpn_required:
                raise RuntimeError(
                    "VPN_REQUIRED=true with SOCKS5 proxy but aiohttp-socks not installed. "
                    "Run: pip install aiohttp-socks"
                )
            logger.warning("aiohttp-socks not installed — falling back to direct")

    # HTTP proxy or no proxy — aiohttp handles HTTP proxies natively
    # The proxy URL is passed per-request, not at session level
    return aiohttp.ClientSession(
        trust_env=False,
        **kwargs,
    )


def get_proxy_url() -> Optional[str]:
    """Get the current proxy URL for use in per-request proxy parameter."""
    if _proxy_url and _proxy_url.startswith("http"):
        return _proxy_url
    return None
