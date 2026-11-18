"""Cleaning + de-duplication across sources."""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

import phonenumbers

from .enrich.website import registered_domain
from .models import Lead, RawLead

# Domains that are directories / socials, not the business's own site
NON_BUSINESS_DOMAINS = (
    "facebook.com", "instagram.com", "yelp.com", "yellowpages.com", "yell.com", "linkedin.com",
    "google.com", "goo.gl", "business.site", "tripadvisor.", "booking.com", "linktr.ee",
    "yellowpages-uae.com", "twitter.com", "x.com", "tiktok.com", "wa.me", "whatsapp.com",
)


def normalize_phone(raw: str | None, country: str) -> str | None:
    if not raw:
        return None
    try:
        p = phonenumbers.parse(raw, country or None)
        if phonenumbers.is_possible_number(p):
            return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    digits = re.sub(r"[^\d+]", "", raw)
    return digits or None


def normalize_website(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    dom = registered_domain(url)
    if not dom or any(d in dom for d in NON_BUSINESS_DOMAINS):
        return None
    return url.split("?")[0].split("#")[0]


def _name_key(name: str) -> str:
    n = name.lower()
    n = re.sub(r"\b(llc|inc|ltd|limited|co|corp|the|pllc|dds|pc|fze|fzco|llp)\b", "", n)
    return re.sub(r"[^a-z0-9]", "", n)


def lead_key(name: str, domain: str | None, phone: str | None, place_id: str | None) -> str:
    basis = place_id or (f"d:{domain}" if domain else None) or (f"p:{phone}" if phone else None) or f"n:{_name_key(name)}"
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def to_lead(r: RawLead) -> Lead:
    phone = normalize_phone(r.phone, r.country)
    site = normalize_website(r.website)
    dom = registered_domain(site)
    return Lead(
        key=lead_key(r.name, dom, phone, r.place_id), name=r.name.strip(), niche=r.niche,
        country=r.country, city=r.city, address=r.address, phone=phone, website=site, domain=dom,
        rating=r.rating, review_count=r.review_count, category=r.category, maps_url=r.maps_url,
        place_id=r.place_id, sources=[r.source],
    )


def _merge(into: Lead, other: Lead) -> None:
    for f in ("address", "phone", "website", "domain", "rating", "review_count", "category", "maps_url", "place_id"):
        if getattr(into, f) in (None, "") and getattr(other, f) not in (None, ""):
            setattr(into, f, getattr(other, f))
    # prefer the higher review count (usually the Google one)
    if (other.review_count or 0) > (into.review_count or 0):
        into.review_count, into.rating = other.review_count, other.rating or into.rating
    for s in other.sources:
        if s not in into.sources:
            into.sources.append(s)


def dedupe(raw: list[RawLead]) -> list[Lead]:
    """Merge the same business found by several sources (domain / phone / fuzzy name+city)."""
    leads: list[Lead] = []
    by_domain: dict[str, Lead] = {}
    by_phone: dict[str, Lead] = {}
    by_place: dict[str, Lead] = {}
    for r in raw:
        if not r.name:
            continue
        l = to_lead(r)
        match = (by_place.get(l.place_id) if l.place_id else None) \
            or (by_domain.get(l.domain) if l.domain else None) \
            or (by_phone.get(l.phone) if l.phone else None)
        if not match:
            nk = _name_key(l.name)
            for cand in leads:
                if cand.city == l.city and SequenceMatcher(None, _name_key(cand.name), nk).ratio() > 0.9:
                    match = cand
                    break
        if match:
            _merge(match, l)
            l = match
        else:
            leads.append(l)
        if l.place_id:
            by_place[l.place_id] = l
        if l.domain:
            by_domain[l.domain] = l
        if l.phone:
            by_phone[l.phone] = l
    return leads
