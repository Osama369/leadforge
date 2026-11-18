"""Qualification engine: turns raw signals into a 0-100 score per service + reasons.

A *service* is just a weighted set of signals (see SIGNALS below), defined in the
workspace catalog. Clients can create new services (e.g. "Social Media Management",
"Reputation Management") by combining signals in the dashboard; no code changes needed."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from .models import Lead, ServiceScore

# Signal library: every fact the auditor can detect, with a label, group and default reason.
# Reason text placeholders: {review_count} {rating} {pagespeed} {copyright_year} {cms}
# {chat_widget} {load_s} {serp_query} {serp_rank} {serp_leader}
SIGNALS: dict[str, dict] = {
    # Website presence
    "no_website":        {"group": "Website", "label": "Has no website", "reason": "Has no website at all"},
    "website_unknown":   {"group": "Website", "label": "No website listed (directory)", "reason": "No website listed in directory (check before pitching)"},
    "site_broken":       {"group": "Website", "label": "Website down / broken", "reason": "Website is down or broken"},
    "builder_subdomain": {"group": "Website", "label": "Free builder subdomain", "reason": "Uses a free builder subdomain, not a real domain"},
    "diy_builder":       {"group": "Website", "label": "DIY builder (Wix, GoDaddy...)", "reason": "DIY builder site ({cms})"},
    "not_mobile":        {"group": "Website", "label": "Not mobile-friendly", "reason": "Not mobile-friendly"},
    "outdated_site":     {"group": "Website", "label": "Outdated (old © year)", "reason": "Site looks outdated (© {copyright_year})"},
    "no_https":          {"group": "Website", "label": "No SSL / HTTPS", "reason": "No SSL certificate"},
    "slow_load":         {"group": "Website", "label": "Slow server response", "reason": "Slow to load ({load_s}s)"},
    "poor_pagespeed":    {"group": "Website", "label": "Poor mobile PageSpeed", "reason": "Mobile PageSpeed only {pagespeed}/100"},
    "thin_content":      {"group": "Website", "label": "Thin homepage content", "reason": "Thin homepage content"},
    # SEO
    "not_ranking":       {"group": "SEO", "label": "Not on Google page 1", "reason": "Not on Google page 1 for '{serp_query}' ({serp_leader} ranks above them)"},
    "page1_not_top3":    {"group": "SEO", "label": "Page 1 but not top 3", "reason": "Only #{serp_rank} on Google for '{serp_query}'"},
    "ranks_top3":        {"group": "SEO", "label": "Already top 3 on Google", "reason": "Already top 3 on Google for '{serp_query}'"},
    "no_local_schema":   {"group": "SEO", "label": "No LocalBusiness schema", "reason": "No LocalBusiness schema markup"},
    "missing_meta":      {"group": "SEO", "label": "Missing meta description", "reason": "Missing meta description"},
    "weak_title":        {"group": "SEO", "label": "Weak page title", "reason": "Weak/missing homepage title"},
    "no_h1":             {"group": "SEO", "label": "No H1 heading", "reason": "Homepage has no H1 heading"},
    # Marketing stack
    "no_analytics":      {"group": "Marketing", "label": "No Google Analytics", "reason": "No Google Analytics, so they're flying blind"},
    "has_analytics":     {"group": "Marketing", "label": "Has analytics", "reason": "Has analytics, so they're data-aware"},
    "no_google_ads_tag": {"group": "Marketing", "label": "Not running Google Ads", "reason": "No Google Ads tag, not running search ads"},
    "has_google_ads_tag": {"group": "Marketing", "label": "Runs Google Ads", "reason": "Already runs Google Ads"},
    "no_meta_pixel":     {"group": "Marketing", "label": "No Meta pixel", "reason": "No Meta (Facebook) pixel, so no retargeting"},
    "no_facebook":       {"group": "Marketing", "label": "No Facebook page linked", "reason": "No Facebook page linked from website"},
    "no_instagram":      {"group": "Marketing", "label": "No Instagram linked", "reason": "No Instagram linked from website"},
    "few_socials":       {"group": "Marketing", "label": "0-1 social profiles", "reason": "Weak social media presence"},
    # Customer experience
    "no_chat_widget":    {"group": "Customer experience", "label": "No live chat / chatbot", "reason": "No live chat or chatbot on website"},
    "has_chat_widget":   {"group": "Customer experience", "label": "Has a chat widget", "reason": "Already has {chat_widget}"},
    "no_booking":        {"group": "Customer experience", "label": "No online booking", "reason": "No online booking system"},
    "whatsapp_only":     {"group": "Customer experience", "label": "WhatsApp link, no chat", "reason": "Handles leads via WhatsApp manually"},
    "no_email_found":    {"group": "Customer experience", "label": "No email on website", "reason": "No contact email published on website"},
    # Reputation
    "low_reviews":       {"group": "Reputation", "label": "Fewer than 30 reviews", "reason": "Only {review_count} Google reviews"},
    "many_reviews":      {"group": "Reputation", "label": "100+ reviews (busy)", "reason": "{review_count} reviews, an established business"},
    "good_rating":       {"group": "Reputation", "label": "Rating 4.3+", "reason": "Good rating ({rating}★) to build on"},
    "low_rating":        {"group": "Reputation", "label": "Rating below 4.0", "reason": "Rating only {rating}★, needs reputation work"},
    # Niche fit
    "high_value_niche":  {"group": "Niche fit", "label": "High-ticket niche", "reason": "High-ticket niche"},
    "appointment_niche": {"group": "Niche fit", "label": "Appointment-based niche", "reason": "Appointment-based business"},
}


def load_rules() -> dict:
    from .catalog import rules
    return rules()


def _niche_match(lead: Lead, words: list[str], exact: list[str]) -> bool:
    hay = f"{lead.niche} {lead.category or ''}".lower()
    return lead.niche.lower() in exact or any(w in hay for w in words)


# Sources whose listings reliably include the website when one exists. If a lead
# came only from other sources, "no website listed" != "has no website".
RELIABLE_WEBSITE_SOURCES = {"google_places", "serper_places", "yellowpages_us", "yell_uk", "import"}


def build_signals(lead: Lead, rules: dict) -> dict[str, bool]:
    a = lead.audit
    has_site = bool(lead.website)
    site_known = has_site or any(s in RELIABLE_WEBSITE_SOURCES for s in lead.sources)
    ok = bool(a and a.reachable)
    rc = lead.review_count or 0
    year = datetime.now().year
    S: dict[str, Callable[[], bool]] = {
        "no_website":        lambda: not has_site and site_known,
        "website_unknown":   lambda: not site_known,
        "site_broken":       lambda: has_site and a is not None and not a.reachable,
        "builder_subdomain": lambda: ok and a.builder_subdomain,
        "no_https":          lambda: ok and not a.https,
        "not_mobile":        lambda: ok and not a.has_viewport,
        "outdated_site":     lambda: ok and a.copyright_year is not None and a.copyright_year < year - 2,
        "thin_content":      lambda: ok and 0 < a.word_count < 300,
        "slow_load":         lambda: ok and (a.load_ms or 0) > 3000,
        "poor_pagespeed":    lambda: ok and a.pagespeed_mobile is not None and a.pagespeed_mobile < 50,
        "diy_builder":       lambda: ok and a.cms in ("Wix", "GoDaddy Builder", "Weebly", "Squarespace", "Duda"),
        "no_local_schema":   lambda: ok and not a.has_local_schema,
        "missing_meta":      lambda: ok and not a.meta_description,
        "weak_title":        lambda: ok and (not a.title or not 15 <= len(a.title) <= 65),
        "no_h1":             lambda: ok and a.h1_count == 0,
        "no_analytics":      lambda: ok and not (a.has_ga4 or a.has_gtm),
        "has_analytics":     lambda: ok and (a.has_ga4 or a.has_gtm),
        "no_google_ads_tag": lambda: ok and not a.has_google_ads_tag,
        "has_google_ads_tag": lambda: ok and a.has_google_ads_tag,
        "no_meta_pixel":     lambda: ok and not a.has_meta_pixel,
        "no_chat_widget":    lambda: ok and not a.has_chat_widget,
        "has_chat_widget":   lambda: ok and a.has_chat_widget,
        "no_booking":        lambda: ok and not a.has_booking,
        "whatsapp_only":     lambda: ok and "whatsapp" in a.socials and not a.has_chat_widget,
        "not_ranking":       lambda: lead.serp_checked and (lead.serp_rank is None or lead.serp_rank > 10),
        "page1_not_top3":    lambda: lead.serp_checked and lead.serp_rank is not None and 4 <= lead.serp_rank <= 10,
        "ranks_top3":        lambda: lead.serp_checked and lead.serp_rank is not None and lead.serp_rank <= 3,
        "low_reviews":       lambda: lead.review_count is not None and rc < 30,
        "many_reviews":      lambda: rc >= 100,
        "good_rating":       lambda: (lead.rating or 0) >= 4.3,
        "low_rating":        lambda: lead.rating is not None and lead.rating < 4.0,
        "no_facebook":       lambda: ok and "facebook" not in a.socials,
        "no_instagram":      lambda: ok and "instagram" not in a.socials,
        "few_socials":       lambda: ok and len([k for k in a.socials if k != "whatsapp"]) <= 1,
        "no_email_found":    lambda: ok and not a.emails,
        "high_value_niche":  lambda: _niche_match(lead, rules.get("high_value_niches", []), rules.get("high_value_exact", [])),
        "appointment_niche": lambda: _niche_match(lead, rules.get("appointment_niches", []), rules.get("appointment_exact", [])),
    }
    missing = set(SIGNALS) - set(S)
    assert not missing, f"signals without detector: {missing}"
    return {k: bool(f()) for k, f in S.items()}


def _fmt(reason: str, lead: Lead) -> str:
    a = lead.audit
    vals = {
        "review_count": lead.review_count, "rating": lead.rating,
        "pagespeed": a.pagespeed_mobile if a else None,
        "copyright_year": a.copyright_year if a else None,
        "cms": a.cms if a else None, "chat_widget": a.chat_widget if a else None,
        "load_s": f"{a.load_ms / 1000:.1f}" if a and a.load_ms else None,
        "serp_query": lead.serp_query, "serp_rank": lead.serp_rank,
        "serp_leader": lead.serp_leader or "a competitor",
    }
    try:
        return reason.format(**vals)
    except (KeyError, IndexError):
        return reason


def score_lead(lead: Lead, services: list[str] | None = None) -> Lead:
    rules = load_rules()
    sig = build_signals(lead, rules)
    contactable = bool(lead.email or lead.phone)
    out: dict[str, ServiceScore] = {}
    for svc, cfg in rules["services"].items():
        if services and svc not in services:
            continue
        score = cfg.get("base", 0)
        reasons: list[tuple[int, str]] = []
        for name, rule in (cfg.get("signals") or {}).items():
            if sig.get(name):
                score += rule["points"]
                reasons.append((rule["points"], _fmt(rule["reason"], lead)))
        if not contactable:
            score = min(score, rules.get("no_contact_cap", 30))
        score = max(0, min(100, score))
        tier = "hot" if score >= rules["tiers"]["hot"] else "warm" if score >= rules["tiers"]["warm"] else "cold"
        # strongest positive reasons first, negatives (caveats) last
        reasons.sort(key=lambda r: (r[0] < 0, -abs(r[0])))
        out[svc] = ServiceScore(service=svc, score=score, tier=tier, reasons=[r for _, r in reasons])
    lead.scores = out
    if out:
        best = max(out.values(), key=lambda s: s.score)
        lead.best_service, lead.best_score = best.service, best.score
    return lead
