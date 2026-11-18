"""Exports: Excel (.xlsx with Hot/Warm/All/Summary tabs), CSV, Google Sheets."""
from __future__ import annotations

import re

import csv
import logging
from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .config import settings
from .geo import SERVICES, country_name
from .models import Lead

log = logging.getLogger(__name__)

HEAD = [("Business", 28), ("Best Service", 16), ("Best Score", 10), ("Tier", 8)]
TAIL = [
    ("Status", 11), ("Notes", 30), ("Why Qualified", 60), ("Pitch Line", 70),
    ("Phone", 16), ("Email", 28), ("Email Check", 11), ("Website", 30),
    ("Niche", 14), ("City", 14), ("Country", 14), ("Address", 34),
    ("Rating", 7), ("Reviews", 8), ("Maps Link", 14),
    ("Facebook", 14), ("Instagram", 14), ("LinkedIn", 14), ("WhatsApp", 14),
    ("CMS", 12), ("Chat Widget", 13), ("Google Ads Tag", 9), ("Meta Pixel", 9), ("Analytics", 9),
    ("Mobile Speed", 9), ("SSL", 6), ("Google Rank", 9), ("Top Competitor", 22), ("Site Issues", 50), ("Tech Detected", 30),
    ("Sources", 22), ("Found On", 18),
]
SCORE_START = len(HEAD) + 1   # first per-service score column (1-based)


def columns() -> list[tuple[str, int]]:
    """Header row: one score column per enabled service (custom services included)."""
    return HEAD + [(label, max(7, min(len(label) + 2, 14))) for label in SERVICES.values()] + TAIL


def _yn(v: bool | None) -> str:
    return "" if v is None else ("Yes" if v else "No")


def lead_row(l: Lead) -> list:
    a = l.audit
    best = l.scores.get(l.best_service) if l.best_service else None
    sc = lambda s: l.scores[s].score if s in l.scores else ""
    socials = a.socials if a else {}
    ok = bool(a and a.reachable)
    return [
        l.name, SERVICES.get(l.best_service, ""), l.best_score, (best.tier.upper() if best else ""),
        *[sc(sid) for sid in SERVICES],
        l.status or "new", l.notes or "",
        " • ".join(best.reasons[:4]) if best else "", l.pitch or "",
        l.phone or "", l.email or "", l.email_status or "", l.website or "",
        l.niche, l.city or "", country_name(l.country), l.address or "",
        l.rating if l.rating is not None else "", l.review_count if l.review_count is not None else "",
        l.maps_url or "",
        socials.get("facebook", ""), socials.get("instagram", ""), socials.get("linkedin", ""),
        socials.get("whatsapp", ""),
        (a.cms or "") if ok else "", (a.chat_widget or "None") if ok else "",
        _yn(a.has_google_ads_tag) if ok else "", _yn(a.has_meta_pixel) if ok else "",
        _yn(a.has_ga4 or a.has_gtm) if ok else "",
        (a.pagespeed_mobile if a.pagespeed_mobile is not None else "") if ok else "",
        _yn(a.https) if ok else "",
        (l.serp_rank or "20+") if l.serp_checked else "", l.serp_leader or "",
        "; ".join(a.seo_issues) if a else ("No website" if not l.website else ""),
        ", ".join(a.tech) if ok else "",
        ", ".join(l.sources), l.created_at[:16].replace("T", " "),
    ]


def _col_index(cols, *names):
    idx = {name: i for i, (name, _) in enumerate(cols, 1)}
    return [idx[n] for n in names]


HEADER_FILL = PatternFill("solid", fgColor="1F2937")
TIER_FILLS = {"HOT": "FDE2E1", "WARM": "FEF3C7", "COLD": "E5E7EB"}


def _sheet(ws, leads: list[Lead]) -> None:
    COLUMNS = columns()
    WRAP_COLS = _col_index(COLUMNS, "Why Qualified", "Pitch Line", "Address", "Site Issues", "Notes")
    LINK_COLS = _col_index(COLUMNS, "Website", "Maps Link")
    ws.append([c for c, _ in COLUMNS])
    for i, (_, w) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=i)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    for l in leads:
        ws.append(lead_row(l))
    n = len(leads) + 1
    ws.freeze_panes = "B2"
    if not leads:
        ws.append(["No leads in this tier yet"])
        return
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{n}"
    # colour scores: hot red-ish, warm amber
    score_cols = ["C"] + [get_column_letter(SCORE_START + i) for i in range(len(SERVICES))]
    for col in score_cols:
        rng = f"{col}2:{col}{n}"
        ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThanOrEqual", formula=["70"],
                                                       fill=PatternFill("solid", fgColor="FCA5A5")))
        ws.conditional_formatting.add(rng, CellIsRule(operator="between", formula=["40", "69"],
                                                       fill=PatternFill("solid", fgColor="FDE68A")))
    for r in range(2, n + 1):
        tier = ws.cell(row=r, column=4).value
        if tier in TIER_FILLS:
            ws.cell(row=r, column=4).fill = PatternFill("solid", fgColor=TIER_FILLS[tier])
        for c in WRAP_COLS:
            ws.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical="top")
        for c in LINK_COLS:  # website + maps link clickable
            v = ws.cell(row=r, column=c).value
            if v:
                ws.cell(row=r, column=c).hyperlink = v
                ws.cell(row=r, column=c).font = Font(color="2563EB", underline="single")


def to_excel(leads: list[Lead], path: Path, run_info: dict | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    leads = sorted(leads, key=lambda l: l.best_score, reverse=True)
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    tiers = Counter((l.scores[l.best_service].tier if l.best_service else "cold") for l in leads)
    rows = [("LeadForge report", ""), ("", "")]
    for k, v in (run_info or {}).items():
        rows.append((k, str(v)))
    rows += [("", ""), ("Total leads", len(leads)), ("Hot (70+)", tiers["hot"]), ("Warm (40-69)", tiers["warm"]),
             ("Cold", tiers["cold"]), ("With email", sum(1 for l in leads if l.email)),
             ("With phone", sum(1 for l in leads if l.phone)),
             ("No website", sum(1 for l in leads if not l.website)), ("", "")]
    for svc, label in SERVICES.items():
        rows.append((f"{label}: hot leads", sum(1 for l in leads if svc in l.scores and l.scores[svc].tier == "hot")))
    for r in rows:
        summary.append(list(r))
    summary["A1"].font = Font(bold=True, size=14)
    summary.column_dimensions["A"].width, summary.column_dimensions["B"].width = 30, 50

    groups = [("Hot", [l for l in leads if l.best_score >= 70]),
              ("Warm", [l for l in leads if 40 <= l.best_score < 70]),
              ("All Leads", leads)]
    for svc, label in SERVICES.items():
        g = sorted([l for l in leads if svc in l.scores and l.scores[svc].score >= 40],
                   key=lambda l: l.scores[svc].score, reverse=True)
        if g:
            groups.append((f"{svc.upper()} targets", g))
    for title, g in groups:
        _sheet(wb.create_sheet(title), g)
    wb.save(path)
    return path


def to_csv(leads: list[Lead], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in columns()])
        for l in sorted(leads, key=lambda l: l.best_score, reverse=True):
            w.writerow(lead_row(l))
    return path


def to_google_sheet(leads: list[Lead], sheet_id: str | None, title: str) -> str:
    """Push to Google Sheets with a service account. Share the sheet with the
    service-account email first (or omit sheet_id to create a new spreadsheet)."""
    if not settings.gsheets_credentials:
        raise RuntimeError("Set GOOGLE_SHEETS_CREDENTIALS=path/to/service-account.json in .env")
    import gspread

    from .catalog import rules
    gc = gspread.service_account(filename=settings.gsheets_credentials)
    sheet_id = sheet_id or settings.gsheet_id
    if sheet_id:
        m = re.search(r"/d/([a-zA-Z0-9_-]+)", sheet_id)   # accept a full sheet URL too
        sh = gc.open_by_key(m.group(1) if m else sheet_id.strip())
    else:
        sh = gc.create(title)   # note: new service accounts often have no Drive storage for this
    t = rules()["tiers"]
    leads = sorted(leads, key=lambda l: l.best_score, reverse=True)
    header = [c for c, _ in columns()]
    for tab, g in (("Hot", [l for l in leads if l.best_score >= t["hot"]]),
                   ("Warm", [l for l in leads if t["warm"] <= l.best_score < t["hot"]]),
                   ("All Leads", leads)):
        try:
            ws = sh.worksheet(tab)
            ws.clear()
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(tab, rows=max(len(g) + 10, 100), cols=len(header))
        ws.update([header] + [[str(v) for v in lead_row(l)] for l in g], value_input_option="USER_ENTERED")
        ws.freeze(rows=1, cols=1)
        ws.format("1:1", {"textFormat": {"bold": True}})
    try:
        sh.del_worksheet(sh.worksheet("Sheet1"))
    except Exception:
        pass
    return sh.url
