"""Core data models shared by every stage of the pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SearchQuery:
    """One unit of discovery work: a niche in a place."""
    niche: str                 # e.g. "dentist"
    country: str               # ISO-2, e.g. "US"
    city: str | None = None    # e.g. "Houston"
    limit: int = 60            # max businesses per source for this query
    search_text: str | None = None   # phrase actually searched (synonym); defaults to niche

    @property
    def phrase(self) -> str:
        return self.search_text or self.niche

    @property
    def location_text(self) -> str:
        from .geo import country_name
        return f"{self.city}, {country_name(self.country)}" if self.city else country_name(self.country)


@dataclass
class RawLead:
    """What a source connector returns, before cleaning/enrichment."""
    name: str
    source: str
    niche: str = ""
    country: str = ""
    city: str | None = None
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    review_count: int | None = None
    category: str | None = None
    maps_url: str | None = None
    place_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class WebsiteAudit:
    """Everything we learn by visiting the business website."""
    reachable: bool = False
    final_url: str | None = None
    status_code: int | None = None
    https: bool = False
    load_ms: int | None = None
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)   # {"facebook": url, ...}
    cms: str | None = None                                   # "WordPress", "Wix", ...
    builder_subdomain: bool = False                          # *.wixsite.com etc.
    tech: list[str] = field(default_factory=list)            # everything detected
    has_google_ads_tag: bool = False
    has_meta_pixel: bool = False
    has_ga4: bool = False
    has_gtm: bool = False
    has_chat_widget: bool = False
    chat_widget: str | None = None
    has_booking: bool = False
    has_viewport: bool = False
    title: str | None = None
    meta_description: str | None = None
    h1_count: int = 0
    has_schema: bool = False
    has_local_schema: bool = False
    word_count: int = 0
    copyright_year: int | None = None
    seo_issues: list[str] = field(default_factory=list)
    pagespeed_mobile: int | None = None                      # 0-100, None = not checked
    error: str | None = None


@dataclass
class ServiceScore:
    service: str        # "seo" | "ads" | "web" | "chatbot"
    score: int          # 0-100
    tier: str           # "hot" | "warm" | "cold"
    reasons: list[str] = field(default_factory=list)


@dataclass
class Lead:
    """A cleaned, enriched, scored lead — one row in the export."""
    key: str                          # dedupe key
    name: str
    niche: str
    country: str
    city: str | None = None
    address: str | None = None
    phone: str | None = None          # E.164
    website: str | None = None
    domain: str | None = None
    rating: float | None = None
    review_count: int | None = None
    category: str | None = None
    maps_url: str | None = None
    place_id: str | None = None
    sources: list[str] = field(default_factory=list)
    audit: WebsiteAudit | None = None
    email: str | None = None          # best email
    email_status: str | None = None   # "valid_mx" | "no_mx" | "unknown"
    serp_checked: bool = False        # organic Google rank was checked
    serp_query: str | None = None     # e.g. "dentist Houston"
    serp_rank: int | None = None      # 1-20, None = not in top 20
    serp_leader: str | None = None    # best-ranking competitor business domain
    scores: dict[str, ServiceScore] = field(default_factory=dict)
    best_service: str | None = None
    best_score: int = 0
    pitch: str | None = None
    status: str = "new"               # sales pipeline (dashboard); DB column is the source of truth
    notes: str = ""
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Lead":
        known = set(cls.__dataclass_fields__)
        d = {k: v for k, v in d.items() if k in known}   # tolerate rows from older versions
        audit = d.pop("audit", None)
        scores = d.pop("scores", {}) or {}
        lead = cls(**d)
        lead.audit = WebsiteAudit(**audit) if audit else None
        lead.scores = {k: ServiceScore(**v) for k, v in scores.items()}
        return lead
