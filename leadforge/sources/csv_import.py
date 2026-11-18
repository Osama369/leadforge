"""Import businesses from a CSV/TXT file (works with zero API keys).

CSV columns (any subset, header required): name, website, phone, city, address, rating, review_count
TXT: one website URL per line.
Useful for: testing, auditing a list you bought, re-scoring old leads.
"""
from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import urlparse

from ..models import RawLead, SearchQuery


def _num(v, cast):
    try:
        return cast(v) if v not in (None, "") else None
    except ValueError:
        return None


def load_file(path: Path, q: SearchQuery) -> list[RawLead]:
    leads: list[RawLead] = []
    if path.suffix.lower() == ".txt":
        for line in path.read_text(encoding="utf-8").splitlines():
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            if not url.startswith("http"):
                url = "https://" + url
            name = urlparse(url).netloc.removeprefix("www.")
            leads.append(RawLead(name=name, source="import", niche=q.niche, country=q.country,
                                 city=q.city, website=url))
        return leads
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            site = row.get("website") or None
            name = row.get("name") or (urlparse(site).netloc if site else "")
            if not name:
                continue
            leads.append(RawLead(
                name=name, source="import", niche=row.get("niche") or q.niche,
                country=q.country, city=row.get("city") or q.city, address=row.get("address") or None,
                phone=row.get("phone") or None, website=site,
                rating=_num(row.get("rating"), float), review_count=_num(row.get("review_count"), int),
            ))
    return leads
