"""Mocked API responses for Google Places (New) and Serper, so parsing is verified without keys."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SERPER_API_KEY", "test")

import httpx

from leadforge.models import SearchQuery
from leadforge.sources.google_places import GooglePlacesSource
from leadforge.sources.serper import SerperPlacesSource

PLACES = {"places": [{"id": "ChIJ1", "displayName": {"text": "Smile Care Dental"},
                      "formattedAddress": "1 Main St, Houston, TX", "internationalPhoneNumber": "+1 713-555-0142",
                      "websiteUri": "https://smilecare.test/", "rating": 4.7, "userRatingCount": 212,
                      "googleMapsUri": "https://maps.google.com/?cid=1", "businessStatus": "OPERATIONAL",
                      "primaryTypeDisplayName": {"text": "Dentist"}},
                     {"id": "ChIJ2", "displayName": {"text": "Closed Dental"}, "businessStatus": "CLOSED_PERMANENTLY"}]}
SERPER = {"places": [{"title": "Bright Dental", "address": "2 Oak St", "rating": 4.9, "ratingCount": 88,
                      "category": "Dentist", "phoneNumber": "(713) 555-0199", "website": "https://bright.test", "cid": "42"}]}


def handler(req: httpx.Request):
    body = json.loads(req.content or b"{}")
    if req.url.host == "places.googleapis.com":
        assert req.headers["X-Goog-FieldMask"].startswith("places.id")
        assert body["textQuery"] == "dentist in Houston, United States"
        return httpx.Response(200, json=PLACES)
    if req.url.host == "google.serper.dev":
        return httpx.Response(200, json=SERPER if body.get("page") == 1 else {"places": []})
    return httpx.Response(404)


def test_sources():
    q = SearchQuery(niche="dentist", country="US", city="Houston", limit=60)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await GooglePlacesSource(c).search(q), await SerperPlacesSource(c).search(q)
    g, s = asyncio.run(go())
    assert len(g) == 1 and g[0].website == "https://smilecare.test/" and g[0].review_count == 212
    assert g[0].place_id == "ChIJ1" and g[0].category == "Dentist"
    assert len(s) == 1 and s[0].phone == "(713) 555-0199" and s[0].maps_url.endswith("cid=42")


if __name__ == "__main__":
    test_sources(); print("PASS test_sources")


def test_serp_rank_and_seo_pitch():
    from leadforge.enrich.serp import SerpChecker
    from leadforge.geo import expand_niche
    from leadforge.models import Lead
    from leadforge.pitch import template_pitch
    from leadforge.scoring import score_lead

    organic = {"organic": [{"link": "https://www.yelp.com/x"}, {"link": "https://toprival.test/"},
                           {"link": "https://second.test"}, {"link": "https://www.mine.test/about"}]}

    def h(req):
        assert json.loads(req.content)["q"] == "dentist Houston"
        return httpx.Response(200, json=organic)

    mine = Lead(key="a", name="Mine", niche="dentist", country="US", city="Houston, TX",
                phone="+17135550100", website="https://mine.test", domain="mine.test", sources=["google_places"])
    absent = Lead(key="b", name="Absent", niche="dentist", country="US", city="Houston, TX",
                  phone="+17135550101", website="https://absent.test", domain="absent.test", sources=["google_places"])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            return await SerpChecker(c).check([mine, absent])
    assert asyncio.run(go()) == 1                     # one search shared by both leads
    assert mine.serp_rank == 4 and absent.serp_rank is None
    assert absent.serp_leader == "toprival.test"     # yelp skipped as aggregator
    score_lead(absent, ["seo"])
    assert any("page 1" in r for r in absent.scores["seo"].reasons)
    assert "toprival.test" in template_pitch(absent)
    assert expand_niche("Dentist")[:2] == ["dentist", "dental clinic"]


if __name__ == "__main__":
    test_serp_rank_and_seo_pitch(); print("PASS test_serp_rank_and_seo_pitch")
