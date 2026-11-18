"""Google Places API (New) — Text Search.

One request returns up to 20 places *including* phone + website, so we never
need separate Place Details calls. Billing is per request (Enterprise SKU because
of phone/website/rating fields), so 3 pages = up to 60 businesses for 3 calls.
Terms note: Places content may be cached for limited time only; place_id can be
stored indefinitely. Re-run (refresh) instead of treating exports as permanent.
"""
from __future__ import annotations

import logging

from ..config import settings
from ..http import request
from ..models import RawLead, SearchQuery
from .base import Source

log = logging.getLogger(__name__)

URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ",".join([
    "places.id", "places.displayName", "places.formattedAddress",
    "places.internationalPhoneNumber", "places.nationalPhoneNumber", "places.websiteUri",
    "places.rating", "places.userRatingCount", "places.googleMapsUri",
    "places.primaryTypeDisplayName", "places.businessStatus", "nextPageToken",
])


class GooglePlacesSource(Source):
    name = "google_places"
    needs_key = True
    calls_made = 0   # shared across instances within a run (bill protection)

    def available(self) -> bool:
        return bool(settings.google_places_key)

    async def search(self, q: SearchQuery) -> list[RawLead]:
        out: list[RawLead] = []
        token: str | None = None
        text = f"{q.phrase} in {q.location_text}"
        while len(out) < q.limit:
            if GooglePlacesSource.calls_made >= settings.max_places_calls_per_run:
                log.warning("Places call cap reached (%s). Raise MAX_PLACES_CALLS_PER_RUN to allow more.",
                            settings.max_places_calls_per_run)
                break
            body = {"textQuery": text, "pageSize": 20, "regionCode": q.country, "languageCode": "en"}
            if token:
                body["pageToken"] = token
            r = await request(self.client, "POST", URL, json=body, headers={
                "X-Goog-Api-Key": settings.google_places_key,
                "X-Goog-FieldMask": FIELDS,
            })
            GooglePlacesSource.calls_made += 1
            if r.status_code != 200:
                log.error("Places API %s: %s", r.status_code, r.text[:300])
                break
            data = r.json()
            for p in data.get("places", []):
                if p.get("businessStatus") not in (None, "OPERATIONAL"):
                    continue
                out.append(RawLead(
                    name=(p.get("displayName") or {}).get("text", ""),
                    source=self.name, niche=q.niche, country=q.country, city=q.city,
                    address=p.get("formattedAddress"),
                    phone=p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber"),
                    website=p.get("websiteUri"),
                    rating=p.get("rating"), review_count=p.get("userRatingCount"),
                    category=(p.get("primaryTypeDisplayName") or {}).get("text"),
                    maps_url=p.get("googleMapsUri"), place_id=p.get("id"),
                ))
            token = data.get("nextPageToken")
            if not token:
                break
        return out[: q.limit]
