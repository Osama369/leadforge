"""SQLite store: every lead ever found, audit cache, run history, suppression list.
Single-file DB for the MVP; the schema maps 1:1 to Postgres when you scale."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Lead, WebsiteAudit, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    key TEXT PRIMARY KEY,
    name TEXT, niche TEXT, country TEXT, city TEXT, domain TEXT, phone TEXT, email TEXT,
    best_service TEXT, best_score INTEGER,
    data TEXT NOT NULL,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_leads_domain ON leads(domain);
CREATE INDEX IF NOT EXISTS ix_leads_search ON leads(niche, country, city);
CREATE TABLE IF NOT EXISTS audits (
    domain TEXT PRIMARY KEY, data TEXT NOT NULL, audited_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
    params TEXT, found INTEGER, qualified INTEGER, output TEXT
);
CREATE TABLE IF NOT EXISTS suppression (
    value TEXT PRIMARY KEY, reason TEXT, added_at TEXT   -- email, domain or phone that opted out
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")      # readers don't block the running campaign
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(leads)")}
        for col, ddl in (("status", "TEXT DEFAULT 'new'"), ("notes", "TEXT DEFAULT ''")):
            if col not in cols:   # migrate DBs created by v0.1
                self.db.execute(f"ALTER TABLE leads ADD COLUMN {col} {ddl}")
        self.db.commit()

    # ---- audit cache ----
    def cached_audit(self, domain: str, max_age_days: int) -> WebsiteAudit | None:
        row = self.db.execute("SELECT data, audited_at FROM audits WHERE domain=?", (domain,)).fetchone()
        if not row:
            return None
        age = datetime.now(timezone.utc) - datetime.fromisoformat(row[1])
        if age > timedelta(days=max_age_days):
            return None
        return WebsiteAudit(**json.loads(row[0]))

    def save_audit(self, domain: str, audit: WebsiteAudit) -> None:
        from dataclasses import asdict
        self.db.execute("INSERT OR REPLACE INTO audits VALUES (?,?,?)",
                        (domain, json.dumps(asdict(audit)), utcnow()))
        self.db.commit()

    # ---- leads ----
    def upsert(self, leads: list[Lead]) -> None:
        for l in leads:
            old = self.db.execute("SELECT created_at, status, notes FROM leads WHERE key=?", (l.key,)).fetchone()
            status, notes = ("new", "")
            if old:
                l.created_at, status, notes = old[0], old[1] or "new", old[2] or ""
            l.updated_at = utcnow()
            self.db.execute(
                "INSERT OR REPLACE INTO leads (key, name, niche, country, city, domain, phone, email, best_service, "
                "best_score, data, created_at, updated_at, status, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (l.key, l.name, l.niche, l.country, l.city, l.domain, l.phone, l.email,
                 l.best_service, l.best_score, json.dumps(l.to_dict()), l.created_at, l.updated_at, status, notes))
        self.db.commit()

    # ---- sales workflow (dashboard) ----
    STATUSES = ("new", "contacted", "replied", "meeting", "won", "lost")

    def set_status(self, key: str, status: str | None = None, notes: str | None = None) -> None:
        if status is not None:
            if status not in self.STATUSES:
                raise ValueError(f"status must be one of {self.STATUSES}")
            self.db.execute("UPDATE leads SET status=? WHERE key=?", (status, key))
        if notes is not None:
            self.db.execute("UPDATE leads SET notes=? WHERE key=?", (notes, key))
        self.db.commit()

    def search(self, *, niche: str | None = None, country: str | None = None, city: str | None = None,
               service: str | None = None, min_score: int = 0, tier: str | None = None,
               status: str | None = None, has_email: bool = False, text: str | None = None,
               limit: int = 500, offset: int = 0) -> tuple[list[dict], int]:
        """Rows for the dashboard: (lead dict + status/notes, total count)."""
        where, args = ["1=1"], []
        if niche:
            where.append("niche = ?"); args.append(niche)
        if country:
            where.append("country = ?"); args.append(country)
        if city:
            where.append("city LIKE ?"); args.append(f"%{city}%")
        if status:
            where.append("status = ?"); args.append(status)
        if has_email:
            where.append("email IS NOT NULL")
        if text:
            where.append("(name LIKE ? OR domain LIKE ? OR email LIKE ?)"); args += [f"%{text}%"] * 3
        score_expr = (f"CAST(json_extract(data, '$.scores.{service}.score') AS INTEGER)"
                      if service and re.fullmatch(r"[a-z0-9_]{1,40}", service) else "best_score")
        from .catalog import rules
        t = rules()["tiers"]
        lo, hi = min_score, 100
        if tier == "hot":
            lo = max(lo, t["hot"])
        elif tier == "warm":
            lo, hi = max(lo, t["warm"]), t["hot"] - 1
        elif tier == "cold":
            hi = t["warm"] - 1
        where.append(f"COALESCE({score_expr}, 0) BETWEEN ? AND ?"); args += [lo, hi]
        w = " AND ".join(where)
        total = self.db.execute(f"SELECT COUNT(*) FROM leads WHERE {w}", args).fetchone()[0]
        rows = self.db.execute(
            f"SELECT data, status, notes FROM leads WHERE {w} ORDER BY {score_expr} DESC, updated_at DESC "
            f"LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        out = []
        for data, st, nt in rows:
            d = json.loads(data)
            d["status"], d["notes"] = st or "new", nt or ""
            out.append(d)
        return out, total

    def get(self, key: str) -> Lead | None:
        row = self.db.execute("SELECT data FROM leads WHERE key=?", (key,)).fetchone()
        return Lead.from_dict(json.loads(row[0])) if row else None

    def runs(self, limit: int = 50) -> list[dict]:
        cur = self.db.execute("SELECT id, started_at, finished_at, params, found, qualified, output "
                              "FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) | {"params": json.loads(r[3] or "{}")} for r in cur.fetchall()]

    def facets(self) -> dict:
        q = lambda col: [r[0] for r in self.db.execute(
            f"SELECT {col}, COUNT(*) c FROM leads WHERE {col} IS NOT NULL GROUP BY {col} ORDER BY c DESC")]
        return {"niches": q("niche"), "countries": q("country"), "statuses": list(self.STATUSES)}

    def known_keys(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT key FROM leads")}

    def query(self, niche: str | None = None, country: str | None = None, min_score: int = 0) -> list[Lead]:
        sql, args = "SELECT data FROM leads WHERE best_score >= ?", [min_score]
        if niche:
            sql += " AND niche = ?"; args.append(niche)
        if country:
            sql += " AND country = ?"; args.append(country)
        sql += " ORDER BY best_score DESC"
        return [Lead.from_dict(json.loads(r[0])) for r in self.db.execute(sql, args)]

    # ---- suppression (opt-outs: never export these again) ----
    def suppress(self, value: str, reason: str = "opt-out") -> None:
        self.db.execute("INSERT OR REPLACE INTO suppression VALUES (?,?,?)", (value.lower().strip(), reason, utcnow()))
        self.db.commit()

    def suppressed(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT value FROM suppression")}

    # ---- runs ----
    def log_run(self, started: str, params: dict, found: int, qualified: int, output: str) -> None:
        self.db.execute("INSERT INTO runs(started_at, finished_at, params, found, qualified, output) VALUES (?,?,?,?,?,?)",
                        (started, utcnow(), json.dumps(params), found, qualified, output))
        self.db.commit()

    def stats(self) -> dict:
        from .catalog import rules
        hot = int(rules()["tiers"]["hot"])
        q = lambda s: self.db.execute(s).fetchone()[0]
        return {
            "leads": q("SELECT COUNT(*) FROM leads"),
            "hot": q(f"SELECT COUNT(*) FROM leads WHERE best_score >= {hot}"),
            "with_email": q("SELECT COUNT(*) FROM leads WHERE email IS NOT NULL"),
            "audits_cached": q("SELECT COUNT(*) FROM audits"),
            "runs": q("SELECT COUNT(*) FROM runs"),
            "suppressed": q("SELECT COUNT(*) FROM suppression"),
            "contacted": q("SELECT COUNT(*) FROM leads WHERE status NOT IN ('new')"),
            "won": q("SELECT COUNT(*) FROM leads WHERE status = 'won'"),
        }

    def overview(self, service_ids: list[str]) -> dict:
        """Numbers for the dashboard home page."""
        from .catalog import rules
        hot = int(rules()["tiers"]["hot"])
        by_service = {}
        for sid in service_ids:
            if re.fullmatch(r"[a-z0-9_]{1,40}", sid):
                by_service[sid] = self.db.execute(
                    f"SELECT COUNT(*) FROM leads WHERE CAST(json_extract(data, '$.scores.{sid}.score') AS INTEGER) >= ?",
                    (hot,)).fetchone()[0]
        pipeline = {st: 0 for st in self.STATUSES}
        for st, c in self.db.execute("SELECT COALESCE(status,'new'), COUNT(*) FROM leads GROUP BY 1"):
            pipeline[st] = c
        by_country = [{"code": c, "total": t, "hot": h} for c, t, h in self.db.execute(
            "SELECT country, COUNT(*), SUM(best_score >= ?) FROM leads GROUP BY country ORDER BY 2 DESC LIMIT 8", (hot,))]
        by_niche = [{"niche": n, "total": t, "hot": h} for n, t, h in self.db.execute(
            "SELECT niche, COUNT(*), SUM(best_score >= ?) FROM leads GROUP BY niche ORDER BY 3 DESC, 2 DESC LIMIT 8", (hot,))]
        top, _ = self.search(min_score=hot, status="new", limit=6)
        return {"stats": self.stats(), "hot_by_service": by_service, "pipeline": pipeline,
                "by_country": by_country, "by_niche": by_niche, "top_hot": top, "runs": self.runs(6)}

    def bulk_status(self, keys: list[str], status: str) -> int:
        if status not in self.STATUSES:
            raise ValueError(f"status must be one of {self.STATUSES}")
        cur = self.db.executemany("UPDATE leads SET status=? WHERE key=?", [(status, k) for k in keys])
        self.db.commit()
        return cur.rowcount
