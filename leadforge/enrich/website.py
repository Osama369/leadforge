"""Website auditor: visits a business site (home + contact/about pages) and
extracts contacts, tech stack, marketing pixels, chat/booking tools and SEO signals."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..http import request
from ..models import WebsiteAudit
from . import signatures as sig

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24}")
BAD_EMAIL_PARTS = (
    "example.", "domain.com", "yourdomain", "email.com", "sentry", "wixpress", "@2x", ".png", ".jpg",
    ".jpeg", ".gif", ".svg", ".webp", "godaddy.com", "squarespace.com", "wordpress.com", "noreply",
    "no-reply", "user@", "name@", "test@", "@sentry", "schema.org", "@w3.org", "jquery", "bootstrap",
)
CONTACT_HINTS = ("contact", "about", "reach", "get-in-touch", "location", "appointment", "book")
COPYRIGHT_RE = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(20\d{2}|19\d{2})", re.I)


def _decode_cfemail(hexstr: str) -> str | None:
    """Cloudflare email obfuscation: data-cfemail="..." """
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return None


def clean_emails(candidates: set[str], site_domain: str | None) -> list[str]:
    out = []
    for e in candidates:
        e = e.strip().strip(".").lower()
        if len(e) > 80 or any(b in e for b in BAD_EMAIL_PARTS):
            continue
        if not EMAIL_RE.fullmatch(e):
            continue
        out.append(e)
    # same-domain emails first, then role-based (info@, contact@) first
    def rank(e: str):
        dom = e.split("@")[1]
        same = site_domain and (dom == site_domain or dom.endswith("." + site_domain))
        role = e.split("@")[0] in ("info", "contact", "hello", "office", "admin", "enquiries", "inquiries", "sales")
        return (0 if same else 1, 0 if role else 1, len(e))
    return sorted(set(out), key=rank)


def registered_domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else "https://" + url).netloc.lower().split(":")[0]
    return host.removeprefix("www.") or None


def _match_any(html: str, table: dict[str, list[str]]) -> list[str]:
    return [name for name, needles in table.items() if any(n in html for n in needles)]


def _parse_page(html: str, base_url: str, audit: WebsiteAudit, emails: set[str], is_home: bool) -> list[str]:
    """Parse one page into the audit. Returns candidate contact-page URLs."""
    low = html.lower()
    soup = BeautifulSoup(html, "html.parser")

    # --- contacts ---
    emails.update(EMAIL_RE.findall(soup.get_text(" ")))
    for a in soup.select("a[href^='mailto:']"):
        emails.add(a["href"][7:].split("?")[0])
    for el in soup.select("[data-cfemail]"):
        d = _decode_cfemail(el.get("data-cfemail", ""))
        if d:
            emails.add(d)
    for a in soup.select("a[href^='tel:']"):
        p = a["href"][4:].strip()
        if p and p not in audit.phones:
            audit.phones.append(p)
    for name, rx in sig.SOCIAL_PATTERNS.items():
        if name not in audit.socials:
            m = rx.search(html)
            if m:
                audit.socials[name] = m.group(0).rstrip("/\\\"'")

    # --- tech / marketing (any page) ---
    for t in _match_any(low, sig.CHAT_WIDGETS):
        audit.has_chat_widget, audit.chat_widget = True, audit.chat_widget or t
        audit.tech.append(t)
    for t in _match_any(low, sig.BOOKING):
        audit.has_booking = True
        audit.tech.append(t)
    a = sig.ANALYTICS
    audit.has_ga4 |= bool(a["ga4"].search(html))
    audit.has_gtm |= bool(a["gtm"].search(html))
    audit.has_google_ads_tag |= bool(a["google_ads"].search(html))
    audit.has_meta_pixel |= bool(a["meta_pixel"].search(html))
    for k in ("tiktok_pixel", "linkedin_insight"):
        if a[k].search(html):
            audit.tech.append(k)

    if is_home:
        cms = _match_any(low, sig.CMS)
        audit.cms = cms[0] if cms else None
        audit.tech.extend(cms)
        audit.has_viewport = bool(soup.select_one("meta[name='viewport']"))
        audit.title = soup.title.get_text(strip=True)[:200] if soup.title else None
        md = soup.select_one("meta[name='description']")
        audit.meta_description = (md.get("content") or "").strip()[:300] if md else None
        audit.h1_count = len(soup.find_all("h1"))
        for s in soup.select("script[type='application/ld+json']"):
            audit.has_schema = True
            try:
                data = json.loads(s.string or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
            for it in items:
                types = it.get("@type", []) if isinstance(it, dict) else []
                types = [types] if isinstance(types, str) else types
                if any(str(t).lower() in sig.LOCAL_SCHEMA_TYPES for t in types):
                    audit.has_local_schema = True
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        audit.word_count = len(text.split())
        years = [int(y) for y in COPYRIGHT_RE.findall(html)]
        audit.copyright_year = max(years) if years else None

    # contact page candidates (same host)
    host = urlparse(base_url).netloc
    cands = []
    for link in soup.select("a[href]"):
        href = link["href"]
        label = (link.get_text(" ", strip=True) + " " + href).lower()
        if any(h in label for h in CONTACT_HINTS):
            full = urljoin(base_url, href).split("#")[0]
            if urlparse(full).netloc == host and full not in cands and full.rstrip("/") != base_url.rstrip("/"):
                cands.append(full)
    return cands


def _seo_issues(audit: WebsiteAudit) -> list[str]:
    issues = []
    if not audit.https:
        issues.append("No HTTPS / SSL")
    if not audit.has_viewport:
        issues.append("Not mobile-friendly (no viewport tag)")
    if not audit.title:
        issues.append("Missing page title")
    elif len(audit.title) < 15 or len(audit.title) > 65:
        issues.append(f"Weak title length ({len(audit.title)} chars)")
    if not audit.meta_description:
        issues.append("Missing meta description")
    if audit.h1_count == 0:
        issues.append("No H1 heading")
    elif audit.h1_count > 1:
        issues.append(f"{audit.h1_count} H1 headings")
    if not audit.has_local_schema:
        issues.append("No LocalBusiness schema")
    if audit.word_count and audit.word_count < 300:
        issues.append(f"Thin homepage content ({audit.word_count} words)")
    if audit.load_ms and audit.load_ms > 3000:
        issues.append(f"Slow server response ({audit.load_ms / 1000:.1f}s)")
    if audit.copyright_year and audit.copyright_year < datetime.now().year - 2:
        issues.append(f"Outdated site (© {audit.copyright_year})")
    if audit.pagespeed_mobile is not None and audit.pagespeed_mobile < 50:
        issues.append(f"Poor mobile PageSpeed ({audit.pagespeed_mobile}/100)")
    return issues


async def audit_website(client: httpx.AsyncClient, url: str, max_extra_pages: int = 2) -> WebsiteAudit:
    audit = WebsiteAudit()
    if not url.startswith("http"):
        url = "https://" + url
    emails: set[str] = set()
    try:
        t0 = time.monotonic()
        r = await request(client, "GET", url, retries=2)
        audit.load_ms = int((time.monotonic() - t0) * 1000)
    except Exception as e:
        # try plain http if https failed (old sites)
        if url.startswith("https://"):
            try:
                r = await request(client, "GET", "http://" + url[8:], retries=1)
            except Exception as e2:
                audit.error = f"unreachable: {type(e2).__name__}"
                return audit
        else:
            audit.error = f"unreachable: {type(e).__name__}"
            return audit

    audit.status_code = r.status_code
    audit.final_url = str(r.url)
    audit.https = audit.final_url.startswith("https://")
    if r.status_code >= 400 or "text/html" not in r.headers.get("content-type", "text/html"):
        audit.error = f"HTTP {r.status_code}"
        audit.seo_issues = ["Website broken / not loading"]
        return audit
    audit.reachable = True
    dom = registered_domain(audit.final_url)
    audit.builder_subdomain = any(dom and dom.endswith(b) for b in sig.BUILDER_SUBDOMAINS)

    cands = _parse_page(r.text, audit.final_url, audit, emails, is_home=True)
    # visit contact/about pages for emails + more signals
    for extra in cands[:max_extra_pages]:
        if clean_emails(emails, dom) and len(audit.phones) > 0 and extra != cands[0]:
            break
        try:
            r2 = await request(client, "GET", extra, retries=1)
            if r2.status_code == 200:
                _parse_page(r2.text, str(r2.url), audit, emails, is_home=False)
        except Exception:
            continue

    audit.emails = clean_emails(emails, dom)[:5]
    audit.tech = sorted(set(audit.tech))
    audit.seo_issues = _seo_issues(audit)
    return audit


def refresh_issues(audit: WebsiteAudit) -> None:
    """Recompute issue list (e.g. after PageSpeed score arrives)."""
    if audit.reachable:
        audit.seo_issues = _seo_issues(audit)
