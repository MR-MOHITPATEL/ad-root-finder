"""Human review decisions on competitor ads — the keep/maybe/skip layer.

Backed by SQLite (data/rf/rf.db) so many reviewers can decide at the same time
without losing each other's writes. One row per ad_id; a decision is one atomic
upsert. Legacy data/rf/decisions.json is imported once on first use.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .paths import DATA, DECISIONS

DB = DATA / "rf.db"
STATUSES = ("works", "maybe", "no")


@contextmanager
def _conn():
    c = sqlite3.connect(DB, timeout=15, isolation_level=None)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=15000")
        _ensure_schema(c)
        yield c
    finally:
        c.close()


def _ensure_schema(c: sqlite3.Connection) -> None:
    c.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            ad_id      TEXT PRIMARY KEY,
            status     TEXT NOT NULL,
            root_id    TEXT,
            note       TEXT DEFAULT '',
            phash      TEXT,
            decided_by TEXT DEFAULT '',
            decided_at TEXT NOT NULL
        )
    """)
    _migrate_json(c)


def _migrate_json(c: sqlite3.Connection) -> None:
    if not DECISIONS.exists():
        return
    if c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]:
        return
    try:
        old = json.loads(DECISIONS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    rows = [
        (aid, v.get("status", "maybe"), v.get("root_id"), v.get("note", ""),
         v.get("phash"), v.get("decided_by", ""),
         v.get("decided_at") or datetime.now(timezone.utc).isoformat())
        for aid, v in old.items()
    ]
    c.executemany(
        "INSERT OR IGNORE INTO decisions VALUES (?,?,?,?,?,?,?)", rows)
    DECISIONS.rename(DECISIONS.with_suffix(".json.migrated"))


# ── API (dict-compatible with the old JSON store) ───────────────────────────

def load() -> dict:
    with _conn() as c:
        return {
            r[0]: {"status": r[1], "root_id": r[2], "note": r[3],
                   "phash": r[4], "decided_by": r[5], "decided_at": r[6]}
            for r in c.execute("SELECT ad_id,status,root_id,note,phash,decided_by,decided_at "
                               "FROM decisions")
        }


def set_decision(ad_id: str, status: str, *, root_id: str | None = None,
                 note: str = "", phash: str | None = None, by: str = "") -> None:
    if status not in STATUSES:
        raise ValueError(status)
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute("""
            INSERT INTO decisions (ad_id,status,root_id,note,phash,decided_by,decided_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(ad_id) DO UPDATE SET
                status=excluded.status,
                root_id=COALESCE(excluded.root_id, decisions.root_id),
                note=CASE WHEN excluded.note != '' THEN excluded.note ELSE decisions.note END,
                phash=COALESCE(excluded.phash, decisions.phash),
                decided_by=CASE WHEN excluded.decided_by != '' THEN excluded.decided_by ELSE decisions.decided_by END,
                decided_at=excluded.decided_at
        """, (ad_id, status, root_id, note, phash, by, now))


def set_many(ad_ids: list[str], status: str, *, root_id: str | None = None,
             note: str = "", phash: str | None = None, by: str = "") -> None:
    for aid in ad_ids:
        set_decision(aid, status, root_id=root_id, note=note, phash=phash, by=by)


def clear(ad_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM decisions WHERE ad_id = ?", (ad_id,))


def decided_phashes() -> dict[str, dict]:
    with _conn() as c:
        out: dict[str, dict] = {}
        for status, root_id, phash in c.execute(
                "SELECT status,root_id,phash FROM decisions WHERE phash IS NOT NULL AND phash != ''"):
            out[phash] = {"status": status, "root_id": root_id}
        return out
