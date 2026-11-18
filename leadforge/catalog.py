"""Workspace catalog: services you sell, target niches, countries & cities, scoring settings.

Everything the dashboard lets a client customise lives here (SQLite, same DB as leads).
On first run it is seeded from config/scoring.yaml + geo.py defaults. `apply()` pushes the
catalog into the in-memory registries (geo.COUNTRIES, geo.SERVICES, ...) that the rest of
the engine reads, so CLI and dashboard always use the same definitions.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading

import yaml

from . import geo
from .config import ROOT, settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS cat_services (
    id TEXT PRIMARY KEY, label TEXT NOT NULL, description TEXT DEFAULT '',
    base INTEGER DEFAULT 15, signals TEXT NOT NULL DEFAULT '{}', pitch_hook TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1, builtin INTEGER DEFAULT 0, sort INTEGER DEFAULT 100
);
CREATE TABLE IF NOT EXISTS cat_niches (
    name TEXT PRIMARY KEY, services TEXT NOT NULL DEFAULT '[]', synonyms TEXT NOT NULL DEFAULT '[]',
    high_value INTEGER DEFAULT 0, appointment INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cat_countries (
    code TEXT PRIMARY KEY, name TEXT NOT NULL, gl TEXT NOT NULL, cities TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS cat_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DEFAULT_HOOKS = {
    "seo": "We help {niche}s in {city} rank in the Google top 3 so new customers find them first.",
    "ads": "We run Google & Meta ads for {niche}s that bring booked customers, not just clicks.",
    "web": "We build fast, mobile-first websites for {niche}s that turn visitors into calls.",
    "chatbot": "We set up AI assistants that answer and book {niche} customers 24/7, even at 2am.",
}
DEFAULT_DESC = {
    "seo": "Rank higher on Google search & Maps",
    "ads": "Google & Meta paid campaigns",
    "web": "New websites & redesigns",
    "chatbot": "AI chat, booking & WhatsApp agents",
}
_lock = threading.Lock()
_rules_cache: dict | None = None


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


class Catalog:
    def __init__(self):
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(settings.db_path, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        if not self.db.execute("SELECT 1 FROM cat_kv WHERE key='seeded'").fetchone():
            self._seed()

    # ------------------------------------------------------------------ seed
    def _seed(self) -> None:
        y = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))
        for i, (sid, cfg) in enumerate(y["services"].items()):
            self.db.execute(
                "INSERT OR IGNORE INTO cat_services VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, geo.SERVICES.get(sid, sid.title()), DEFAULT_DESC.get(sid, ""), cfg.get("base", 15),
                 json.dumps(cfg.get("signals", {})), DEFAULT_HOOKS.get(sid, ""), 1, 1, i))
        hv, ap = y.get("high_value_niches", []), y.get("appointment_niches", [])
        niches: dict[str, set] = {}
        for svc, names in geo.SERVICE_NICHES.items():
            for n in names:
                niches.setdefault(n, set()).add(svc)
        for n in geo.NICHE_SYNONYMS:
            niches.setdefault(n, set())
        for n, svcs in niches.items():
            self.db.execute("INSERT OR IGNORE INTO cat_niches VALUES (?,?,?,?,?)",
                            (n, json.dumps(sorted(svcs)), json.dumps(geo.NICHE_SYNONYMS.get(n, [])),
                             int(any(k in n for k in hv)), int(any(k in n for k in ap))))
        for code, c in geo.COUNTRIES.items():
            self.db.execute("INSERT OR IGNORE INTO cat_countries VALUES (?,?,?,?,?)",
                            (code, c["name"], c["gl"], json.dumps(c["cities"]), 1))
        for k, v in {"hot": y["tiers"]["hot"], "warm": y["tiers"]["warm"], "no_contact_cap": y["no_contact_cap"],
                     "high_value_keywords": hv, "appointment_keywords": ap,
                     "workspace_name": "LeadForge", "seeded": True}.items():
            self.db.execute("INSERT OR REPLACE INTO cat_kv VALUES (?,?)", (k, json.dumps(v)))
        self.db.commit()

    # ------------------------------------------------------------------ kv
    def kv(self, key: str, default=None):
        r = self.db.execute("SELECT value FROM cat_kv WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_kv(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO cat_kv VALUES (?,?)", (key, json.dumps(value)))
        self.db.commit()
        apply(self)

    # ------------------------------------------------------------------ services
    def services(self, enabled_only: bool = False) -> list[dict]:
        q = "SELECT * FROM cat_services" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY sort, label"
        out = []
        for r in self.db.execute(q):
            d = dict(r)
            d["signals"] = json.loads(d["signals"])
            d["enabled"], d["builtin"] = bool(d["enabled"]), bool(d["builtin"])
            out.append(d)
        return out

    def save_service(self, data: dict, sid: str | None = None) -> str:
        from .scoring import SIGNALS
        label = (data.get("label") or "").strip()
        if not label:
            raise ValueError("Service name is required")
        sid = sid or slugify(data.get("id") or label)
        if not sid:
            raise ValueError("Service name must contain letters or numbers")
        signals = {}
        for name, rule in (data.get("signals") or {}).items():
            if name not in SIGNALS:
                raise ValueError(f"Unknown signal '{name}'")
            signals[name] = {"points": max(-100, min(100, int(rule.get("points", 0)))),
                             "reason": (rule.get("reason") or SIGNALS[name]["reason"]).strip()}
        existing = self.db.execute("SELECT builtin, sort FROM cat_services WHERE id=?", (sid,)).fetchone()
        if not existing and data.get("_create") and self.db.execute(
                "SELECT 1 FROM cat_services WHERE id=?", (sid,)).fetchone():
            raise ValueError(f"A service with id '{sid}' already exists")
        self.db.execute(
            "INSERT OR REPLACE INTO cat_services VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, label, (data.get("description") or "").strip(), int(data.get("base", 15)),
             json.dumps(signals), (data.get("pitch_hook") or "").strip(), int(bool(data.get("enabled", True))),
             existing["builtin"] if existing else 0, existing["sort"] if existing else 100))
        self.db.commit()
        apply(self)
        return sid

    def delete_service(self, sid: str) -> None:
        r = self.db.execute("SELECT builtin FROM cat_services WHERE id=?", (sid,)).fetchone()
        if not r:
            raise KeyError(sid)
        if r["builtin"]:
            raise ValueError("Built-in services can't be deleted; disable them instead")
        self.db.execute("DELETE FROM cat_services WHERE id=?", (sid,))
        for n in self.niches():   # unlink from niches
            if sid in n["services"]:
                self.save_niche({**n, "services": [s for s in n["services"] if s != sid]}, n["name"])
        self.db.commit()
        apply(self)

    # ------------------------------------------------------------------ niches
    def niches(self) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT * FROM cat_niches ORDER BY name"):
            d = dict(r)
            d["services"], d["synonyms"] = json.loads(d["services"]), json.loads(d["synonyms"])
            d["high_value"], d["appointment"] = bool(d["high_value"]), bool(d["appointment"])
            out.append(d)
        return out

    def save_niche(self, data: dict, old_name: str | None = None) -> str:
        name = re.sub(r"\s+", " ", (data.get("name") or "").strip().lower())
        if not name or len(name) > 60:
            raise ValueError("Niche name is required (max 60 characters)")
        syn = sorted({re.sub(r"\s+", " ", s.strip().lower()) for s in data.get("synonyms", []) if s.strip()} - {name})
        if old_name and old_name != name:
            self.db.execute("DELETE FROM cat_niches WHERE name=?", (old_name,))
        self.db.execute("INSERT OR REPLACE INTO cat_niches VALUES (?,?,?,?,?)",
                        (name, json.dumps(sorted(set(data.get("services", [])))), json.dumps(syn),
                         int(bool(data.get("high_value"))), int(bool(data.get("appointment")))))
        self.db.commit()
        apply(self)
        return name

    def delete_niche(self, name: str) -> None:
        self.db.execute("DELETE FROM cat_niches WHERE name=?", (name,))
        self.db.commit()
        apply(self)

    # ------------------------------------------------------------------ countries
    def countries(self) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT * FROM cat_countries ORDER BY name"):
            d = dict(r)
            d["cities"], d["enabled"] = json.loads(d["cities"]), bool(d["enabled"])
            out.append(d)
        return out

    def save_country(self, data: dict, old_code: str | None = None) -> str:
        code = (data.get("code") or "").strip().upper()
        name = (data.get("name") or "").strip()
        if not re.fullmatch(r"[A-Z]{2}", code):
            raise ValueError("Country code must be 2 letters (ISO), e.g. PK, DE, QA")
        if not name:
            raise ValueError("Country name is required")
        gl = (data.get("gl") or code).strip().lower()
        seen, cities = set(), []
        for c in data.get("cities", []):
            c = re.sub(r"\s+", " ", c.strip())
            if c and c.lower() not in seen:
                seen.add(c.lower())
                cities.append(c)
        if old_code and old_code != code:
            self.db.execute("DELETE FROM cat_countries WHERE code=?", (old_code,))
        self.db.execute("INSERT OR REPLACE INTO cat_countries VALUES (?,?,?,?,?)",
                        (code, name, gl, json.dumps(cities), int(bool(data.get("enabled", True)))))
        self.db.commit()
        apply(self)
        return code

    def delete_country(self, code: str) -> None:
        self.db.execute("DELETE FROM cat_countries WHERE code=?", (code,))
        self.db.commit()
        apply(self)

    # ------------------------------------------------------------------ export for engine
    def rules(self) -> dict:
        return {
            "tiers": {"hot": int(self.kv("hot", 70)), "warm": int(self.kv("warm", 40))},
            "no_contact_cap": int(self.kv("no_contact_cap", 30)),
            "services": {s["id"]: {"base": s["base"], "signals": s["signals"]} for s in self.services(True)},
            "high_value_niches": self.kv("high_value_keywords", []),
            "appointment_niches": self.kv("appointment_keywords", []),
            "high_value_exact": [n["name"] for n in self.niches() if n["high_value"]],
            "appointment_exact": [n["name"] for n in self.niches() if n["appointment"]],
        }


def apply(cat: Catalog | None = None) -> None:
    """Load catalog into the in-memory registries used by the engine (mutated in place)."""
    global _rules_cache
    with _lock:
        cat = cat or Catalog()
        countries = {c["code"]: {"name": c["name"], "gl": c["gl"], "cities": c["cities"]}
                     for c in cat.countries() if c["enabled"]}
        geo.COUNTRIES.clear(); geo.COUNTRIES.update(countries)
        services = cat.services(True)
        geo.SERVICES.clear(); geo.SERVICES.update({s["id"]: s["label"] for s in services})
        niches = cat.niches()
        geo.SERVICE_NICHES.clear()
        for s in services:
            geo.SERVICE_NICHES[s["id"]] = [n["name"] for n in niches if s["id"] in n["services"]]
        geo.NICHE_SYNONYMS.clear(); geo.NICHE_SYNONYMS.update({n["name"]: n["synonyms"] for n in niches})
        geo.PITCH_HOOKS.clear(); geo.PITCH_HOOKS.update({s["id"]: s["pitch_hook"] for s in services})
        _rules_cache = cat.rules()


def rules() -> dict:
    if _rules_cache is None:
        apply()
    return _rules_cache
