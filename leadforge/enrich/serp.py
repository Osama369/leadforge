"""Google organic rank check via Serper /search.

One call per (niche, city) query, shared by every lead in that query. We look for
each lead's domain in the top 20 organic results, and remember the best-ranking
*business* competitor. That powers the strongest SEO pitch there is:
"you're not on page 1 for 'dentist houston', but <competitor> is #1".
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings
from ..geo import COUNTRIES
from ..http import request
from ..models import Lead
from ..normalize import NON_BUSINESS_DOMAINS
from .website import registered_domain

log = logging.getLogger(__name__)

URL = "https://google.serper.dev/search"
# Big directories/aggregators that rank for everything: not "competitors"
AGGREGATORS = NON_BUSINESS_DOMAINS + (
    "yelp.", "angi.com", "homeadvisor.com", "thumbtack.com", "healthgrades.com", "zocdoc.com",
    "bbb.org", "mapquest.com", "nextdoor.com", "reddit.com", "wikipedia.org", "expertise.com",
    "threebestrated", "findlaw.com", "avvo.com", "justia.com", "lawyers.com", "opencare.com",
    "trustpilot.com", "checkatrade.com", "bayut.com", "dubizzle.com", "propertyfinder.",
    "nhs.uk", "webmd.com", "realtor.com", "zillow.com", "indeed.com", "glassdoor.",
)


class SerpChecker:
    calls_made = 0

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._cache: dict[tuple[str, str], list[str]] = {}

    def available(self) -> bool:
        return bool(settings.serper_key)

    async def organic_domains(self, query: str, country: str) -> list[str]:
        key = (query.lower(), country)
        if key in self._cache:
            return self._cache[key]
        if SerpChecker.calls_made >= settings.max_serper_calls_per_run:
            log.warning("Serper call cap reached; skipping rank check for '%s'", query)
            return []
        r = await request(self.client, "POST", URL, headers={"X-API-KEY": settings.serper_key},
                          json={"q": query, "gl": COUNTRIES[country]["gl"], "hl": "en", "num": 20})
        SerpChecker.calls_made += 1
        domains: list[str] = []
        if r.status_code == 200:
            for item in r.json().get("organic", []):
                d = registered_domain(item.get("link"))
                if d and d not in domains:
                    domains.append(d)
        else:
            log.error("Serper search %s: %s", r.status_code, r.text[:200])
        self._cache[key] = domains
        return domains

    async def check(self, leads: list[Lead]) -> int:
        """Set serp_* fields on leads that have a website. Returns number of searches made."""
        groups: dict[tuple[str, str, str], list[Lead]] = {}
        for l in leads:
            if l.domain:
                groups.setdefault((l.niche, (l.city or "").split(",")[0].strip(), l.country), []).append(l)
        before = SerpChecker.calls_made
        for (niche, city, country), group in groups.items():
            query = f"{niche} {city}".strip()
            ranked = await self.organic_domains(query, country)
            if not ranked:
                continue
            businesses = [d for d in ranked if not any(a in d for a in AGGREGATORS)]
            for l in group:
                l.serp_checked, l.serp_query = True, query
                pos = next((i + 1 for i, d in enumerate(ranked) if d == l.domain or d.endswith("." + l.domain)), None)
                l.serp_rank = pos
                l.serp_leader = next((d for d in businesses if d != l.domain), None)
        return SerpChecker.calls_made - before
