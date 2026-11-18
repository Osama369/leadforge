# LeadForge: Architecture

## Goal
Find local businesses (niche × country × city) and **audit each one's online presence**.
Then score how badly they need each agency service (SEO, Ads, Web, AI Chatbot) and export
ready-to-pitch leads with the reason and an opener line.

## Pipeline

```
 CLI (typer)  ──►  RunOptions (niches × countries × cities × services)
                         │
 1. DISCOVER   sources/  ├─ google_places.py   Places API (New) Text Search, 20/call, phone+website
               (plugins) ├─ serper.py          Serper.dev Maps results (backup / extra coverage)
                         ├─ directories.py     Yellow Pages US, Yell UK, Yellow Pages UAE (no key)
                         └─ csv_import.py      your own list (.csv / .txt)
                         │   RawLead[]
 2. CLEAN      normalize.py  phone → E.164, website → canonical domain, drop social/directory URLs
               dedupe        place_id → domain → phone → fuzzy name+city; merges sources
                         │   Lead[]
 3. ENRICH     enrich/website.py   home + contact/about pages:
                                   emails (incl. Cloudflare-obfuscated), phones, socials, WhatsApp,
                                   CMS, builder subdomain, GA4/GTM, Google Ads tag, Meta pixel,
                                   chat widgets (20+), booking tools (14+), SEO checks, © year
               enrich/extras.py    PageSpeed mobile score (optional), MX check on emails
               storage (cache)     audits cached per domain for CACHE_DAYS
                         │
 4. QUALIFY    scoring.py + config/scoring.yaml
                                   signals → points per service → 0-100 → hot / warm / cold
                                   + human-readable reasons; no-contact leads capped
 5. PITCH      pitch.py            template opener (free) or Claude (--ai-pitch)
                         │
 6. STORE      storage.py (SQLite) leads, audits, runs, suppression (opt-outs)
 7. EXPORT     export.py           .xlsx (Summary / Hot / Warm / All / per-service tabs), .csv,
                                   Google Sheets (service account)
```

## Design decisions
| Decision | Why |
|---|---|
| **Source = plugin** (`Source.search(query) -> RawLead[]`) | Add Yelp, Bing Places or a new country directory by adding one file |
| **Text Search only, no Place Details** | Phone + website come back in the search response, so you pay 1 call per 20 businesses, not 21 |
| **Hard call caps per run** (`MAX_*_CALLS_PER_RUN`) | A typo can never produce a surprise Google bill |
| **Scoring rules in YAML** | Sales can tune weights without code; every point has a reason string |
| **"No website" only trusted from reliable sources** | A directory card without a URL ≠ a business without a website |
| **Audit cache per domain** | Re-runs and overlapping searches cost nothing |
| **MX check, no SMTP probing** | SMTP probing is slow, often blocked and gets your IP blacklisted |
| **Suppression table** | Opt-outs are never exported again (GDPR/PECR/CAN-SPAM) |
| **SQLite now, Postgres later** | Same schema; zero setup for the MVP |

## Known limits (tested 2026-11-11)
- **Yellow Pages US and Yell UK return HTTP 403 (bot protection) to plain HTTP requests.**
  The scrapers detect this and skip. To use them at all you need a residential proxy (`PROXY_URL`)
  or a headless-browser fetcher (planned). Google Places and Serper are the reliable core.
- **Yellow Pages UAE works**, but its pages only render ~2–5 listings server-side, and those
  cards don't include websites. Treat it as a supplement and use Google Places for UAE coverage.
- Places API terms: Places content may only be cached for a limited time (place_id indefinitely).
  Re-run to refresh rather than treating old exports as a permanent database.

## Scale-up path (v1 → SaaS)
1. **Queue workers**: wrap `discover` / `enrich` stages as Celery or ARQ tasks over Redis. Stages are already independent.
2. **Postgres** instead of SQLite (same tables), plus a FastAPI layer over `run_pipeline`.
3. **Dashboard** (Next.js): campaigns, filters, lead table, "export to Sheet" button.
4. **Headless fetcher** (Playwright + residential proxy) for bot-protected directories.
5. **Grid search** for Places: split big cities into map tiles (locationBias), since one query returns at most 60 results.
6. **SERP rank check** (Serper `/search`): is the business in the top 10 for "niche + city"? This is a strong SEO signal.
7. **Re-scan scheduler**: re-audit old leads monthly and flag changes ("started running ads", "new website").
8. **CRM / outreach webhooks**: HubSpot, GoHighLevel, Instantly.

## Editable catalog (services, niches, locations)
- `leadforge/catalog.py` stores services, niches, countries/cities and workspace settings in SQLite
  (`cat_services`, `cat_niches`, `cat_countries`, `cat_kv`). It is seeded once from `config/scoring.yaml` + `geo.py`.
- `catalog.apply()` loads it into the in-memory registries (`geo.COUNTRIES`, `SERVICES`, `SERVICE_NICHES`,
  `NICHE_SYNONYMS`, `PITCH_HOOKS`). CLI and dashboard both call it at startup, and every catalog write calls it again.
- A service = base score + weighted signals from `scoring.SIGNALS` (detectors are code, weights are data).
  Custom services need no code: pick signals, points and reason text in the dashboard.

## Dashboard
- `leadforge/web/app.py` (FastAPI) holds the JSON API; `leadforge/web/static/` holds the UI (`index.html`, `app.css`,
  `app.js`: vanilla JS, no build step, hash router, reusable searchable multi-select).
- One campaign at a time (in-memory job registry, progress polled every 1.5 s). Max 400 searches per campaign.
- API keys from Settings are written to `.env` and reloaded live (`config.update_env`).
