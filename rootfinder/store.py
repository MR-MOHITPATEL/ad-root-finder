"""
Single data layer. Two backends, chosen at runtime:

  * Supabase   — when SUPABASE_URL + SUPABASE_KEY are set (production)
  * local files — otherwise (dev / offline)

Consumers (decisions.py, analyze.py, ledger.py, web/data.py) call these
functions and never touch storage directly.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from .paths import ADS_DIR, DATA, DECISIONS, IMAGES_DIR, MATCHES_DIR, ROOT, ROOTS_CATALOGUE, SEEN, slug

load_dotenv(ROOT / ".env")

BUCKET = "ad-images"
_DB = DATA / "rf.db"  # local decisions store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@lru_cache(maxsize=1)
def _supabase():
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    if not (url and key):
        return None
    from supabase import create_client
    return create_client(url, key)


def mode() -> str:
    return "supabase" if _supabase() else "local"


# ── SEED (first run) ────────────────────────────────────────────────────────

SEED_CATALOGUE = ROOT / "config" / "catalogue.seed.json"


def _seed_roots() -> list[dict]:
    for p in (SEED_CATALOGUE, ROOTS_CATALOGUE):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")).get("roots", [])
    return []


# ── DECISIONS ──────────────────────────────────────────────────────────────

@contextmanager
def _sqlite():
    c = sqlite3.connect(_DB, timeout=15, isolation_level=None)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=15000")
        c.execute("""CREATE TABLE IF NOT EXISTS decisions(
            ad_id TEXT PRIMARY KEY, status TEXT NOT NULL, root_id TEXT,
            note TEXT DEFAULT '', phash TEXT, decided_by TEXT DEFAULT '',
            decided_at TEXT NOT NULL)""")
        _migrate_decisions_json(c)
        yield c
    finally:
        c.close()


def _migrate_decisions_json(c) -> None:
    if not DECISIONS.exists() or c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]:
        return
    try:
        old = json.loads(DECISIONS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    c.executemany("INSERT OR IGNORE INTO decisions VALUES (?,?,?,?,?,?,?)", [
        (a, v.get("status", "maybe"), v.get("root_id"), v.get("note", ""),
         v.get("phash"), v.get("decided_by", ""), v.get("decided_at") or _now())
        for a, v in old.items()])
    DECISIONS.rename(DECISIONS.with_suffix(".json.migrated"))


def decisions_load() -> dict:
    sb = _supabase()
    if sb:
        rows = sb.table("decisions").select("*").execute().data
        return {r["ad_id"]: r for r in rows}
    with _sqlite() as c:
        return {r[0]: {"status": r[1], "root_id": r[2], "note": r[3], "phash": r[4],
                       "decided_by": r[5], "decided_at": r[6]}
                for r in c.execute("SELECT ad_id,status,root_id,note,phash,decided_by,decided_at "
                                   "FROM decisions")}


def decision_set(ad_id: str, status: str, *, root_id=None, note="", phash=None, by="") -> None:
    sb = _supabase()
    if sb:
        existing = sb.table("decisions").select("*").eq("ad_id", ad_id).execute().data
        prev = existing[0] if existing else {}
        sb.table("decisions").upsert({
            "ad_id": ad_id, "status": status,
            "root_id": root_id or prev.get("root_id"),
            "note": note or prev.get("note", ""),
            "phash": phash or prev.get("phash"),
            "decided_by": by or prev.get("decided_by", ""),
            "decided_at": _now(),
        }).execute()
        return
    with _sqlite() as c:
        c.execute("""INSERT INTO decisions(ad_id,status,root_id,note,phash,decided_by,decided_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(ad_id) DO UPDATE SET status=excluded.status,
              root_id=COALESCE(excluded.root_id, decisions.root_id),
              note=CASE WHEN excluded.note!='' THEN excluded.note ELSE decisions.note END,
              phash=COALESCE(excluded.phash, decisions.phash),
              decided_by=CASE WHEN excluded.decided_by!='' THEN excluded.decided_by ELSE decisions.decided_by END,
              decided_at=excluded.decided_at""",
                  (ad_id, status, root_id, note, phash, by, _now()))


def decision_clear(ad_id: str) -> None:
    sb = _supabase()
    if sb:
        sb.table("decisions").delete().eq("ad_id", ad_id).execute()
        return
    with _sqlite() as c:
        c.execute("DELETE FROM decisions WHERE ad_id=?", (ad_id,))


def decided_phashes() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for v in decisions_load().values():
        if v.get("phash"):
            out[v["phash"]] = {"status": v["status"], "root_id": v.get("root_id")}
    return out


# ── CATALOGUE (roots + candidates) ─────────────────────────────────────────

def catalogue_load() -> dict:
    sb = _supabase()
    if sb:
        roots = sb.table("roots").select("*").execute().data
        cands = sb.table("candidates").select("*").execute().data
        if not roots:  # first run — seed
            for r in _seed_roots():
                sb.table("roots").upsert(_root_row(r)).execute()
            roots = sb.table("roots").select("*").execute().data
        return {
            "roots": [_root_from_row(r) for r in roots],
            "candidates": [{"candidate_name": c["name"], **{k: c[k] for k in
                            ("mechanism", "visual_motif", "fits_our_brand")},
                            "examples": c.get("examples") or []} for c in cands],
        }
    if ROOTS_CATALOGUE.exists():
        return json.loads(ROOTS_CATALOGUE.read_text(encoding="utf-8"))
    return {"roots": _seed_roots(), "candidates": []}


def _root_row(r: dict) -> dict:
    return {k: r.get(k) for k in ("root_id", "name", "status", "mechanism", "why_it_works",
            "visual_motif", "fits_our_brand", "compliance_notes")} | {
        "competitor_examples": r.get("competitor_examples") or [],
        "our_executions": r.get("our_executions") or [],
        "updated_at": _now()}


def _root_from_row(r: dict) -> dict:
    r = dict(r)
    r.setdefault("competitor_examples", r.get("competitor_examples") or [])
    r.setdefault("our_executions", r.get("our_executions") or [])
    return r


def catalogue_save(cat: dict) -> None:
    sb = _supabase()
    if sb:
        for r in cat.get("roots", []):
            sb.table("roots").upsert(_root_row(r)).execute()
        have = {c["name"] for c in sb.table("candidates").select("name").execute().data}
        want = {c["candidate_name"] for c in cat.get("candidates", [])}
        for c in cat.get("candidates", []):
            sb.table("candidates").upsert({
                "name": c["candidate_name"], "mechanism": c.get("mechanism"),
                "visual_motif": c.get("visual_motif"), "fits_our_brand": c.get("fits_our_brand"),
                "examples": c.get("examples") or []}).execute()
        for gone in have - want:
            sb.table("candidates").delete().eq("name", gone).execute()
        return
    cat = dict(cat)
    cat["updated_at"] = _now()
    ROOTS_CATALOGUE.write_text(json.dumps(cat, indent=2, ensure_ascii=False), encoding="utf-8")


# ── ROOT IMAGES (manual add / remove, with dedup) ──────────────────────────

def _root_images(root: dict) -> list[dict]:
    return (root.get("competitor_examples") or []) + (root.get("our_executions") or [])


def all_root_image_phashes() -> dict[str, str]:
    """{phash: root name} across every image on every named root — for dedup."""
    out: dict[str, str] = {}
    for r in catalogue_load().get("roots", []):
        for e in _root_images(r):
            if e.get("phash"):
                out[e["phash"]] = r.get("name") or r.get("root_id")
    return out


def root_add_image(root_id: str, data: bytes, *, filename: str, kind: str,
                   label: str = "", phash: str | None = None) -> dict:
    """kind: 'ours' | 'competitor'. Returns {ok, url|reason, dupe_root?}."""
    from .imagehash import hamming

    if phash:
        for ph, rname in all_root_image_phashes().items():
            if hamming(phash, ph) <= 6:
                return {"ok": False, "reason": "duplicate", "dupe_root": rname}

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".jpg"
    key = f"manual/{root_id}/{_now().replace(':', '').replace('.', '')}{ext}"
    sb = _supabase()
    url = None
    if sb:
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp"}.get(ext, "image/jpeg")
        try:
            sb.storage.from_(BUCKET).upload(key, data, {"upsert": "true", "content-type": mime})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"upload failed: {e}"}
        url = f"{os.getenv('SUPABASE_URL', '').rstrip('/')}/storage/v1/object/public/{BUCKET}/{key}"
    else:
        p = IMAGES_DIR / "manual" / root_id
        p.mkdir(parents=True, exist_ok=True)
        fp = p / (filename or "img" + ext)
        fp.write_bytes(data)
        url = str(fp)

    entry = {"image_url": url, "phash": phash, "manual": True, "added_at": _now(),
             "note": label, "brand": label or ("Our execution" if kind == "ours" else "Competitor")}
    cat = catalogue_load()
    root = next((r for r in cat["roots"] if r["root_id"] == root_id), None)
    if not root:
        return {"ok": False, "reason": "root not found"}
    root.setdefault("our_executions" if kind == "ours" else "competitor_examples", []).append(entry)
    catalogue_save(cat)
    return {"ok": True, "url": url}


def root_remove_image(root_id: str, image_url: str) -> bool:
    cat = catalogue_load()
    root = next((r for r in cat["roots"] if r["root_id"] == root_id), None)
    if not root:
        return False
    for key in ("competitor_examples", "our_executions"):
        root[key] = [e for e in (root.get(key) or []) if e.get("image_url") != image_url]
    catalogue_save(cat)
    return True


# ── LEDGER ─────────────────────────────────────────────────────────────────

def ledger_load() -> dict:
    sb = _supabase()
    if sb:
        return {r["ad_id"]: r for r in sb.table("ledger").select("*").execute().data}
    if SEEN.exists():
        try:
            return json.loads(SEEN.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def ledger_save(d: dict) -> None:
    sb = _supabase()
    if sb:
        rows = [{"ad_id": k, **{f: v.get(f) for f in ("competitor", "page_name", "first_seen",
                "last_seen", "times_seen", "was_active", "went_inactive_at")}}
                for k, v in d.items()]
        for i in range(0, len(rows), 500):
            sb.table("ledger").upsert(rows[i:i + 500]).execute()
        return
    SEEN.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


# ── MATCHES ────────────────────────────────────────────────────────────────

def matches_all() -> list[dict]:
    sb = _supabase()
    if sb:
        rows, page = [], 0
        while True:
            chunk = sb.table("matches").select("*").range(page * 1000, page * 1000 + 999).execute().data
            rows += chunk
            if len(chunk) < 1000:
                break
            page += 1
        return [_match_from_row(r) for r in rows]
    out = []
    for f in sorted(MATCHES_DIR.glob("*.json")):
        for m in json.loads(f.read_text(encoding="utf-8")).get("matches", []):
            m["_competitor_file"] = f.stem
            out.append(m)
    return out


def _match_from_row(r: dict) -> dict:
    return {
        "ad_id": r["ad_id"], "page_name": r.get("page_name"), "headline": r.get("headline"),
        "snapshot_url": r.get("snapshot_url"), "image": r.get("image_url"),
        "image_phash": r.get("image_phash"), "start_time": r.get("start_time"),
        "is_active": r.get("is_active"), "is_noise": r.get("is_noise"),
        "root": r.get("root") or {}, "_competitor_file": r.get("competitor"),
    }


def matches_existing_ids(competitor: str) -> set[str]:
    sb = _supabase()
    if sb:
        return {r["ad_id"] for r in
                sb.table("matches").select("ad_id").eq("competitor", competitor).execute().data}
    f = MATCHES_DIR / f"{slug(competitor)}.json"
    if f.exists():
        return {m["ad_id"] for m in json.loads(f.read_text(encoding="utf-8")).get("matches", [])}
    return set()


def matches_save(competitor: str, records: list[dict]) -> None:
    sb = _supabase()
    if sb:
        rows = [{
            "ad_id": m["ad_id"], "competitor": competitor, "page_name": m.get("page_name"),
            "headline": m.get("headline"), "snapshot_url": m.get("snapshot_url"),
            "image_url": m.get("image"), "image_phash": m.get("image_phash"),
            "start_time": m.get("start_time"), "is_active": m.get("is_active"),
            "is_noise": bool(m.get("is_noise")), "root": m.get("root") or {},
            "analyzed_at": _now(),
        } for m in records]
        for i in range(0, len(rows), 200):
            sb.table("matches").upsert(rows[i:i + 200]).execute()
        return
    f = MATCHES_DIR / f"{slug(competitor)}.json"
    f.write_text(json.dumps({"term": competitor, "analyzed_at": _now(),
                             "count": len(records), "matches": records},
                            indent=2, ensure_ascii=False), encoding="utf-8")


# ── IMAGES ─────────────────────────────────────────────────────────────────

def put_image(local_path: str | Path, key: str) -> str | None:
    """Upload to Supabase Storage; return public URL. Local mode: return the path."""
    local_path = Path(local_path)
    if not local_path.exists():
        return None
    sb = _supabase()
    if not sb:
        return str(local_path)
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp"}.get(local_path.suffix.lower(), "image/jpeg")
    try:
        sb.storage.from_(BUCKET).upload(
            key, local_path.read_bytes(),
            {"upsert": "true", "content-type": mime})
    except Exception:  # noqa: BLE001 — already exists is fine
        pass
    base = os.getenv("SUPABASE_URL", "").rstrip("/")
    return f"{base}/storage/v1/object/public/{BUCKET}/{key}"
