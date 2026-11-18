"""LeadForge dashboard API (FastAPI). Start with:  python -m leadforge serve

Every /api route needs a logged-in user (session cookie) except login, setup and health.
Admins manage API keys, scoring, services/niches/locations and the team; members run
campaigns and work leads. Mutating requests must carry `X-Requested-With: leadforge` (CSRF guard).
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .. import __version__
from ..auth import Auth, LoginLimiter
from ..catalog import Catalog, apply
from ..config import EDITABLE_ENV, settings, update_env
from ..export import to_csv, to_excel, to_google_sheet
from ..geo import COUNTRIES, SERVICES, normalize_country
from ..models import Lead, utcnow
from ..pipeline import ProgressTracker, RunOptions, build_queries, run_pipeline
from ..scoring import SIGNALS
from ..sources.directories import SPECS
from ..storage import Store

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="LeadForge", version=__version__,
              docs_url=None if settings.production else "/docs", redoc_url=None,
              openapi_url=None if settings.production else "/openapi.json")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

COOKIE = "lf_session"
PUBLIC = {"/api/auth/login", "/api/auth/setup", "/api/auth/me", "/api/health"}
# admin-only: API keys, scoring, the catalog (any write), the team
ADMIN_PREFIXES = ("/api/settings", "/api/users")
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
limiter = LoginLimiter()


@app.on_event("startup")
def _startup() -> None:
    apply()
    Auth()   # create user tables


def store() -> Store:
    return Store(settings.db_path)


def bad_request(e: Exception):
    raise HTTPException(422, str(e))


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@app.middleware("http")
async def guard(request: Request, call_next):
    path, method = request.url.path, request.method
    if path.startswith("/api/"):
        if method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "leadforge":
            return JSONResponse({"detail": "Missing CSRF header"}, status_code=403)
        if path not in PUBLIC:
            user = Auth().session_user(request.cookies.get(COOKIE))
            if not user:
                return JSONResponse({"detail": "Please log in"}, status_code=401)
            admin_only = path.startswith(ADMIN_PREFIXES) or (
                path.startswith("/api/catalog/") and method != "GET")
            if admin_only and user["role"] != "admin":
                return JSONResponse({"detail": "Only admins can do this"}, status_code=403)
            request.state.user = user
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# ================================================================== auth
class LoginReq(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class SetupReq(LoginReq):
    name: str = ""


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, max_age=settings.session_days * 86400, httponly=True,
                    secure=settings.cookie_secure, samesite="lax", path="/")


@app.get("/api/health")
def health():
    store().db.execute("SELECT 1")
    return {"ok": True, "version": __version__}


@app.get("/api/auth/me")
def me(request: Request):
    a = Auth()
    user = a.session_user(request.cookies.get(COOKIE))
    if user:
        return {"user": user}
    # first run: no users yet -> the UI shows "create admin" (disabled in production: use the CLI)
    return JSONResponse({"detail": "Please log in", "setup": a.count() == 0 and not settings.production},
                        status_code=401)


@app.post("/api/auth/setup")
def setup(req: SetupReq, request: Request, response: Response):
    a = Auth()
    if settings.production or a.count():
        raise HTTPException(403, "Setup is closed. Ask an admin for an account.")
    try:
        user = a.create_user(req.email, req.password, "admin", req.name)
    except ValueError as e:
        bad_request(e)
    _set_cookie(response, a.create_session(user["id"], client_ip(request), request.headers.get("user-agent", "")))
    return {"user": user}


@app.post("/api/auth/login")
def login(req: LoginReq, request: Request, response: Response):
    ip, em = client_ip(request), "email:" + req.email.strip().lower()
    if limiter.blocked(ip, em):
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
    a = Auth()
    user = a.authenticate(req.email, req.password)
    if not user:
        limiter.fail(ip, em)
        raise HTTPException(401, "Wrong email or password")
    limiter.clear(ip, em)
    _set_cookie(response, a.create_session(user["id"], ip, request.headers.get("user-agent", "")))
    return {"user": user}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    Auth().logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


class PasswordReq(BaseModel):
    current: str
    new: str = Field(max_length=200)


@app.post("/api/auth/password")
def change_password(req: PasswordReq, request: Request, response: Response):
    a, u = Auth(), request.state.user
    if not a.check_password(u["id"], req.current):
        raise HTTPException(422, "Current password is wrong")
    try:
        a.update_user(u["id"], password=req.new)   # also logs out other devices
    except ValueError as e:
        bad_request(e)
    _set_cookie(response, a.create_session(u["id"], client_ip(request), request.headers.get("user-agent", "")))
    return {"ok": True}


# ================================================================== team (admin)
class UserIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    name: str = ""
    password: str = Field(max_length=200)
    role: str = "member"


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = Field(None, max_length=200)


@app.get("/api/users")
def list_users():
    return {"users": Auth().users()}


@app.post("/api/users")
def create_user(u: UserIn):
    try:
        return Auth().create_user(u.email, u.password, u.role, u.name)
    except ValueError as e:
        bad_request(e)


@app.patch("/api/users/{uid}")
def patch_user(uid: int, u: UserPatch, request: Request):
    if uid == request.state.user["id"] and (u.active is False or (u.role and u.role != "admin")):
        raise HTTPException(422, "You can't deactivate or demote yourself")
    try:
        return Auth().update_user(uid, name=u.name, role=u.role, active=u.active, password=u.password or None)
    except KeyError:
        raise HTTPException(404, "user not found")
    except ValueError as e:
        bad_request(e)


@app.delete("/api/users/{uid}")
def delete_user(uid: int, request: Request):
    if uid == request.state.user["id"]:
        raise HTTPException(422, "You can't delete yourself")
    try:
        Auth().delete_user(uid)
    except KeyError:
        raise HTTPException(404, "user not found")
    except ValueError as e:
        bad_request(e)
    return {"ok": True}


# ================================================================== runs
@dataclass
class Job:
    id: int
    params: dict
    queries: int
    status: str = "running"           # running | done | failed
    started_at: str = field(default_factory=utcnow)
    finished_at: str | None = None
    progress: ProgressTracker = field(default_factory=ProgressTracker)
    log: list[str] = field(default_factory=list)
    found: int = 0
    hot: int = 0
    error: str | None = None

    def view(self) -> dict:
        return {"id": self.id, "params": self.params, "queries": self.queries, "status": self.status,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "stages": self.progress.tasks, "log": self.log, "found": self.found, "hot": self.hot,
                "error": self.error}


JOBS: dict[int, Job] = {}
_next_id = 1


class RunRequest(BaseModel):
    name: str = ""
    niches: list[str] = Field(min_length=1)
    countries: list[str] = Field(min_length=1)
    cities: list[str] = []
    top_cities: int = Field(3, ge=1, le=25)
    services: list[str] = []
    sources: list[str] = []
    limit: int = Field(60, ge=5, le=200)
    expand: bool = False
    serp: bool = True
    pagespeed: bool = False
    ai_pitch: bool = False
    only_new: bool = False


async def _execute(job: Job, opts: RunOptions) -> None:
    try:
        leads, _ = await run_pipeline(opts, progress=job.progress)
        hot = Catalog().rules()["tiers"]["hot"]
        job.found, job.hot = len(leads), sum(1 for l in leads if l.best_score >= hot)
        job.status = "done"
    except Exception as e:  # surface the failure in the UI instead of a silent dead task
        job.status, job.error = "failed", f"{type(e).__name__}: {e}"
        log.error("run %s failed\n%s", job.id, traceback.format_exc())
    finally:
        job.finished_at = utcnow()
        store().log_run(job.started_at, job.params, job.found, job.hot, "dashboard")


@app.post("/api/runs")
async def start_run(req: RunRequest, request: Request):
    global _next_id
    if any(j.status == "running" for j in JOBS.values()):
        raise HTTPException(409, "A campaign is already running. Wait for it to finish.")
    bad = [s for s in req.services if s not in SERVICES]
    if bad:
        raise HTTPException(422, f"Unknown or disabled services: {bad}")
    # cities may come as "City|CC" (city scoped to a country) from the dashboard
    plain = [c for c in req.cities if c.strip()]
    try:
        queries = []
        if plain:
            per_cc: dict[str, list[str]] = {}
            for c in plain:
                city, _, cc = c.partition("|")
                for code in ([normalize_country(cc)] if cc else [normalize_country(x) for x in req.countries]):
                    per_cc.setdefault(code, []).append(city.strip())
            for code, cities in per_cc.items():
                queries += build_queries(req.niches, [code], cities, req.top_cities, req.limit, req.expand)
            # countries with no city picked -> their top cities
            for cc in {normalize_country(x) for x in req.countries} - set(per_cc):
                queries += build_queries(req.niches, [cc], None, req.top_cities, req.limit, req.expand)
        else:
            queries = build_queries(req.niches, req.countries, None, req.top_cities, req.limit, req.expand)
    except ValueError as e:
        bad_request(e)
    if not queries:
        raise HTTPException(422, "Nothing to search: pick at least one niche and one location.")
    if len(queries) > 400:
        raise HTTPException(422, f"{len(queries)} searches is too many for one campaign (max 400). Narrow it down.")
    opts = RunOptions(queries=queries, services=req.services or None, sources=req.sources or None,
                      pagespeed=req.pagespeed, ai_pitch=req.ai_pitch, only_new=req.only_new, serp=req.serp)
    job = Job(id=_next_id, params=req.model_dump() | {"by": request.state.user["email"]}, queries=len(queries), log=opts.log)
    _next_id += 1
    JOBS[job.id] = job
    asyncio.create_task(_execute(job, opts))
    return job.view()


@app.get("/api/runs")
def list_runs():
    live = [j.view() for j in sorted(JOBS.values(), key=lambda j: -j.id)]
    return {"live": live, "history": store().runs(50)}


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    if run_id not in JOBS:
        raise HTTPException(404, "run not found")
    return JOBS[run_id].view()


# ================================================================== leads
def _filters(niche, country, city, service, min_score, tier, status, has_email, q):
    try:
        cc = normalize_country(country) if country else None
    except ValueError:
        cc = country.upper()
    return dict(niche=niche or None, country=cc, city=city or None, service=service or None,
                min_score=min_score, tier=tier or None, status=status or None, has_email=has_email, text=q or None)


@app.get("/api/leads")
def leads(niche: str = "", country: str = "", city: str = "", service: str = "", min_score: int = 0,
          tier: str = "", status: str = "", has_email: bool = False, q: str = "",
          limit: int = Query(50, le=1000), offset: int = 0):
    rows, total = store().search(**_filters(niche, country, city, service, min_score, tier, status, has_email, q),
                                 limit=limit, offset=offset)
    return {"total": total, "rows": rows}


@app.get("/api/leads/{key}")
def lead_detail(key: str):
    s = store()
    l = s.get(key)
    if not l:
        raise HTTPException(404, "lead not found")
    st = s.db.execute("SELECT status, notes FROM leads WHERE key=?", (key,)).fetchone()
    return l.to_dict() | {"status": st[0] or "new", "notes": st[1] or ""}


class LeadUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None


@app.patch("/api/leads/{key}")
def update_lead(key: str, upd: LeadUpdate):
    try:
        store().set_status(key, upd.status, upd.notes)
    except ValueError as e:
        bad_request(e)
    return {"ok": True}


class BulkUpdate(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=5000)
    status: str


@app.post("/api/leads/bulk-status")
def bulk_status(req: BulkUpdate):
    try:
        return {"updated": store().bulk_status(req.keys, req.status)}
    except ValueError as e:
        bad_request(e)


class SuppressReq(BaseModel):
    value: str = Field(min_length=3)


@app.post("/api/suppress")
def suppress(req: SuppressReq):
    store().suppress(req.value)
    return {"ok": True}


@app.get("/api/export")
def export(fmt: str = "xlsx", niche: str = "", country: str = "", city: str = "", service: str = "",
           min_score: int = 0, tier: str = "", status: str = "", has_email: bool = False, q: str = "",
           keys: str = ""):
    s = store()
    rows, _ = s.search(**_filters(niche, country, city, service, min_score, tier, status, has_email, q), limit=100000)
    if keys:
        wanted = set(keys.split(","))
        rows = [r for r in rows if r["key"] in wanted]
    leads = [Lead.from_dict(r) for r in rows]
    if not leads:
        raise HTTPException(404, "No leads match these filters")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    ws = Catalog().kv("workspace_name", "LeadForge")
    import re
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{ws}-{niche or 'leads'}-{country or 'all'}-{stamp}").strip("-")
    tmp = Path(tempfile.gettempdir())
    info = {"Workspace": ws, "Filters": f"niche={niche or 'any'} country={country or 'any'} "
            f"service={service or 'best'} tier={tier or 'any'} status={status or 'any'}", "Exported": utcnow()}
    if fmt == "csv":
        path = to_csv(leads, tmp / f"{name}.csv")
        return FileResponse(path, filename=path.name, media_type="text/csv", background=BackgroundTask(path.unlink, missing_ok=True))
    if fmt == "gsheet":
        try:
            return {"url": to_google_sheet(leads, None, name)}
        except Exception as e:
            raise HTTPException(400, f"Google Sheets export failed: {e}")
    path = to_excel(leads, tmp / f"{name}.xlsx", info)
    return FileResponse(path, filename=path.name, background=BackgroundTask(path.unlink, missing_ok=True),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ================================================================== catalog
class ServiceIn(BaseModel):
    id: str = ""
    label: str = Field(min_length=1, max_length=60)
    description: str = ""
    base: int = Field(15, ge=0, le=100)
    signals: dict[str, dict] = {}
    pitch_hook: str = ""
    enabled: bool = True


class NicheIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    services: list[str] = []
    synonyms: list[str] = []
    high_value: bool = False
    appointment: bool = False


class CountryIn(BaseModel):
    code: str = Field(min_length=2, max_length=2)
    name: str = Field(min_length=1, max_length=60)
    gl: str = ""
    cities: list[str] = []
    enabled: bool = True


@app.get("/api/catalog")
def catalog():
    c = Catalog()
    return {"services": c.services(), "niches": c.niches(), "countries": c.countries(),
            "signals": SIGNALS,
            "scoring": {"hot": c.kv("hot", 70), "warm": c.kv("warm", 40), "no_contact_cap": c.kv("no_contact_cap", 30)},
            "workspace_name": c.kv("workspace_name", "LeadForge")}


@app.post("/api/catalog/services")
def create_service(s: ServiceIn):
    try:
        return {"id": Catalog().save_service(s.model_dump() | {"_create": True})}
    except ValueError as e:
        bad_request(e)


@app.put("/api/catalog/services/{sid}")
def update_service(sid: str, s: ServiceIn):
    try:
        return {"id": Catalog().save_service(s.model_dump(), sid)}
    except ValueError as e:
        bad_request(e)


@app.delete("/api/catalog/services/{sid}")
def delete_service(sid: str):
    try:
        Catalog().delete_service(sid)
    except KeyError:
        raise HTTPException(404, "service not found")
    except ValueError as e:
        bad_request(e)
    return {"ok": True}


@app.post("/api/catalog/niches")
def create_niche(n: NicheIn):
    c = Catalog()
    if any(x["name"] == n.name.strip().lower() for x in c.niches()):
        raise HTTPException(409, f"Niche '{n.name}' already exists")
    try:
        return {"name": c.save_niche(n.model_dump())}
    except ValueError as e:
        bad_request(e)


@app.put("/api/catalog/niches/{name}")
def update_niche(name: str, n: NicheIn):
    try:
        return {"name": Catalog().save_niche(n.model_dump(), name)}
    except ValueError as e:
        bad_request(e)


@app.delete("/api/catalog/niches/{name}")
def delete_niche(name: str):
    Catalog().delete_niche(name)
    return {"ok": True}


@app.post("/api/catalog/countries")
def create_country(cn: CountryIn):
    c = Catalog()
    if any(x["code"] == cn.code.upper() for x in c.countries()):
        raise HTTPException(409, f"Country {cn.code.upper()} already exists")
    try:
        return {"code": c.save_country(cn.model_dump())}
    except ValueError as e:
        bad_request(e)


@app.put("/api/catalog/countries/{code}")
def update_country(code: str, cn: CountryIn):
    try:
        return {"code": Catalog().save_country(cn.model_dump(), code.upper())}
    except ValueError as e:
        bad_request(e)


@app.delete("/api/catalog/countries/{code}")
def delete_country(code: str):
    Catalog().delete_country(code.upper())
    return {"ok": True}


# ================================================================== settings
def _mask(v: str | None) -> str:
    if not v:
        return ""
    return v[:4] + "•" * 6 + v[-4:] if len(v) > 10 else "•" * len(v)


class SettingsIn(BaseModel):
    env: dict[str, str | None] = {}            # only keys the user changed
    workspace_name: str | None = None
    hot: int | None = Field(None, ge=1, le=100)
    warm: int | None = Field(None, ge=1, le=100)
    no_contact_cap: int | None = Field(None, ge=0, le=100)


@app.get("/api/settings")
def get_settings():
    import os
    c = Catalog()
    env = {k: _mask(os.getenv(k)) if k.endswith("_KEY") or k == "PROXY_URL" else (os.getenv(k) or "")
           for k in sorted(EDITABLE_ENV)}
    return {"env": env, "workspace_name": c.kv("workspace_name", "LeadForge"),
            "scoring": {"hot": c.kv("hot", 70), "warm": c.kv("warm", 40), "no_contact_cap": c.kv("no_contact_cap", 30)}}


@app.put("/api/settings")
def put_settings(s: SettingsIn):
    c = Catalog()
    hot = s.hot if s.hot is not None else c.kv("hot", 70)
    warm = s.warm if s.warm is not None else c.kv("warm", 40)
    if warm >= hot:
        raise HTTPException(422, "Warm threshold must be lower than Hot threshold")
    try:
        # ignore masked placeholders sent back unchanged
        changed = {k: v for k, v in s.env.items() if v is None or "•" not in v}
        if changed:
            update_env(changed)
    except ValueError as e:
        bad_request(e)
    if s.workspace_name is not None:
        c.set_kv("workspace_name", s.workspace_name.strip()[:40] or "LeadForge")
    for k in ("hot", "warm", "no_contact_cap"):
        v = getattr(s, k)
        if v is not None:
            c.set_kv(k, v)
    return get_settings()


# ================================================================== meta / overview
@app.get("/api/meta")
def meta():
    c = Catalog()
    return {
        "version": __version__,
        "workspace_name": c.kv("workspace_name", "LeadForge"),
        "services": [s for s in c.services() if s["enabled"]],
        "niches": c.niches(),
        "countries": [x for x in c.countries() if x["enabled"]],
        "sources": [
            {"id": "google_places", "label": "Google Maps · Places API", "ready": bool(settings.google_places_key),
             "needs": "GOOGLE_PLACES_API_KEY"},
            {"id": "serper_places", "label": "Google Maps · Serper", "ready": bool(settings.serper_key),
             "needs": "SERPER_API_KEY"},
            *[{"id": sp.name, "label": {"yellowpages_us": "Yellow Pages (US)", "yell_uk": "Yell (UK)",
                                         "yellowpages_uae": "Yellow Pages (UAE)"}.get(sp.name, sp.name),
               "ready": True, "country": sp.country,
               "note": "Often blocked by bot protection without a proxy" if sp.country != "AE" else "Small result pages"}
              for sp in SPECS],
        ],
        "features": {"serp": bool(settings.serper_key), "ai_pitch": bool(settings.anthropic_key),
                     "gsheets": bool(settings.gsheets_credentials), "proxy": bool(settings.proxy_url),
                     "places": bool(settings.google_places_key)},
        "tiers": {"hot": c.kv("hot", 70), "warm": c.kv("warm", 40)},
        "statuses": list(Store.STATUSES),
        "facets": store().facets(),
    }


@app.get("/api/overview")
def overview():
    return store().overview(list(SERVICES))


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")
