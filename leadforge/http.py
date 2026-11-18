"""Shared async HTTP client with retries and per-host politeness."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from urllib.parse import urlparse

import httpx

from .config import settings

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


def make_client(*, use_proxy: bool = False, timeout: int | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout or settings.http_timeout),
        follow_redirects=True,
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        proxy=settings.proxy_url if (use_proxy and settings.proxy_url) else None,
        limits=httpx.Limits(max_connections=settings.concurrency * 2),
        verify=True,
    )


class HostThrottle:
    """Ensures a minimum delay between requests to the same host (directories)."""

    def __init__(self, delay_ms: int):
        self.delay = delay_ms / 1000
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            elapsed = time.monotonic() - self._last.get(host, 0)
            jitter = random.uniform(0, self.delay * 0.4)
            if elapsed < self.delay:
                await asyncio.sleep(self.delay - elapsed + jitter)
            self._last[host] = time.monotonic()


async def request(client: httpx.AsyncClient, method: str, url: str, *, retries: int = 3,
                  throttle: HostThrottle | None = None, **kw) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(retries):
        if throttle:
            await throttle.wait(url)
        try:
            r = await client.request(method, url, **kw)
            if r.status_code in RETRY_STATUS and attempt < retries - 1:
                wait = float(r.headers.get("retry-after", 0) or 0) or (2 ** attempt + random.random())
                log.debug("retry %s %s -> %s (wait %.1fs)", method, url, r.status_code, wait)
                await asyncio.sleep(min(wait, 30))
                continue
            return r
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last_exc = e
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt + random.random())
    raise last_exc or RuntimeError(f"request failed: {url}")
