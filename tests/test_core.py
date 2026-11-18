"""Offline tests: scoring, dedupe, HTML auditing. Run: python -m pytest -q  (or python tests/test_core.py)"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from leadforge.enrich.website import audit_website
from leadforge.models import Lead, RawLead, WebsiteAudit
from leadforge.normalize import dedupe
from leadforge.scoring import score_lead

OLD_SITE = """<html><head><title>Smile</title></head><body>
<p>Call us <a href="tel:+17135550142">713-555-0142</a> or email
<a href="mailto:info@smilecare.test">info@smilecare.test</a></p>
<a href="https://www.facebook.com/smilecarehou">fb</a>
<a href="https://wa.me/17135550142">WhatsApp</a>
<div>&copy; 2019 Smile Care</div>
<img src="/wp-content/uploads/logo.png"></body></html>"""

MODERN_SITE = """<html><head><title>Bright Dental Houston | Family &amp; Cosmetic Dentist</title>
<meta name="viewport" content="width=device-width"><meta name="description" content="Top rated dentist">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-ABC123"></script>
<script>gtag('config','AW-123456789');</script>
<script src="https://connect.facebook.net/en_US/fbevents.js"></script><script>fbq('init','1')</script>
<script src="https://widget.intercom.io/widget/x"></script>
<script type="application/ld+json">{"@type":"Dentist","name":"Bright"}</script>
</head><body><h1>Bright Dental</h1><a href="https://calendly.com/bright">Book</a>
<a href="mailto:hello@brightdental.test">mail</a> """ + "word " * 400 + "</body></html>"


def mock_client(html_by_host):
    def handler(req):
        body = html_by_host.get(req.url.host)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"}) if body else httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_audit_and_score():
    async def go():
        async with mock_client({"smilecare.test": OLD_SITE, "brightdental.test": MODERN_SITE}) as c:
            return await audit_website(c, "https://smilecare.test"), await audit_website(c, "https://brightdental.test")
    old, new = asyncio.run(go())
    assert old.emails == ["info@smilecare.test"] and old.cms == "WordPress"
    assert "whatsapp" in old.socials and not old.has_viewport and old.copyright_year == 2019
    assert new.has_google_ads_tag and new.has_meta_pixel and new.has_ga4
    assert new.has_chat_widget and new.chat_widget == "Intercom" and new.has_booking and new.has_local_schema

    def lead(audit, email, reviews):
        return Lead(key="k", name="X", niche="dentist", country="US", city="Houston", phone="+17135550142",
                    website="https://x.test", rating=4.7, review_count=reviews, sources=["google_places"],
                    audit=audit, email=email)
    weak = score_lead(lead(old, old.emails[0], 180))
    strong = score_lead(lead(new, new.emails[0], 180))
    print("\nOLD site scores:", {k: v.score for k, v in weak.scores.items()})
    print("MODERN site scores:", {k: v.score for k, v in strong.scores.items()})
    assert weak.scores["web"].tier == "hot" and weak.scores["chatbot"].tier == "hot"
    assert strong.scores["chatbot"].score < 40 and strong.scores["ads"].score < weak.scores["ads"].score


def test_no_website_and_contact_cap():
    l = score_lead(Lead(key="k", name="Y", niche="plumber", country="US", phone="+12125550100",
                        sources=["google_places"]))
    assert l.best_service == "web" and l.scores["web"].tier == "hot"
    unknown = score_lead(Lead(key="k", name="Z", niche="dentist", country="AE", phone="+97143575783",
                              sources=["yellowpages_uae"]))
    assert unknown.scores["web"].score < l.scores["web"].score      # directory w/o website != no website
    nocontact = score_lead(Lead(key="k", name="W", niche="plumber", country="US", sources=["google_places"]))
    assert nocontact.best_score <= 30


def test_dedupe_merges_sources():
    raw = [RawLead(name="Smile Care Dental", source="google_places", country="US", city="Houston",
                   phone="(713) 555-0142", website="https://www.smilecare.test/", review_count=210, place_id="abc"),
           RawLead(name="Smile Care Dental LLC", source="yellowpages_us", country="US", city="Houston",
                   phone="713-555-0142", website=None)]
    out = dedupe(raw)
    assert len(out) == 1 and set(out[0].sources) == {"google_places", "yellowpages_us"}
    assert out[0].phone == "+17135550142" and out[0].domain == "smilecare.test"


if __name__ == "__main__":
    for t in (test_audit_and_score, test_no_website_and_contact_cap, test_dedupe_merges_sources):
        t(); print("PASS", t.__name__)
