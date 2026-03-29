"""
News and context fetcher for the AI ensemble.

Sources:
  - NewsAPI (requires key)
  - RSS feeds (free, no key)
  - Basic web scraping fallback
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    published: Optional[datetime] = None
    summary: str = ""
    relevance_tags: list[str] = field(default_factory=list)


class NewsFetcher:
    """Fetch recent news for market context."""

    def __init__(self, newsapi_key: str = "") -> None:
        self.newsapi_key = newsapi_key
        self._session: Optional[aiohttp.ClientSession] = None
        from backend.data_layer.rate_limiter import NEWS_LIMITER
        self._limiter = NEWS_LIMITER

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            from backend.data_layer.proxy import get_proxied_session
            self._session = get_proxied_session()
        return self._session

    async def search_news(self, query: str, limit: int = 5) -> list[NewsItem]:
        """Search NewsAPI for relevant articles."""
        if not self.newsapi_key:
            return []
        await self._limiter.acquire()
        session = await self._get_session()
        try:
            async with session.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "pageSize": limit,
                    "sortBy": "publishedAt",
                    "apiKey": self.newsapi_key,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                articles = data.get("articles", [])
                return [
                    NewsItem(
                        title=a.get("title", ""),
                        source=a.get("source", {}).get("name", ""),
                        url=a.get("url", ""),
                        summary=a.get("description", ""),
                    )
                    for a in articles
                ]
        except Exception as e:
            logger.error(f"NewsAPI search failed: {e}")
            return []

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
