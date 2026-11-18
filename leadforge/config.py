"""Settings loaded from environment / .env. Every API key is optional:
sources and features whose key is missing are skipped automatically.

`settings` is a single shared object; `update_env()` writes .env and reloads it
in place, so every module sees new keys without a restart (dashboard Settings page)."""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = Path(os.getenv("LF_ENV_FILE") or ROOT / ".env")   # Docker: keep it on the data volume
load_dotenv(ENV_PATH)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.load()

    def load(self) -> None:
        g = lambda k: os.getenv(k) or None
        # --- Discovery APIs ---
        self.google_places_key = g("GOOGLE_PLACES_API_KEY")
        self.serper_key = g("SERPER_API_KEY")
        # --- Enrichment ---
        self.pagespeed_key = g("PAGESPEED_API_KEY")
        # --- AI pitch (optional) ---
        self.anthropic_key = g("ANTHROPIC_API_KEY")
        self.pitch_model = os.getenv("PITCH_MODEL") or "claude-opus-5-5"
        # --- Google Sheets (optional): service-account JSON path ---
        self.gsheets_credentials = g("GOOGLE_SHEETS_CREDENTIALS")
        self.gsheet_id = g("GOOGLE_SHEET_ID")   # sheet shared with the service-account email
        # --- Scraping behaviour ---
        self.proxy_url = g("PROXY_URL")
        self.concurrency = _int("CONCURRENCY", 10)
        self.http_timeout = _int("HTTP_TIMEOUT", 15)
        self.directory_delay_ms = _int("DIRECTORY_DELAY_MS", 2500)
        self.cache_days = _int("CACHE_DAYS", 14)
        # --- Safety caps (protect your Google bill) ---
        self.max_places_calls_per_run = _int("MAX_PLACES_CALLS_PER_RUN", 50)
        self.max_serper_calls_per_run = _int("MAX_SERPER_CALLS_PER_RUN", 50)
        # --- Server / security ---
        self.production = (os.getenv("LF_ENV") or "development").lower() == "production"
        self.cookie_secure = self.production or os.getenv("COOKIE_SECURE", "").lower() in ("1", "true", "yes")
        self.session_days = _int("SESSION_DAYS", 14)
        # --- Paths ---
        self.db_path = Path(os.getenv("DB_PATH") or ROOT / "data" / "leads.db")
        self.output_dir = Path(os.getenv("OUTPUT_DIR") or ROOT / "output")
        self.user_agent = os.getenv("USER_AGENT") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


settings = Settings()

# Keys the dashboard may edit (everything else in .env is left untouched)
EDITABLE_ENV = {
    "GOOGLE_PLACES_API_KEY", "SERPER_API_KEY", "PAGESPEED_API_KEY", "ANTHROPIC_API_KEY", "PITCH_MODEL",
    "GOOGLE_SHEETS_CREDENTIALS", "GOOGLE_SHEET_ID", "PROXY_URL", "MAX_PLACES_CALLS_PER_RUN", "MAX_SERPER_CALLS_PER_RUN",
    "CONCURRENCY", "CACHE_DAYS",
}


def update_env(values: dict[str, str | None]) -> None:
    """Upsert keys in .env (preserving comments/other lines) and reload settings in place."""
    values = {k: ("" if v is None else str(v).strip()) for k, v in values.items() if k in EDITABLE_ENV}
    for k, v in values.items():
        if "\n" in v or "\r" in v:
            raise ValueError(f"{k}: value must be a single line")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in values:
            lines[i] = f"{m.group(1)}={values[m.group(1)]}"
            seen.add(m.group(1))
    lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in values.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    settings.load()
