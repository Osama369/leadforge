# LeadForge

Find local businesses, **audit their website automatically**, and get only the leads that need
your agency's services (SEO, Ads, Website, AI Chatbot), each with the reason and a pitch line.
Exports to Excel or Google Sheets.

## Setup (Windows)
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # add keys later; all are optional
python -m leadforge doctor  # shows which sources/features are ready
```
> Tip: keep the project in a short path (e.g. `D:\leadforge`). Very long Windows paths break some package installs.

## Web dashboard
```bash
python -m leadforge serve          # open http://127.0.0.1:8000
```
- **Overview**: KPIs, hot leads per service, sales pipeline, best niches, uncontacted hot leads, recent campaigns.
  A first-run checklist guides new users (add API key → review services → first campaign).
- **New campaign**: a 5-step wizard. Searchable dropdowns pick services → niches (recommended for the chosen
  services first) → countries and cities (grouped by country) → sources and options → review. A live summary
  shows searches and the max API calls before you launch. New niches and cities can be added from the dropdown.
- **Leads**: filter by service score / tier / status / country / niche / has email; bulk-change status;
  export the filtered view or only the selected rows (Excel, CSV, Google Sheets). Click a lead for the full
  audit, per-service reasons, pitch (copy button), status + notes and "Do not contact".
- **Campaigns**: history with **Run again** (pre-fills the wizard, only new businesses).
- **Setup → Services**: enable/disable services, or create your own (e.g. "Social media management"):
  pick the signals that make a business need it (no Instagram, slow site, no booking…), points per signal,
  the reason text, and a pitch hook with `{niche}` / `{city}`.
- **Setup → Niches**: add/edit/delete niches, link them to services, related search phrases, high-value / appointment flags.
- **Setup → Locations**: add/delete countries (ISO code) and their cities (biggest first = default cities).
- **Settings**: workspace/agency name (sidebar + export file names), API keys (saved to `.env`, shown masked),
  hot/warm thresholds, no-contact cap, per-campaign API call caps.

Everything you customise is stored in `data/leads.db` (catalog tables) and seeded from `config/scoring.yaml`
on first start. Light/dark theme, works on phones.

**Login:** the first time you open the dashboard locally it asks you to create the admin account.
Admins manage API keys, scoring, services/niches/locations and the **Team** (add members, roles, disable, reset
password). Members run campaigns and work leads. CLI: `create-user`, `reset-password`, `backup`.

**Going live:** Docker + Caddy (automatic HTTPS) on a VPS. Step-by-step guide: [docs/DEPLOY.md](docs/DEPLOY.md).

## Command line
```bash
# Full run: Google Maps (+ directories) → audit → score → Excel
python -m leadforge run --niche dentist --country USA --city "Houston, TX" --service seo,chatbot

# Several niches, countries and cities (omit --city = top 3 cities of each country)
python -m leadforge run -n "dentist,orthodontist" -c "USA,UK,UAE" --top-cities 3

# Only hot leads, with PageSpeed and AI pitch lines, also pushed to Google Sheets
python -m leadforge run -n lawyer -c UK --city London --min-score 70 --pagespeed --ai-pitch --gsheet

# More coverage: also search related phrasings (dentist → dental clinic, orthodontist, ...)
python -m leadforge run -n dentist -c USA --city "Houston, TX" --expand
# Google organic rank check runs automatically when SERPER_API_KEY is set (1 call per niche+city);
# turn it off with --no-serp

# No API keys? Audit a list of websites you already have
python -m leadforge audit --file test_sites.txt --niche dentist --country USA

# Re-export everything in the DB, opt-out handling, stats
python -m leadforge export --min-score 70 --format xlsx,csv
python -m leadforge suppress info@example.com
python -m leadforge stats
python -m leadforge niches   # suggested niches per service
```

## API keys (add later in `.env`)
| Key | Where | Cost |
|---|---|---|
| `GOOGLE_PLACES_API_KEY` | Google Cloud Console → enable **Places API (New)** → Credentials | New accounts get trial credit, plus a monthly free usage cap. **Set a budget alert and a daily quota.** |
| `SERPER_API_KEY` | serper.dev | 2,500 free queries |
| `PAGESPEED_API_KEY` | Google Cloud → PageSpeed Insights API | Free |
| `ANTHROPIC_API_KEY` | console.anthropic.com | Pay per use. Default model `claude-opus-5-5`; set `PITCH_MODEL=claude-haiku-5-5` for the cheapest option |
| `GOOGLE_SHEETS_CREDENTIALS` | Google Cloud → Service account → JSON key; share your Sheet with that email | Free |

## Output columns
Business · Best Service · Best Score · Tier · SEO/Ads/Web/Chatbot scores · **Why Qualified** · **Pitch Line** ·
Phone · Email (+MX check) · Website · Niche · City · Country · Address · Rating · Reviews · Maps link ·
Facebook · Instagram · LinkedIn · WhatsApp · CMS · Chat widget · Google Ads tag · Meta pixel · Analytics ·
Mobile speed · SSL · Site issues · Tech detected · Sources · Found on

## Tuning
Edit `config/scoring.yaml`: every signal has points and a reason. Hot ≥ 70, Warm ≥ 40.

## Tests
```bash
python tests/test_core.py
python tests/test_sources.py
```

## Compliance
- Business contact data only. Honour opt-outs with `suppress`.
- UK/EU: legitimate interest + opt-out in every email. US: CAN-SPAM (address + opt-out); TCPA for calls/SMS.
- Directory sites prohibit scraping in their terms: keep volume low or rely on the Google APIs.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and scale-up plan.
