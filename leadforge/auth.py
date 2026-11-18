"""Users, password hashing and login sessions (SQLite, no extra dependencies).

- Passwords: scrypt (stdlib hashlib) with a random salt per user.
- Sessions: random 256-bit token in an HttpOnly cookie; only its SHA-256 is stored,
  so a leaked database can't be used to log in.
- Roles: "admin" (everything, incl. API keys, catalog, team) and "member"
  (campaigns, leads, exports).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .config import settings

ROLES = ("admin", "member")
MIN_PASSWORD = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE, name TEXT DEFAULT '', password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member', active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT, last_login TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
    created_at TEXT, expires_at TEXT, ip TEXT, user_agent TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
"""

_N, _R, _P = 2 ** 14, 8, 1
_DUMMY = None   # hash used to keep login timing equal for unknown emails


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b = lambda x: base64.b64encode(x).decode()
    return f"scrypt${_N}${_R}${_P}${b(salt)}${b(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_password_rules(pw: str) -> None:
    if len(pw or "") < MIN_PASSWORD:
        raise ValueError(f"Password must be at least {MIN_PASSWORD} characters")


class Auth:
    _lock = threading.Lock()

    def __init__(self):
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(settings.db_path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)

    # ---------------------------------------------------------------- users
    @staticmethod
    def _public(r: sqlite3.Row | None) -> dict | None:
        if not r:
            return None
        return {"id": r["id"], "email": r["email"], "name": r["name"] or "", "role": r["role"],
                "active": bool(r["active"]), "created_at": r["created_at"], "last_login": r["last_login"]}

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def users(self) -> list[dict]:
        return [self._public(r) for r in self.db.execute("SELECT * FROM users ORDER BY role, email")]

    def get(self, uid: int) -> dict | None:
        return self._public(self.db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())

    def create_user(self, email: str, password: str, role: str = "member", name: str = "") -> dict:
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email):
            raise ValueError("Enter a valid email address")
        if role not in ROLES:
            raise ValueError(f"Role must be one of {ROLES}")
        check_password_rules(password)
        if self.db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise ValueError(f"A user with email {email} already exists")
        cur = self.db.execute(
            "INSERT INTO users(email, name, password_hash, role, active, created_at) VALUES (?,?,?,?,1,?)",
            (email, (name or "").strip()[:80], hash_password(password), role, _now().isoformat(timespec="seconds")))
        self.db.commit()
        return self.get(cur.lastrowid)

    def _admins_left(self, excluding: int) -> int:
        return self.db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND id!=?",
                               (excluding,)).fetchone()[0]

    def update_user(self, uid: int, *, name: str | None = None, role: str | None = None,
                    active: bool | None = None, password: str | None = None) -> dict:
        u = self.get(uid)
        if not u:
            raise KeyError(uid)
        demote = (role is not None and role != "admin") or active is False
        if u["role"] == "admin" and demote and not self._admins_left(uid):
            raise ValueError("You can't remove the last active admin")
        if role is not None:
            if role not in ROLES:
                raise ValueError(f"Role must be one of {ROLES}")
            self.db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
        if name is not None:
            self.db.execute("UPDATE users SET name=? WHERE id=?", (name.strip()[:80], uid))
        if active is not None:
            self.db.execute("UPDATE users SET active=? WHERE id=?", (int(active), uid))
            if not active:
                self.logout_all(uid)
        if password is not None:
            check_password_rules(password)
            self.db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), uid))
            self.logout_all(uid)
        self.db.commit()
        return self.get(uid)

    def delete_user(self, uid: int) -> None:
        u = self.get(uid)
        if not u:
            raise KeyError(uid)
        if u["role"] == "admin" and not self._admins_left(uid):
            raise ValueError("You can't delete the last active admin")
        self.logout_all(uid)
        self.db.execute("DELETE FROM users WHERE id=?", (uid,))
        self.db.commit()

    def authenticate(self, email: str, password: str) -> dict | None:
        global _DUMMY
        r = self.db.execute("SELECT * FROM users WHERE email=?", ((email or "").strip().lower(),)).fetchone()
        if not r:
            _DUMMY = _DUMMY or hash_password(secrets.token_hex(8))
            verify_password(password or "", _DUMMY)     # same work as a real check
            return None
        if not verify_password(password or "", r["password_hash"]) or not r["active"]:
            return None
        self.db.execute("UPDATE users SET last_login=? WHERE id=?", (_now().isoformat(timespec="seconds"), r["id"]))
        self.db.commit()
        return self._public(r)

    def check_password(self, uid: int, password: str) -> bool:
        r = self.db.execute("SELECT password_hash FROM users WHERE id=?", (uid,)).fetchone()
        return bool(r) and verify_password(password or "", r[0])

    # ---------------------------------------------------------------- sessions
    def create_session(self, uid: int, ip: str = "", user_agent: str = "") -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)",
                        (_token_hash(token), uid, now.isoformat(), (now + timedelta(days=settings.session_days)).isoformat(),
                         ip[:64], (user_agent or "")[:200]))
        # housekeeping: drop expired sessions
        self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
        self.db.commit()
        return token

    def session_user(self, token: str | None) -> dict | None:
        if not token or len(token) > 100:
            return None
        r = self.db.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash=? AND s.expires_at > ? AND u.active=1",
            (_token_hash(token), _now().isoformat())).fetchone()
        return self._public(r)

    def logout(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
            self.db.commit()

    def logout_all(self, uid: int) -> None:
        self.db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        self.db.commit()


class LoginLimiter:
    """In-memory brute-force guard: N failed logins per IP / per email in a window."""

    def __init__(self, max_fails: int = 8, window_min: int = 15):
        self.max, self.window = max_fails, timedelta(minutes=window_min)
        self.fails: dict[str, list[datetime]] = {}
        self.lock = threading.Lock()

    def _recent(self, key: str) -> list[datetime]:
        cutoff = _now() - self.window
        lst = [t for t in self.fails.get(key, []) if t > cutoff]
        self.fails[key] = lst
        return lst

    def blocked(self, *keys: str) -> bool:
        with self.lock:
            return any(len(self._recent(k)) >= self.max for k in keys)

    def fail(self, *keys: str) -> None:
        with self.lock:
            for k in keys:
                self._recent(k).append(_now())

    def clear(self, *keys: str) -> None:
        with self.lock:
            for k in keys:
                self.fails.pop(k, None)
