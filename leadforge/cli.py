"""LeadForge command line.

  python -m leadforge run --niche dentist --country USA --city Houston --service seo,chatbot
  python -m leadforge audit --file sites.txt --niche dentist --country UK
  python -m leadforge export --min-score 70 --gsheet
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import settings
from .export import to_csv, to_excel, to_google_sheet
from .geo import SERVICE_NICHES, SERVICES, COUNTRIES, default_cities, expand_niche, normalize_country
from .models import SearchQuery, utcnow
from .pipeline import RunOptions, build_queries, run_pipeline
from .storage import Store

app = typer.Typer(add_completion=False, help="LeadForge: find, audit & qualify agency leads.")
console = Console()


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()] if v else []


def _cities(v: str | None) -> list[str]:
    """'Houston, TX, Dallas, TX' or 'Houston;Dallas' -> ['Houston, TX', 'Dallas, TX'].
    A 2-letter uppercase token (US state) is glued back to the city before it."""
    if not v:
        return []
    if ";" in v or "|" in v:
        return [x.strip() for x in re.split(r"[;|]", v) if x.strip()]
    out: list[str] = []
    for part in (p.strip() for p in v.split(",")):
        if not part:
            continue
        if out and re.fullmatch(r"[A-Z]{2}", part):
            out[-1] = f"{out[-1]}, {part}"
        else:
            out.append(part)
    return out


def _services(v: str | None) -> list[str] | None:
    s = [x.lower() for x in _split(v)]
    bad = [x for x in s if x not in SERVICES]
    if bad:
        raise typer.BadParameter(f"Unknown service {bad}. Use: {', '.join(SERVICES)}")
    return s or None


def _slug(*parts: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", "-".join(p for p in parts if p).lower()).strip("-")[:60]


def _report(leads, log_lines, out_paths):
    for line in log_lines:
        console.print(f"[dim]• {line}[/dim]")
    t = Table(title=f"Top leads ({len(leads)} total)", show_lines=False)
    for c in ("Business", "City", "Best", "Score", "Email", "Phone", "Why"):
        t.add_column(c, overflow="fold", max_width=40 if c == "Why" else 26)
    for l in sorted(leads, key=lambda x: x.best_score, reverse=True)[:15]:
        s = l.scores.get(l.best_service)
        color = "red" if l.best_score >= 70 else "yellow" if l.best_score >= 40 else "white"
        t.add_row(l.name, l.city or "", l.best_service or "", f"[{color}]{l.best_score}[/{color}]",
                  l.email or "", l.phone or "", "; ".join(s.reasons[:2]) if s else "")
    console.print(t)
    for p in out_paths:
        console.print(f"[green]Saved:[/green] {p}")


def _export(leads, name: str, fmt: str, gsheet: bool, sheet_id: str | None, info: dict) -> list[str]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    paths = []
    if "xlsx" in fmt:
        paths.append(str(to_excel(leads, settings.output_dir / f"{name}-{stamp}.xlsx", info)))
    if "csv" in fmt:
        paths.append(str(to_csv(leads, settings.output_dir / f"{name}-{stamp}.csv")))
    if gsheet:
        try:
            paths.append(to_google_sheet(leads, sheet_id, f"LeadForge {name} {stamp}"))
        except Exception as e:
            console.print(f"[red]Google Sheets export failed:[/red] {e}")
    return paths


@app.command()
def run(
    niche: str = typer.Option(..., "--niche", "-n", help="Comma-separated: 'dentist,orthodontist'"),
    country: str = typer.Option("USA", "--country", "-c", help="USA, UK, UAE, CA, AU, KSA (comma-separated)"),
    city: str = typer.Option(None, "--city", help="Comma-separated cities. Omit = top cities of the country"),
    top_cities: int = typer.Option(3, help="How many default cities when --city is omitted"),
    service: str = typer.Option(None, "--service", "-s", help="seo,ads,web,chatbot (default: all)"),
    source: str = typer.Option(None, help="Limit sources: google_places,serper_places,yellowpages_us,yell_uk,yellowpages_uae"),
    limit: int = typer.Option(60, help="Max businesses per source per query"),
    pagespeed: bool = typer.Option(False, "--pagespeed", help="Add Google PageSpeed mobile score (slower)"),
    ai_pitch: bool = typer.Option(False, "--ai-pitch", help="Write pitch lines with Claude (needs ANTHROPIC_API_KEY)"),
    expand: bool = typer.Option(False, "--expand", help="Also search related phrasings (dentist -> dental clinic, orthodontist...)"),
    serp: bool = typer.Option(True, "--serp/--no-serp", help="Check Google organic rank (needs SERPER_API_KEY; 1 call per niche+city)"),
    min_score: int = typer.Option(0, help="Only export leads with best score >= this"),
    only_new: bool = typer.Option(False, "--only-new", help="Skip leads found in previous runs"),
    fmt: str = typer.Option("xlsx", "--format", help="xlsx, csv or xlsx,csv"),
    gsheet: bool = typer.Option(False, "--gsheet", help="Also push to Google Sheets"),
    sheet_id: str = typer.Option(None, help="Existing Google Sheet ID to overwrite"),
    verbose: bool = typer.Option(False, "-v"),
):
    """Discover businesses, audit their websites, score and export qualified leads."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING, format="%(levelname)s %(message)s")
    queries = build_queries(_split(niche), _split(country), _cities(city) or None, top_cities, limit, expand)
    opts = RunOptions(queries=queries, services=_services(service), sources=_split(source) or None,
                      pagespeed=pagespeed, ai_pitch=ai_pitch, min_score=min_score, only_new=only_new,
                      serp=serp)
    console.print(f"[bold]LeadForge[/bold]: {len(queries)} searches "
                  f"({', '.join(sorted({q.niche for q in queries}))} | {', '.join(sorted({q.location_text for q in queries}))})")
    started = utcnow()
    leads, log_lines = asyncio.run(run_pipeline(opts))
    info = {"Niche": niche, "Country": country, "Cities": city or f"top {top_cities}",
            "Services": service or "all", "Run at": started}
    paths = _export(leads, _slug(niche, country, city or ""), fmt, gsheet, sheet_id, info) if leads else []
    Store(settings.db_path).log_run(started, info, len(leads), sum(1 for l in leads if l.best_score >= 70), "; ".join(paths))
    _report(leads, log_lines, paths)


@app.command()
def audit(
    file: Path = typer.Option(..., "--file", "-f", exists=True, help=".txt (one URL per line) or .csv (name,website,phone,...)"),
    niche: str = typer.Option("business", "--niche", "-n"),
    country: str = typer.Option("USA", "--country", "-c"),
    city: str = typer.Option(None, "--city"),
    service: str = typer.Option(None, "--service", "-s"),
    pagespeed: bool = typer.Option(False, "--pagespeed"),
    ai_pitch: bool = typer.Option(False, "--ai-pitch"),
    fmt: str = typer.Option("xlsx", "--format"),
    gsheet: bool = typer.Option(False, "--gsheet"),
    sheet_id: str = typer.Option(None),
):
    """Audit + score a list of websites you already have (no API keys needed)."""
    q = SearchQuery(niche=niche, country=normalize_country(country), city=city)
    opts = RunOptions(queries=[q], services=_services(service), import_file=file,
                      pagespeed=pagespeed, ai_pitch=ai_pitch)
    leads, log_lines = asyncio.run(run_pipeline(opts))
    paths = _export(leads, _slug("audit", file.stem), fmt, gsheet, sheet_id,
                    {"Source file": file.name, "Niche": niche, "Country": country})
    _report(leads, log_lines, paths)


@app.command()
def export(
    niche: str = typer.Option(None, "--niche", "-n"),
    country: str = typer.Option(None, "--country", "-c"),
    min_score: int = typer.Option(40),
    fmt: str = typer.Option("xlsx", "--format"),
    gsheet: bool = typer.Option(False, "--gsheet"),
    sheet_id: str = typer.Option(None),
):
    """Re-export leads from the database (all past runs)."""
    leads = Store(settings.db_path).query(niche, normalize_country(country) if country else None, min_score)
    if not leads:
        console.print("No leads match."); raise typer.Exit()
    paths = _export(leads, _slug("export", niche or "all", country or ""), fmt, gsheet, sheet_id,
                    {"Filter": f"niche={niche} country={country} min_score={min_score}"})
    _report(leads, [], paths)


@app.command()
def suppress(value: str = typer.Argument(..., help="Email, domain or E.164 phone that opted out")):
    """Add an opt-out. Suppressed contacts are never exported again (GDPR/CAN-SPAM)."""
    Store(settings.db_path).suppress(value)
    console.print(f"Suppressed {value}")


@app.command()
def stats():
    """Database totals."""
    for k, v in Store(settings.db_path).stats().items():
        console.print(f"{k:>15}: {v}")


@app.command()
def niches():
    """Suggested target niches per service, plus supported countries."""
    for svc, label in SERVICES.items():
        console.print(f"[bold]{label}[/bold] ({svc}): {', '.join(SERVICE_NICHES[svc])}")
    console.print("\n[bold]Countries:[/bold] " + ", ".join(f"{k} ({v['name']})" for k, v in COUNTRIES.items()))


@app.command()
def doctor():
    """Show which API keys / features are configured."""
    rows = [
        ("Google Places API", bool(settings.google_places_key), "Main source: Maps businesses with phone + website"),
        ("Serper.dev", bool(settings.serper_key), "Backup Maps source (2,500 free queries)"),
        ("PageSpeed key", bool(settings.pagespeed_key), "Optional: higher PageSpeed quota"),
        ("Anthropic (AI pitch)", bool(settings.anthropic_key), f"Optional: --ai-pitch with {settings.pitch_model}"),
        ("Google Sheets", bool(settings.gsheets_credentials), "Optional: --gsheet export"),
        ("Proxy", bool(settings.proxy_url), "Optional: for directory scraping at volume"),
    ]
    t = Table(title=f"LeadForge {__version__} configuration")
    for c in ("Feature", "Status", "Notes"):
        t.add_column(c)
    for name, ok, note in rows:
        t.add_row(name, "[green]ready[/green]" if ok else "[yellow]not set[/yellow]", note)
    console.print(t)
    console.print("Directory scrapers (Yellow Pages US, Yell UK, Yellow Pages UAE) and website audits need no keys.")


@app.command()
def serve(host: str = typer.Option("127.0.0.1"), port: int = typer.Option(8000)):
    """Start the web dashboard (http://127.0.0.1:8000). Always one worker: campaigns run in-process."""
    import os

    import uvicorn
    console.print(f"[bold]LeadForge dashboard[/bold] at http://{host}:{port}"
                  + (" [green](production)[/green]" if settings.production else ""))
    uvicorn.run("leadforge.web.app:app", host=host, port=port, workers=1,
                log_level="info" if settings.production else "warning",
                proxy_headers=True, forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"))


@app.command("create-user")
def create_user(
    email: str = typer.Option(..., "--email", help="Login email"),
    name: str = typer.Option("", "--name"),
    role: str = typer.Option("admin", "--role", help="admin or member"),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True,
                                 help="Asked interactively if omitted"),
):
    """Create a dashboard login (the first admin on a server is made this way)."""
    from .auth import Auth
    try:
        u = Auth().create_user(email, password, role, name)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    console.print(f"[green]Created[/green] {u['role']} {u['email']}")


@app.command("reset-password")
def reset_password(
    email: str = typer.Option(..., "--email"),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
):
    """Set a new password for a user (and log them out everywhere)."""
    from .auth import Auth
    a = Auth()
    u = next((x for x in a.users() if x["email"] == email.strip().lower()), None)
    if not u:
        raise typer.BadParameter(f"No user {email}")
    try:
        a.update_user(u["id"], password=password, active=True)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    console.print(f"[green]Password updated[/green] for {u['email']}")


@app.command()
def backup(keep: int = typer.Option(14, help="How many backups to keep")):
    """Consistent copy of the database to data/backups (safe while the server runs)."""
    import sqlite3
    dest_dir = settings.db_path.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"leads-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(settings.db_path)
    out = sqlite3.connect(dest)
    with out:
        src.backup(out)
    out.close(); src.close()
    old = sorted(dest_dir.glob("leads-*.db"))[:-keep] if keep > 0 else []
    for f in old:
        f.unlink()
    console.print(f"[green]Backup saved:[/green] {dest} ({len(old)} old removed)")


def main():
    from .catalog import apply
    apply()   # load the editable catalog (services, niches, countries) before any command
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        for stream in (sys.stdout, sys.stderr):   # lead names / reasons contain ★, Arabic, etc.
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
    app()


if __name__ == "__main__":
    main()
