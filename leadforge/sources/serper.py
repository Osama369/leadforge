"""Serper.dev — Google Maps/Places results via a cheap SERP API.
2,500 free queries on signup; good backup/complement to the official Places API."""
from __future__ import annotations

import logging

from ..config import settings
from ..geo import COUNTRIES
from ..http import request
from ..models import RawLead, SearchQuery
from .base import Source

log = logging.getLogger(__name__)

URL = "https://google.serper.dev/places"


class SerperPlacesSource(Source):
    name = "serper_places"
    needs_key = True
    calls_made = 0

    def available(self) -> bool:
        return bool(settings.serper_key)

    async def search(self, q: SearchQuery) -> list[RawLead]:
        out: list[RawLead] = []
        page = 1
        while len(out) < q.limit and page <= 5:
            if SerperPlacesSource.calls_made >= settings.max_serper_calls_per_run:
                log.warning("Serper call cap reached (%s).", settings.max_serper_calls_per_run)
                break
            body = {"q": f"{q.phrase} in {q.location_text}",
                    "gl": COUNTRIES[q.country]["gl"], "hl": "en", "page": page}
            r = await request(self.client, "POST", URL, json=body,
                              headers={"X-API-KEY": settings.serper_key})
            SerperPlacesSource.calls_made += 1
            if r.status_code != 200:
                log.error("Serper %s: %s", r.status_code, r.text[:300])
                break
            places = r.json().get("places", [])
            if not places:
                break
            for p in places:
                cid = p.get("cid")
                out.append(RawLead(
                    name=p.get("title", ""), source=self.name, niche=q.niche,
                    country=q.country, city=q.city, address=p.get("address"),
                    phone=p.get("phoneNumber"), website=p.get("website"),
                    rating=p.get("rating"), review_count=p.get("ratingCount"),
                    category=p.get("category"),
                    maps_url=f"https://maps.google.com/?cid={cid}" if cid else None,
                    extra={"cid": cid} if cid else {},
                ))
            page += 1
        return out[: q.limit]
