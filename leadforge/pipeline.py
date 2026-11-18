"""Orchestrates: discover -> dedupe -> enrich -> verify -> score -> pitch -> store."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from .config import settings
from .enrich import audit_website, pagespeed_mobile, refresh_issues, verify_email_domain
from .enrich.serp import SerpChecker
from .http import make_client
from .models import Lead, RawLead, SearchQuery, utcnow
from .normalize import dedupe, normalize_phone
from .pitch import ai_pitches, template_pitch
from .scoring import score_lead
from .sources import all_sources
from .sources.csv_import import load_file
from .storage import Store

log = logging.getLogger(__name__)


@dataclass
class RunOptions:
    queries: list[SearchQuery]
    services: list[str] | None = None          # None = score all 4
    sources: list[str] | None = None           # None = every available source
    import_file: Path | None = None            # bypass discovery, audit this list
    pagespeed: bool = False                    # slow but valuable for SEO/web scoring
    ai_pitch: bool = False
    min_score: int = 0                         # drop leads below this from the export
    only_new: bool = False                     # skip leads already in DB from earlier runs
    serp: bool = True                          # Google organic rank check (if SERPER_API_KEY set)
    log: list[str] = field(default_factory=list)


async def discover(opts: RunOptions, progress: Progress) -> list[RawLead]:
    raw: list[RawLead] = []
    if opts.import_file:
        for q in opts.queries[:1]:
            raw.extend(load_file(opts.import_file, q))
        return raw
    async with make_client() as api_client, make_client(use_proxy=True) as scrape_client:
        sources = [s for s in all_sources(api_client)]
        # directory scrapers use the (optionally proxied) scrape client
        for s in sources:
            if s.name.startswith(("yellowpages", "yell")):
                s.client = scrape_client
        if opts.sources:
            sources = [s for s in sources if s.name in opts.sources]
        active = [s for s in sources if s.available()]
        skipped = [s.name for s in sources if not s.available()]
        if skipped:
            opts.log.append(f"Skipped (no API key): {', '.join(skipped)}")
        jobs = [(s, q) for q in opts.queries for s in active if s.supports(q)]
        task = progress.add_task("Discovering businesses", total=len(jobs))
        sem = asyncio.Semaphore(4)

        async def run(s, q):
            async with sem:
                try:
                    res = await s.search(q)
                    opts.log.append(f"{s.name}: {len(res)} for '{q.niche}' in {q.location_text}")
                    raw.extend(res)
                except Exception as e:  # one broken source must not kill the run
                    opts.log.append(f"{s.name} failed for {q.location_text}: {type(e).__name__}: {e}")
                finally:
                    progress.advance(task)

        await asyncio.gather(*(run(s, q) for s, q in jobs))
    return raw


async def enrich(leads: list[Lead], store: Store, opts: RunOptions, progress: Progress) -> None:
    with_site = [l for l in leads if l.website]
    task = progress.add_task("Auditing websites", total=len(with_site))
    sem = asyncio.Semaphore(settings.concurrency)
    async with make_client() as client:
        async def one(l: Lead):
            async with sem:
                try:
                    cached = store.cached_audit(l.domain, settings.cache_days) if l.domain else None
                    l.audit = cached or await audit_website(client, l.website)
                    if not cached and l.domain:
                        store.save_audit(l.domain, l.audit)
                except Exception as e:
                    log.debug("audit failed %s: %s", l.website, e)
                finally:
                    progress.advance(task)
        await asyncio.gather(*(one(l) for l in with_site))

        if opts.pagespeed:
            targets = [l for l in with_site if l.audit and l.audit.reachable and l.audit.pagespeed_mobile is None]
            t2 = progress.add_task("PageSpeed (mobile)", total=len(targets))
            psem = asyncio.Semaphore(4)

            async def ps(l: Lead):
                async with psem:
                    l.audit.pagespeed_mobile = await pagespeed_mobile(client, l.audit.final_url or l.website)
                    refresh_issues(l.audit)
                    if l.domain:
                        store.save_audit(l.domain, l.audit)
                    progress.advance(t2)
            await asyncio.gather(*(ps(l) for l in targets))

    # contacts from the website fill gaps from listings
    for l in leads:
        a = l.audit
        if a and a.emails:
            l.email = a.emails[0]
        if not l.phone and a and a.phones:
            l.phone = normalize_phone(a.phones[0], l.country)

    if opts.serp:
        async with make_client() as sclient:
            checker = SerpChecker(sclient)
            if checker.available():
                n = await checker.check(leads)
                opts.log.append(f"Google rank check: {n} searches")

    emails = [l for l in leads if l.email]
    t3 = progress.add_task("Verifying email domains", total=len(emails))
    vsem = asyncio.Semaphore(20)

    async def v(l: Lead):
        async with vsem:
            l.email_status = await verify_email_domain(l.email)
            progress.advance(t3)
    await asyncio.gather(*(v(l) for l in emails))
    for l in emails:
        if l.email_status == "no_mx":       # undeliverable: keep it visible but don't count as contact
            l.email = None


class ProgressTracker:
    """Minimal Progress-compatible tracker for the web dashboard (no terminal output)."""

    def __init__(self):
        self.tasks: list[dict] = []

    def add_task(self, description: str, total: int = 0) -> int:
        self.tasks.append({"stage": description, "done": 0, "total": total})
        return len(self.tasks) - 1

    def advance(self, task_id: int, n: int = 1) -> None:
        self.tasks[task_id]["done"] += n

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def build_queries(niches: list[str], countries: list[str], cities: list[str] | None = None,
                  top_cities: int = 3, limit: int = 60, expand: bool = False) -> list[SearchQuery]:
    """niches × countries × cities (× synonyms) -> SearchQuery list. Shared by CLI and dashboard."""
    from .catalog import rules
    from .geo import default_cities, expand_niche, normalize_country
    rules()   # make sure the editable catalog (countries, synonyms) is loaded
    queries = []
    for cc in countries:
        code = normalize_country(cc)
        for n in niches:
            for phrase in (expand_niche(n) if expand else [n]):
                for ct in (cities or default_cities(code, top_cities)):
                    queries.append(SearchQuery(niche=n.strip().lower(), country=code, city=ct,
                                               limit=limit, search_text=phrase))
    return queries


def _reset_call_caps() -> None:
    """Per-run API call caps (bill protection) — reset at the start of every run."""
    from .sources.google_places import GooglePlacesSource
    from .sources.serper import SerperPlacesSource
    GooglePlacesSource.calls_made = SerperPlacesSource.calls_made = SerpChecker.calls_made = 0


async def run_pipeline(opts: RunOptions, progress=None) -> tuple[list[Lead], list[str]]:
    from .catalog import rules
    rules()
    _reset_call_caps()
    store = Store(settings.db_path)
    if progress is None:
        progress = Progress(TextColumn("[bold]{task.description}"), BarColumn(), MofNCompleteColumn(),
                            TimeElapsedColumn(), transient=False)
    with progress:
        raw = await discover(opts, progress)
        leads = dedupe(raw)
        opts.log.append(f"Discovered {len(raw)} listings -> {len(leads)} unique businesses")
        if opts.only_new:
            known = store.known_keys()
            before = len(leads)
            leads = [l for l in leads if l.key not in known]
            opts.log.append(f"Skipped {before - len(leads)} already-known leads")
        suppressed = store.suppressed()
        leads = [l for l in leads if not ({(l.domain or ""), (l.phone or "")} & suppressed)]
        await enrich(leads, store, opts, progress)

    leads = [l for l in leads if not (l.email and l.email in suppressed)]
    for l in leads:
        score_lead(l, opts.services)
        l.pitch = template_pitch(l)
    if opts.ai_pitch:
        n = await ai_pitches(leads)
        opts.log.append(f"AI pitches written: {n}")
    store.upsert(leads)
    return [l for l in leads if l.best_score >= opts.min_score], opts.log


__all__ = ["RunOptions", "run_pipeline", "utcnow"]
