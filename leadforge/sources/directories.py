"""Business-directory scrapers (no API key needed).

Each directory is described by a DirectorySpec (URL pattern + CSS selectors),
so fixing a site that changed its HTML = edit selectors here, nothing else.
Directories' terms generally prohibit scraping: keep volumes modest, keep the
politeness delay (DIRECTORY_DELAY_MS), and set PROXY_URL if you get blocked.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from ..config import settings
from ..http import HostThrottle, request
from ..models import RawLead, SearchQuery
from .base import Source

log = logging.getLogger(__name__)


@dataclass
class DirectorySpec:
    name: str
    country: str
    url: str                          # format: {niche} {location} {page}
    card: list[str]                   # selector candidates for one listing
    title: list[str]
    phone: list[str] = field(default_factory=list)
    website: list[str] = field(default_factory=list)
    address: list[str] = field(default_factory=list)
    category: list[str] = field(default_factory=list)
    per_page: int = 30
    max_pages: int = 3
    filter_city: bool = False         # directory is country-wide: keep only cards in q.city


SPECS: list[DirectorySpec] = [
    DirectorySpec(
        name="yellowpages_us", country="US",
        url="https://www.yellowpages.com/search?search_terms={niche}&geo_location_terms={location}&page={page}",
        card=["div.search-results.organic div.result", "div.result"],
        title=["a.business-name span", "a.business-name", "h2.n a"],
        phone=["div.phones", "div.phone"],
        website=["a.track-visit-website"],
        address=["div.adr", "div.street-address"],
        category=["div.categories a"],
    ),
    DirectorySpec(
        name="yell_uk", country="GB",
        url="https://www.yell.com/ucs/UcsSearchAction.do?keywords={niche}&location={location}&pageNum={page}",
        card=["div.businessCapsule", "article.businessCapsule"],
        title=["h2.businessCapsule--name", "span.businessCapsule--name", "h2"],
        phone=["span.business--telephoneNumber", "a[href^='tel:']"],
        website=["a.businessCapsule--ctaItem[href^='http']", "a[data-tracking*='WL:CLOSED']"],
        address=["span[itemprop='address']", "address"],
        category=["span.businessCapsule--classification"],
        per_page=25,
    ),
    DirectorySpec(
        name="yellowpages_uae", country="AE",
        url="https://www.yellowpages-uae.com/uae/{niche}?page={page}",
        card=["div.box"],                 # Tailwind markup: parsed by parse_uae_card below
        title=[], per_page=20,
        filter_city=True,
    ),
]


def _label_value(text: str, label: str) -> str | None:
    """'Location : | Off 101, Al Nasr Plaza | City : | Dubai' -> value after label."""
    parts = [p.strip() for p in text.split("|")]
    for i, p in enumerate(parts):
        if p.rstrip(" :").lower() == label.lower() and i + 1 < len(parts):
            return parts[i + 1] or None
    return None


def parse_uae_card(card, base_url: str) -> dict | None:
    link = next((a for a in card.select("a[href^='/']") if a.get_text(strip=True)), None)
    if not link:
        return None
    name = link.get_text(" ", strip=True).replace("More Info", "").strip()
    text = card.get_text(" | ", strip=True)
    tel = card.select_one("a[href^='tel:']")
    site = next((a["href"] for a in card.select("a[href^='http']")
                 if "yellowpages-uae.com" not in a["href"]), None)
    return {
        "name": name, "phone": tel["href"][4:] if tel else None, "website": site,
        "address": _label_value(text, "Location"), "city": _label_value(text, "City"),
        "profile_url": urljoin(base_url, link["href"].split("?")[0]),
    }


CUSTOM_PARSERS = {"yellowpages_uae": parse_uae_card}
BLOCK_MARKERS = ("just a moment", "attention required", "access denied", "are you a robot", "verify you are human")


def _first(el, selectors: list[str]):
    for s in selectors:
        found = el.select_one(s)
        if found:
            return found
    return None


def _text(el, selectors: list[str]) -> str | None:
    f = _first(el, selectors)
    if not f:
        return None
    t = f.get_text(" ", strip=True)
    return t or None


def _href(el, selectors: list[str], base: str) -> str | None:
    f = _first(el, selectors)
    if not f:
        return None
    href = f.get("href") or ""
    if href.startswith("tel:"):
        return None
    href = urljoin(base, href)
    # unwrap redirect links like /redirect?url=https%3A%2F%2F...
    qs = parse_qs(urlparse(href).query)
    for k in ("url", "u", "target", "redirect"):
        if k in qs and qs[k][0].startswith("http"):
            return unquote(qs[k][0])
    return href if href.startswith("http") else None


class DirectorySource(Source):
    needs_key = False
    _throttle = HostThrottle(settings.directory_delay_ms)

    def __init__(self, client, spec: DirectorySpec):
        super().__init__(client)
        self.spec = spec
        self.name = spec.name
        self.countries = {spec.country}

    def available(self) -> bool:
        return True

    def _location(self, q: SearchQuery) -> str:
        return q.city or ""

    async def search(self, q: SearchQuery) -> list[RawLead]:
        s = self.spec
        out: list[RawLead] = []
        own_host = urlparse(s.url).netloc
        for page in range(1, s.max_pages + 1):
            if len(out) >= q.limit:
                break
            url = s.url.format(niche=quote_plus(q.phrase.replace(" ", "-") if "{niche}?" in s.url else q.phrase), location=quote_plus(self._location(q)), page=page)
            try:
                r = await request(self.client, "GET", url, throttle=self._throttle)
            except Exception as e:  # network failure: give up on this source quietly
                log.warning("%s fetch failed: %s", s.name, e)
                break
            soup = BeautifulSoup(r.text, "html.parser")
            title = (soup.title.get_text(strip=True).lower() if soup.title else "")
            if r.status_code in (403, 429) or any(m in title for m in BLOCK_MARKERS):
                log.warning("%s blocked us (HTTP %s, bot protection). Set PROXY_URL to a residential "
                            "proxy, or rely on the Google sources.", s.name, r.status_code)
                break
            if r.status_code != 200:
                break
            cards = []
            for sel in s.card:
                cards = soup.select(sel)
                if cards:
                    break
            if not cards:
                log.info("%s: no listings parsed on page %s (selectors may need updating)", s.name, page)
                break
            custom = CUSTOM_PARSERS.get(s.name)
            for c in cards:
                if custom:
                    d = custom(c, url)
                    if not d or not d["name"]:
                        continue
                    if s.filter_city and q.city and d.get("city") and q.city.split(",")[0].strip().lower() not in d["city"].lower():
                        continue
                    out.append(RawLead(name=d["name"], source=s.name, niche=q.niche, country=q.country,
                                       city=d.get("city") or q.city, address=d.get("address"),
                                       phone=d.get("phone"), website=d.get("website"),
                                       extra={"profile_url": d.get("profile_url")}))
                    continue
                name = _text(c, s.title)
                if not name:
                    continue
                site = _href(c, s.website, url)
                if site and urlparse(site).netloc.endswith(own_host.replace("www.", "")):
                    site = None
                phone = _text(c, s.phone)
                if not phone:
                    tel = c.select_one("a[href^='tel:']")
                    phone = tel["href"][4:] if tel else None
                out.append(RawLead(
                    name=name, source=s.name, niche=q.niche, country=q.country, city=q.city,
                    address=_text(c, s.address), phone=phone, website=site,
                    category=_text(c, s.category),
                ))
            if len(cards) < s.per_page // 2:
                break
        return out[: q.limit]


def directory_sources(client) -> list[DirectorySource]:
    return [DirectorySource(client, spec) for spec in SPECS]
