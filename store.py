"""Cabinet's own record of what it set up, in one small SQLite file in Cabinet's data folder.

Tables:

- ``plans``: a review screen's plan, kept until it is done or goes stale, so a profile that was created
  but never recorded (the app closed in between) can be recognised and recorded later.
- ``installs``: every Hermes profile Cabinet created, with a fingerprint of what it looked like the moment
  it was made. Removing is only offered while the profile still matches that fingerprint.
- ``crews``: every crew Cabinet set up: which bots, in what order, which profiles are Cabinet's and
  which were already the owner's, and any Kanban cards it made.
- ``kits``: Bot Lab plans saved to reuse. A kit is a saved plan, not a profile.
- ``events``: a plain history of what Cabinet did, newest last.

Only Cabinet's own rows live here. Nothing in this file is a secret.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS installs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, profile TEXT NOT NULL, kind TEXT NOT NULL, source_slug TEXT,
  source_name TEXT, created_at REAL NOT NULL, path TEXT NOT NULL, fingerprint TEXT NOT NULL,
  display_name TEXT, description TEXT, crew_id INTEGER, catalog_commit TEXT,
  status TEXT NOT NULL DEFAULT 'active', removed_at REAL);
CREATE INDEX IF NOT EXISTS installs_profile ON installs(profile, status);
CREATE TABLE IF NOT EXISTS crews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL, name TEXT NOT NULL, created_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', catalog_commit TEXT, members TEXT NOT NULL, handoff TEXT NOT NULL,
  cards TEXT, removed_at REAL);
CREATE TABLE IF NOT EXISTS kits (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at REAL NOT NULL, fields TEXT NOT NULL,
  job TEXT, status TEXT NOT NULL DEFAULT 'active', removed_at REAL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, action TEXT NOT NULL, subject TEXT, detail TEXT);
"""

PLAN_LIFETIME = 24 * 3600


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as db:
            db.executescript(SCHEMA)
            db.commit()

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        return db

    # -- plans --
    def add_plan(self, kind: str, payload: dict[str, Any]) -> str:
        pid = uuid.uuid4().hex
        with closing(self._conn()) as db:
            db.execute("INSERT INTO plans(id, kind, created_at, payload) VALUES (?,?,?,?)",
                       (pid, kind, time.time(), json.dumps(payload, ensure_ascii=False)))
            db.commit()
        return pid

    def get_plan(self, pid: str) -> dict[str, Any] | None:
        with closing(self._conn()) as db:
            row = db.execute("SELECT * FROM plans WHERE id=?", (str(pid),)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "kind": row["kind"], "createdAt": row["created_at"], "status": row["status"],
                "payload": json.loads(row["payload"])}

    def pending_plans(self) -> list[dict[str, Any]]:
        cutoff = time.time() - PLAN_LIFETIME
        with closing(self._conn()) as db:
            rows = db.execute("SELECT id FROM plans WHERE status='pending' AND created_at>=? ORDER BY created_at", (cutoff,)).fetchall()
        return [p for p in (self.get_plan(r["id"]) for r in rows) if p]

    def set_plan_status(self, pid: str, status: str) -> None:
        with closing(self._conn()) as db:
            db.execute("UPDATE plans SET status=? WHERE id=?", (status, pid))
            db.commit()

    def expire_plans(self) -> None:
        with closing(self._conn()) as db:
            db.execute("UPDATE plans SET status='expired' WHERE status='pending' AND created_at<?", (time.time() - PLAN_LIFETIME,))
            db.commit()

    # -- installs --
    def add_install(self, *, profile: str, kind: str, source_slug: str, source_name: str, path: str, fingerprint: dict[str, Any],
                    display_name: str, description: str, crew_id: int | None, catalog_commit: str) -> int:
        with closing(self._conn()) as db:
            cur = db.execute(
                "INSERT INTO installs(profile, kind, source_slug, source_name, created_at, path, fingerprint, display_name, "
                "description, crew_id, catalog_commit) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (profile, kind, source_slug, source_name, time.time(), path, json.dumps(fingerprint), display_name, description,
                 crew_id, catalog_commit))
            db.commit()
            return int(cur.lastrowid)

    def _install(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["fingerprint"] = json.loads(d["fingerprint"])
        return d

    def get_install(self, install_id: int) -> dict[str, Any] | None:
        with closing(self._conn()) as db:
            row = db.execute("SELECT * FROM installs WHERE id=?", (int(install_id),)).fetchone()
        return self._install(row) if row else None

    def active_install_for(self, profile: str) -> dict[str, Any] | None:
        with closing(self._conn()) as db:
            row = db.execute("SELECT * FROM installs WHERE profile=? AND status='active' ORDER BY id DESC", (profile,)).fetchone()
        return self._install(row) if row else None

    def installs(self, status: str | None = None) -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            rows = db.execute("SELECT * FROM installs" + (" WHERE status=?" if status else "") + " ORDER BY id",
                              (status,) if status else ()).fetchall()
        return [self._install(r) for r in rows]

    def installs_for_crew(self, crew_id: int) -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            rows = db.execute("SELECT * FROM installs WHERE crew_id=? ORDER BY id", (int(crew_id),)).fetchall()
        return [self._install(r) for r in rows]

    def set_install_crew(self, install_id: int, crew_id: int | None) -> None:
        with closing(self._conn()) as db:
            db.execute("UPDATE installs SET crew_id=? WHERE id=?", (crew_id, int(install_id)))
            db.commit()

    def release_install(self, install_id: int) -> None:
        """Stop tracking a profile without touching it (Cabinet keeps its hands off anything the owner has changed)."""
        with closing(self._conn()) as db:
            db.execute("UPDATE installs SET status='released', removed_at=? WHERE id=?", (time.time(), int(install_id)))
            db.commit()

    def mark_install_removed(self, install_id: int) -> None:
        with closing(self._conn()) as db:
            db.execute("UPDATE installs SET status='removed', removed_at=? WHERE id=?", (time.time(), int(install_id)))
            db.commit()

    # -- crews --
    def add_crew(self, *, slug: str, name: str, members: list[dict[str, Any]], handoff: list[dict[str, Any]],
                 catalog_commit: str, status: str = "active") -> int:
        with closing(self._conn()) as db:
            cur = db.execute("INSERT INTO crews(slug, name, created_at, status, catalog_commit, members, handoff) VALUES (?,?,?,?,?,?,?)",
                             (slug, name, time.time(), status, catalog_commit, json.dumps(members), json.dumps(handoff)))
            db.commit()
            return int(cur.lastrowid)

    def _crew(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["members"] = json.loads(d["members"])
        d["handoff"] = json.loads(d["handoff"])
        d["cards"] = json.loads(d["cards"]) if d["cards"] else []
        return d

    def get_crew(self, crew_id: int) -> dict[str, Any] | None:
        with closing(self._conn()) as db:
            row = db.execute("SELECT * FROM crews WHERE id=?", (int(crew_id),)).fetchone()
        return self._crew(row) if row else None

    def crews(self, status: str | None = None) -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            rows = db.execute("SELECT * FROM crews" + (" WHERE status=?" if status else "") + " ORDER BY id",
                              (status,) if status else ()).fetchall()
        return [self._crew(r) for r in rows]

    def update_crew(self, crew_id: int, **fields: Any) -> None:
        allowed = {"status": None, "members": json.dumps, "cards": json.dumps, "removed_at": None}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise KeyError(k)
            sets.append(f"{k}=?")
            vals.append(allowed[k](v) if allowed[k] else v)
        with closing(self._conn()) as db:
            db.execute(f"UPDATE crews SET {', '.join(sets)} WHERE id=?", (*vals, int(crew_id)))
            db.commit()

    # -- kits --
    def add_kit(self, name: str, fields: dict[str, str], job: str) -> int:
        with closing(self._conn()) as db:
            cur = db.execute("INSERT INTO kits(name, created_at, fields, job) VALUES (?,?,?,?)",
                             (name, time.time(), json.dumps(fields, ensure_ascii=False), job))
            db.commit()
            return int(cur.lastrowid)

    def _kit(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["fields"] = json.loads(d["fields"])
        return d

    def get_kit(self, kit_id: int) -> dict[str, Any] | None:
        with closing(self._conn()) as db:
            row = db.execute("SELECT * FROM kits WHERE id=?", (int(kit_id),)).fetchone()
        return self._kit(row) if row else None

    def kits(self, status: str | None = "active") -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            rows = db.execute("SELECT * FROM kits" + (" WHERE status=?" if status else "") + " ORDER BY id",
                              (status,) if status else ()).fetchall()
        return [self._kit(r) for r in rows]

    def remove_kit(self, kit_id: int) -> None:
        with closing(self._conn()) as db:
            db.execute("UPDATE kits SET status='removed', removed_at=? WHERE id=?", (time.time(), int(kit_id)))
            db.commit()

    # -- events --
    def log(self, action: str, subject: str = "", detail: str = "") -> None:
        with closing(self._conn()) as db:
            db.execute("INSERT INTO events(at, action, subject, detail) VALUES (?,?,?,?)", (time.time(), action, subject, detail[:500]))
            db.commit()

    def events(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows][::-1]
