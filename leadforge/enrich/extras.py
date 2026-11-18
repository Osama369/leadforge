"""PageSpeed Insights (free Google API) and email domain (MX) verification."""
from __future__ import annotations

import asyncio
import logging

import dns.asyncresolver
import dns.exception
import httpx

from ..config import settings

log = logging.getLogger(__name__)

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
_mx_cache: dict[str, bool] = {}


async def pagespeed_mobile(client: httpx.AsyncClient, url: str) -> int | None:
    """Mobile performance score 0-100. Slow (10-40s per URL) — run concurrently."""
    params = {"url": url, "strategy": "mobile", "category": "performance"}
    if settings.pagespeed_key:
        params["key"] = settings.pagespeed_key
    try:
        r = await client.get(PSI_URL, params=params, timeout=90)
        if r.status_code != 200:
            log.debug("PSI %s for %s: %s", r.status_code, url, r.text[:200])
            return None
        score = r.json()["lighthouseResult"]["categories"]["performance"]["score"]
        return int(round(score * 100)) if score is not None else None
    except Exception as e:
        log.debug("PSI failed for %s: %s", url, e)
        return None


async def verify_email_domain(email: str) -> str:
    """'valid_mx' if the domain accepts mail, 'no_mx' if not, 'unknown' on DNS error.
    (We deliberately don't do SMTP RCPT probing: slow, often blocked, and gets IPs blacklisted.)"""
    domain = email.split("@")[-1].lower()
    if domain in _mx_cache:
        return "valid_mx" if _mx_cache[domain] else "no_mx"
    try:
        ans = await dns.asyncresolver.resolve(domain, "MX", lifetime=8)
        ok = len(ans) > 0
    except (dns.asyncresolver.NXDOMAIN, dns.asyncresolver.NoAnswer):
        ok = False
    except (dns.exception.DNSException, asyncio.TimeoutError):
        return "unknown"
    _mx_cache[domain] = ok
    return "valid_mx" if ok else "no_mx"
