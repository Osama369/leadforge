"""Pitch line generator.

Default: free template engine built from the lead's top problems.
Optional: Claude writes a personalised opener (set ANTHROPIC_API_KEY and use --ai-pitch).
"""
from __future__ import annotations

import asyncio
import logging

from .config import settings
from .geo import PITCH_HOOKS, SERVICES
from .models import Lead

log = logging.getLogger(__name__)

HOOKS = {
    "seo": "We help {niche}s in {city} rank in the Google top 3 so new customers find them first.",
    "ads": "We run Google & Meta ads for {niche}s that bring booked customers, not just clicks.",
    "web": "We build fast, mobile-first websites for {niche}s that turn visitors into calls.",
    "chatbot": "We set up AI assistants that answer and book {niche} customers 24/7, even at 2am.",
}


def _plural(word: str) -> str:
    """bakery -> bakeries, dentist -> dentists, salon -> salons, coach -> coaches."""
    if not word or word.endswith("s"):
        return word
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("sh", "ch", "x", "z")):
        return word + "es"
    return word + "s"


def _hook(service: str, niche: str, city: str) -> str:
    tpl = PITCH_HOOKS.get(service) or HOOKS.get(service) or "We help {niche}s in {city} with " + SERVICES.get(service, service) + "."
    try:
        return tpl.replace("{niche}s", "{niches}").format(niche=niche, niches=_plural(niche), city=city)
    except (KeyError, IndexError, ValueError):
        return tpl


def template_pitch(lead: Lead) -> str | None:
    if not lead.best_service or lead.best_service not in lead.scores:
        return None
    s = lead.scores[lead.best_service]
    # only concrete observations: skip caveats meant for the sales rep, not the prospect
    skip = ("(pitch", "(check", "already", "no website listed")
    positives = [r for r in s.reasons if not any(k in r.lower() for k in skip)][:2]
    city = lead.city or "your area"
    opener = f"Hi {lead.name} team,\n"
    if lead.best_service == "seo" and lead.serp_checked and lead.serp_leader and \
            (lead.serp_rank is None or lead.serp_rank > 10):
        return (opener + f"I searched '{lead.serp_query}' on Google and {lead.serp_leader} shows up on page 1, "
                f"but you don't. " + _hook("seo", lead.niche, city))
    if positives:
        opener += "I noticed: " + "; ".join(p[0].lower() + p[1:] for p in positives) + ". "
    return opener + _hook(lead.best_service, lead.niche, city)


SYSTEM = (
    "You write cold-outreach opening lines for a digital agency. Given one local business and the "
    "problems an automated audit found, write ONE opener of 2 sentences max (under 45 words): "
    "first a specific, genuine observation using the facts given, then the outcome the agency can deliver. "
    "Plain, friendly, no hype, no emojis, no placeholders, no subject line. Never invent facts "
    "that are not in the data. Output only the opener text."
)


def _lead_brief(lead: Lead) -> str:
    s = lead.scores[lead.best_service]
    parts = [
        f"Business: {lead.name}", f"Niche: {lead.niche}", f"City: {lead.city or ''}, {lead.country}",
        f"Service to pitch: {SERVICES.get(lead.best_service, lead.best_service)}",
        f"Audit findings: {'; '.join(s.reasons[:4])}",
    ]
    if lead.rating:
        parts.append(f"Google rating: {lead.rating} ({lead.review_count} reviews)")
    if lead.serp_checked:
        rank = f"#{lead.serp_rank}" if lead.serp_rank else "not in top 20"
        parts.append(f"Google organic rank for '{lead.serp_query}': {rank}; top competitor: {lead.serp_leader}")
    return "\n".join(parts)


async def ai_pitches(leads: list[Lead], concurrency: int = 5) -> int:
    """Fill lead.pitch using Claude for leads that have a best_service. Returns count written."""
    if not settings.anthropic_key:
        log.warning("ANTHROPIC_API_KEY not set; using template pitches.")
        return 0
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_key)
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def one(lead: Lead) -> None:
        nonlocal done
        async with sem:
            kwargs = dict(
                model=settings.pitch_model,
                max_tokens=2000,
                output_config={"effort": "low"},
                system=SYSTEM,
                messages=[{"role": "user", "content": _lead_brief(lead)}],
            )
            try:
                try:
                    # server-side refusal fallback (supported on Opus/Fable/Sonnet 5.5 tiers)
                    resp = await client.beta.messages.create(
                        **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
                except (TypeError, anthropic.BadRequestError):
                    resp = await client.messages.create(**kwargs)
            except anthropic.RateLimitError:
                await asyncio.sleep(20)
                return
            except anthropic.APIStatusError as e:
                log.warning("Claude error %s for %s", e.status_code, lead.name)
                return
            except anthropic.APIConnectionError:
                return
            if resp.stop_reason == "refusal":
                return
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            if text:
                lead.pitch = text
                done += 1

    await asyncio.gather(*(one(l) for l in leads if l.best_service and l.best_score >= 40))
    await client.close()
    return done
