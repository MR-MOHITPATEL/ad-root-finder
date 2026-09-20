"""
Single data layer. Three backends, chosen at runtime (checked in this order):

  * Firestore + Cloudflare R2 — when GCP_PROJECT_ID + GCP_SERVICE_ACCOUNT_JSON
    are set (production, post-Supabase-migration). Structured data (matches,
    roots, candidates, ledger, decisions) lives in Firestore; images live in
    R2 (S3-compatible, zero egress fees) when R2_* env vars are also set,
    else fall back to local files for images.
  * Supabase   — when SUPABASE_URL + SUPABASE_KEY are set (legacy prod)
  * local files — otherwise (dev / offline)

Consumers (decisions.py, analyze.py, ledger.py, web/data.py) call these
functions and never touch storage directly.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from copy import deepcopy
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


# ── in-process read cache ──────────────────────────────────────────────────
# Every page nav used to re-fetch matches/decisions/catalogue/ledger from
# the backend from scratch (4+ network round trips per click). These are read
# far more often than they change, so cache each whole-table read briefly
# and drop the cache the moment something writes to that table.
_CACHE_TTL = 20  # seconds
# Firestore bills per document read and a full matches/candidates/ledger load is
# thousands of reads, so hold reads much longer there. Our own writes still
# invalidate immediately (_cache_clear); only data written by another process
# (the local scan) shows up after the TTL.
_CACHE_TTL_FIRESTORE = 300
_cache: dict[str, tuple[float, object]] = {}


def _ttl() -> int:
    return _CACHE_TTL_FIRESTORE if os.getenv("GCP_SERVICE_ACCOUNT_JSON", "").strip() else _CACHE_TTL


def _cache_get(key: str):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _ttl():
        return deepcopy(hit[1])
    return None


def _cache_put(key: str, value):
    _cache[key] = (time.monotonic(), value)
    return deepcopy(value)


def _cache_clear(*keys: str) -> None:
    for k in keys:
        _cache.pop(k, None)


# ── Firestore client ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _firestore():
    project = os.getenv("GCP_PROJECT_ID", "").strip()
    creds_json = os.getenv("GCP_SERVICE_ACCOUNT_JSON", "").strip()
    if not (project and creds_json):
        return None
    from google.cloud import firestore
    from google.oauth2 import service_account
    info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(info)
    # The console lets you name the first database anything; ours is literally
    # "default" (no parentheses), which isn't the SDK's implicit "(default)".
    database = os.getenv("GCP_FIRESTORE_DATABASE", "(default)").strip() or "(default)"
    return firestore.Client(project=project, credentials=creds, database=database)


# ── Cloudflare R2 client (S3-compatible) ────────────────────────────────────

R2_BUCKET = os.getenv("R2_BUCKET", "ad-images")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "").rstrip("/")


@lru_cache(maxsize=1)
def _r2():
    account_id = os.getenv("R2_ACCOUNT_ID", "").strip()
    key_id = os.getenv("R2_ACCESS_KEY_ID", "").strip()
    secret = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
    if not (account_id and key_id and secret and R2_PUBLIC_URL):
        return None
    import boto3
    return boto3.client(
        "s3", endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=key_id, aws_secret_access_key=secret, region_name="auto")


@lru_cache(maxsize=1)
def _supabase():
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    if not (url and key):
        return None
    import httpx
    from supabase import ClientOptions, create_client
    # Railway's egress network silently kills long-lived HTTP/2 connections
    # to Supabase without a clean GOAWAY -- httpx then raises
    # RemoteProtocolError / ConnectionTerminated, and (confirmed in prod logs)
    # it recurs on the very next fresh connection too, since it's the same
    # network path being hit again, not just one stale pooled socket. HTTP/1.1
    # opens one connection per request instead of multiplexing everything
    # onto one stream, so there's nothing long-lived for that path to kill.
    httpx_client = httpx.Client(http2=False, timeout=30)
    return create_client(url, key, options=ClientOptions(httpx_client=httpx_client))


def mode() -> str:
    if _firestore():
        return "firestore"
    return "supabase" if _supabase() else "local"


# The app process stays up for days on Railway. Supabase's edge silently
# drops a pooled HTTP/2 connection that's been idle a while; the cached
# client above then keeps reusing that dead socket and every call fails
# identically ("RemoteProtocolError: ConnectionTerminated") until the
# process restarts. Every Supabase call goes through this wrapper so a
# dead connection is detected and replaced automatically instead of
# taking the whole site down. Also covers a several-second local DNS/Wi-Fi
# blip during a long unattended scrape (retries with backoff, not just once).
_TRANSIENT_ERRORS = (
    "RemoteProtocolError", "ConnectionTerminated", "ConnectError",
    "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
)
_RETRY_DELAYS = (2, 5, 10)  # seconds


def _sb_call(op):
    """Run op(client) against the cached Supabase client, retrying with backoff
    on a transient network error before giving up."""
    sb = _supabase()
    if sb is None:
        return None
    last_err = None
    for delay in (0, *_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return op(sb)
        except Exception as e:  # noqa: BLE001
            transient = type(e).__name__ in _TRANSIENT_ERRORS or "disconnect" in str(e).lower()
            if not transient:
                raise
            last_err = e
            _supabase.cache_clear()
            sb = _supabase()
    raise last_err


def _fs_call(op):
    """Same retry-with-backoff wrapper as _sb_call, for Firestore calls."""
    db = _firestore()
    if db is None:
        return None
    last_err = None
    for delay in (0, *_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return op(db)
        except Exception as e:  # noqa: BLE001
            transient = type(e).__name__ in _TRANSIENT_ERRORS or "disconnect" in str(e).lower()
            if not transient:
                raise
            last_err = e
            _firestore.cache_clear()
            db = _firestore()
    raise last_err


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
    cached = _cache_get("decisions")
    if cached is not None:
        return cached
    if _firestore():
        docs = _fs_call(lambda db: list(db.collection("decisions").stream()))
        return _cache_put("decisions", {d.id: (d.to_dict() | {"ad_id": d.id}) for d in docs})
    if _supabase():
        rows = _sb_call(lambda sb: sb.table("decisions").select("*").execute()).data
        return _cache_put("decisions", {r["ad_id"]: r for r in rows})
    with _sqlite() as c:
        return _cache_put("decisions", {
            r[0]: {"status": r[1], "root_id": r[2], "note": r[3], "phash": r[4],
                   "decided_by": r[5], "decided_at": r[6]}
            for r in c.execute("SELECT ad_id,status,root_id,note,phash,decided_by,decided_at "
                               "FROM decisions")})


def decision_set(ad_id: str, status: str, *, root_id=None, note="", phash=None, by="") -> None:
    if _firestore():
        def op(db):
            ref = db.collection("decisions").document(ad_id)
            prev = ref.get().to_dict() or {}
            ref.set({
                "ad_id": ad_id, "status": status,
                "root_id": root_id or prev.get("root_id"),
                "note": note or prev.get("note", ""),
                "phash": phash or prev.get("phash"),
                "decided_by": by or prev.get("decided_by", ""),
                "decided_at": _now(),
            })
        _fs_call(op)
        _cache_clear("decisions")
        return
    if _supabase():
        def op(sb):
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
        _sb_call(op)
        _cache_clear("decisions")
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
    _cache_clear("decisions")


def decision_clear(ad_id: str) -> None:
    if _firestore():
        _fs_call(lambda db: db.collection("decisions").document(ad_id).delete())
        _cache_clear("decisions")
        return
    if _supabase():
        _sb_call(lambda sb: sb.table("decisions").delete().eq("ad_id", ad_id).execute())
        _cache_clear("decisions")
        return
    with _sqlite() as c:
        c.execute("DELETE FROM decisions WHERE ad_id=?", (ad_id,))
    _cache_clear("decisions")


def decided_phashes() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for v in decisions_load().values():
        if v.get("phash"):
            out[v["phash"]] = {"status": v["status"], "root_id": v.get("root_id")}
    return out


# ── CATALOGUE (roots + candidates) ─────────────────────────────────────────

# The storyboard fields (line of attack / RTB / attributes / story / root_kind) live on
# both a root and a candidate, carried straight through from the Gemini output.
_STORY_FIELDS = ("line_of_attack_type", "line_of_attack", "reason_to_believe",
                 "attributes_verbal", "attributes_visual", "story", "root_kind")


def _candidate_doc_id(name: str) -> str:
    return slug(name) or "unnamed"


def catalogue_load() -> dict:
    cached = _cache_get("catalogue")
    if cached is not None:
        return cached
    if _firestore():
        def op(db):
            roots = list(db.collection("roots").stream())
            if not roots:  # first run — seed
                for r in _seed_roots():
                    db.collection("roots").document(r["root_id"]).set(_root_row(r))
                roots = list(db.collection("roots").stream())
            cands = list(db.collection("candidates").stream())
            return roots, cands
        roots, cands = _fs_call(op)
        return _cache_put("catalogue", {
            "roots": [_root_from_row(d.to_dict() | {"root_id": d.id}) for d in roots],
            "candidates": [{"candidate_name": d.to_dict().get("candidate_name", d.id),
                            **{k: d.to_dict().get(k) for k in
                               ("mechanism", "visual_motif", "fits_our_brand") + _STORY_FIELDS},
                            "examples": d.to_dict().get("examples") or []} for d in cands],
        })
    if _supabase():
        def op(sb):
            roots = sb.table("roots").select("*").execute().data
            cands = sb.table("candidates").select("*").execute().data
            if not roots:  # first run — seed
                for r in _seed_roots():
                    sb.table("roots").upsert(_root_row(r)).execute()
                roots = sb.table("roots").select("*").execute().data
            return roots, cands
        roots, cands = _sb_call(op)
        return _cache_put("catalogue", {
            "roots": [_root_from_row(r) for r in roots],
            "candidates": [{"candidate_name": c["name"], **{k: c.get(k) for k in
                            ("mechanism", "visual_motif", "fits_our_brand") + _STORY_FIELDS},
                            "examples": c.get("examples") or []} for c in cands],
        })
    if ROOTS_CATALOGUE.exists():
        return _cache_put("catalogue", json.loads(ROOTS_CATALOGUE.read_text(encoding="utf-8")))
    return _cache_put("catalogue", {"roots": _seed_roots(), "candidates": []})


def _root_row(r: dict) -> dict:
    return {k: r.get(k) for k in ("root_id", "name", "status", "mechanism", "why_it_works",
            "visual_motif", "fits_our_brand", "compliance_notes") + _STORY_FIELDS} | {
        "competitor_examples": r.get("competitor_examples") or [],
        "our_executions": r.get("our_executions") or [],
        "versions": r.get("versions") or [],
        "version_capacity": r.get("version_capacity") or {},
        "updated_at": _now()}


def _root_from_row(r: dict) -> dict:
    r = dict(r)
    r.setdefault("competitor_examples", r.get("competitor_examples") or [])
    r.setdefault("our_executions", r.get("our_executions") or [])
    r.setdefault("versions", r.get("versions") or [])
    r.setdefault("version_capacity", r.get("version_capacity") or {})
    return r


def catalogue_save(cat: dict) -> None:
    if _firestore():
        def op(db):
            batch = db.batch()
            n = 0
            for r in cat.get("roots", []):
                batch.set(db.collection("roots").document(r["root_id"]), _root_row(r))
                n += 1
                if n >= 400:
                    batch.commit()
                    batch = db.batch()
                    n = 0
            have = {d.id for d in db.collection("candidates").stream()}
            want = set()
            for c in cat.get("candidates", []):
                doc_id = _candidate_doc_id(c["candidate_name"])
                want.add(doc_id)
                batch.set(db.collection("candidates").document(doc_id), {
                    "candidate_name": c["candidate_name"], "mechanism": c.get("mechanism"),
                    "visual_motif": c.get("visual_motif"), "fits_our_brand": c.get("fits_our_brand"),
                    **{k: c.get(k) for k in _STORY_FIELDS},
                    "examples": c.get("examples") or []})
                n += 1
                if n >= 400:
                    batch.commit()
                    batch = db.batch()
                    n = 0
            for gone in have - want:
                batch.delete(db.collection("candidates").document(gone))
                n += 1
                if n >= 400:
                    batch.commit()
                    batch = db.batch()
                    n = 0
            if n:
                batch.commit()
        _fs_call(op)
        _cache_clear("catalogue")
        return
    if _supabase():
        def op(sb):
            for r in cat.get("roots", []):
                sb.table("roots").upsert(_root_row(r)).execute()
            have = {c["name"] for c in sb.table("candidates").select("name").execute().data}
            want = {c["candidate_name"] for c in cat.get("candidates", [])}
            for c in cat.get("candidates", []):
                sb.table("candidates").upsert({
                    "name": c["candidate_name"], "mechanism": c.get("mechanism"),
                    "visual_motif": c.get("visual_motif"), "fits_our_brand": c.get("fits_our_brand"),
                    **{k: c.get(k) for k in _STORY_FIELDS},
                    "examples": c.get("examples") or []}).execute()
            for gone in have - want:
                sb.table("candidates").delete().eq("name", gone).execute()
        _sb_call(op)
        _cache_clear("catalogue")
        return
    cat = dict(cat)
    cat["updated_at"] = _now()
    ROOTS_CATALOGUE.write_text(json.dumps(cat, indent=2, ensure_ascii=False), encoding="utf-8")
    _cache_clear("catalogue")


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
    url = _upload_image_bytes(data, key, ext)
    if url is None:
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
    cached = _cache_get("ledger")
    if cached is not None:
        return cached
    if _firestore():
        docs = _fs_call(lambda db: list(db.collection("ledger").stream()))
        return _cache_put("ledger", {d.id: d.to_dict() for d in docs})
    if _supabase():
        rows = _sb_call(lambda sb: sb.table("ledger").select("*").execute()).data
        return _cache_put("ledger", {r["ad_id"]: r for r in rows})
    if SEEN.exists():
        try:
            return _cache_put("ledger", json.loads(SEEN.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            return {}
    return {}


def ledger_save(d: dict) -> None:
    if _firestore():
        def op(db):
            batch = db.batch()
            n = 0
            for k, v in d.items():
                batch.set(db.collection("ledger").document(k), {f: v.get(f) for f in (
                    "competitor", "page_name", "first_seen", "last_seen",
                    "times_seen", "was_active", "went_inactive_at")})
                n += 1
                if n >= 400:
                    batch.commit()
                    batch = db.batch()
                    n = 0
            if n:
                batch.commit()
        _fs_call(op)
        _cache_clear("ledger")
        return
    if _supabase():
        rows = [{"ad_id": k, **{f: v.get(f) for f in ("competitor", "page_name", "first_seen",
                "last_seen", "times_seen", "was_active", "went_inactive_at")}}
                for k, v in d.items()]

        def op(sb):
            for i in range(0, len(rows), 500):
                sb.table("ledger").upsert(rows[i:i + 500]).execute()
        _sb_call(op)
        _cache_clear("ledger")
        return
    SEEN.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    _cache_clear("ledger")


# ── MATCHES ────────────────────────────────────────────────────────────────

def matches_all() -> list[dict]:
    cached = _cache_get("matches")
    if cached is not None:
        return cached
    if _firestore():
        docs = _fs_call(lambda db: list(db.collection("matches").stream()))
        return _cache_put("matches", [_match_from_row(d.to_dict() | {"ad_id": d.id}) for d in docs])
    if _supabase():
        def op(sb):
            rows, page = [], 0
            while True:
                chunk = sb.table("matches").select("*").range(page * 1000, page * 1000 + 999).execute().data
                rows += chunk
                if len(chunk) < 1000:
                    break
                page += 1
            return rows
        rows = _sb_call(op)
        return _cache_put("matches", [_match_from_row(r) for r in rows])
    out = []
    for f in sorted(MATCHES_DIR.glob("*.json")):
        for m in json.loads(f.read_text(encoding="utf-8")).get("matches", []):
            m["_competitor_file"] = f.stem
            out.append(m)
    return _cache_put("matches", out)


def _match_from_row(r: dict) -> dict:
    return {
        "ad_id": r["ad_id"], "page_name": r.get("page_name"), "headline": r.get("headline"),
        "body": r.get("body"), "link_description": r.get("link_description"),
        "snapshot_url": r.get("snapshot_url"), "image": r.get("image_url"),
        "image_phash": r.get("image_phash"), "start_time": r.get("start_time"),
        "is_active": r.get("is_active"), "is_noise": r.get("is_noise"),
        "root": r.get("root") or {}, "versions": r.get("versions") or [],
        "version_capacity": r.get("version_capacity") or {},
        "_competitor_file": r.get("competitor"),
    }


def matches_existing_ids(competitor: str) -> set[str]:
    if _firestore():
        docs = _fs_call(lambda db: list(
            db.collection("matches").where("competitor", "==", competitor).stream()))
        return {d.id for d in docs}
    if _supabase():
        rows = _sb_call(lambda sb: sb.table("matches").select("ad_id").eq("competitor", competitor).execute()).data
        return {r["ad_id"] for r in rows}
    f = MATCHES_DIR / f"{slug(competitor)}.json"
    if f.exists():
        return {m["ad_id"] for m in json.loads(f.read_text(encoding="utf-8")).get("matches", [])}
    return set()


def _match_row(m: dict, competitor: str) -> dict:
    return {
        "ad_id": m["ad_id"], "competitor": competitor, "page_name": m.get("page_name"),
        "headline": m.get("headline"), "body": m.get("body"),
        "link_description": m.get("link_description"), "snapshot_url": m.get("snapshot_url"),
        "image_url": m.get("image"), "image_phash": m.get("image_phash"),
        "start_time": m.get("start_time"), "is_active": m.get("is_active"),
        "is_noise": bool(m.get("is_noise")), "root": m.get("root") or {},
        "versions": m.get("versions") or [], "version_capacity": m.get("version_capacity") or {},
        "analyzed_at": _now(),
    }


def matches_save(competitor: str, records: list[dict]) -> None:
    if _firestore():
        def op(db):
            batch = db.batch()
            n = 0
            for m in records:
                batch.set(db.collection("matches").document(m["ad_id"]), _match_row(m, competitor))
                n += 1
                if n >= 400:
                    batch.commit()
                    batch = db.batch()
                    n = 0
            if n:
                batch.commit()
        _fs_call(op)
        _cache_clear("matches")
        return
    if _supabase():
        rows = [_match_row(m, competitor) for m in records]

        def op(sb):
            for i in range(0, len(rows), 200):
                sb.table("matches").upsert(rows[i:i + 200]).execute()
        _sb_call(op)
        _cache_clear("matches")
        return
    f = MATCHES_DIR / f"{slug(competitor)}.json"
    f.write_text(json.dumps({"term": competitor, "analyzed_at": _now(),
                             "count": len(records), "matches": records},
                            indent=2, ensure_ascii=False), encoding="utf-8")
    _cache_clear("matches")


# ── IMAGES ─────────────────────────────────────────────────────────────────

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


_MAX_SIDE = 1080        # ad creatives are viewed as small cards; 1080px is plenty
_SHRINK_ABOVE = 150_000  # bytes -- don't touch images that are already small


def shrink_image(data: bytes, ext: str) -> bytes:
    """Downscale to _MAX_SIDE and recompress (same format). Returns the original
    bytes if it can't be decoded or the result isn't meaningfully smaller."""
    if len(data) <= _SHRINK_ABOVE:
        return data
    try:
        import io

        from PIL import Image
        img = Image.open(io.BytesIO(data))
        fmt = (img.format or "").upper()
        if max(img.size) > _MAX_SIDE:
            img.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.LANCZOS)
        out = io.BytesIO()
        if fmt in ("JPEG", "JPG") or ext in (".jpg", ".jpeg"):
            img.convert("RGB").save(out, "JPEG", quality=82, optimize=True, progressive=True)
        elif fmt == "WEBP" or ext == ".webp":
            img.save(out, "WEBP", quality=82)
        elif fmt == "PNG" or ext == ".png":
            img.save(out, "PNG", optimize=True)
        else:
            return data
        smaller = out.getvalue()
        return smaller if len(smaller) < len(data) * 0.9 else data
    except Exception:  # noqa: BLE001
        return data


def _upload_image_bytes(data: bytes, key: str, ext: str) -> str | None:
    """Try R2 first, then Supabase Storage. Returns the public URL, or None if
    neither backend is configured (caller falls back to local files)."""
    mime = _MIME.get(ext, "image/jpeg")
    data = shrink_image(data, ext)
    if _r2():
        try:
            _r2().put_object(Bucket=R2_BUCKET, Key=key, Body=data, ContentType=mime)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"R2 upload failed: {e}") from e
        return f"{R2_PUBLIC_URL}/{key}"
    if _supabase():
        try:
            _sb_call(lambda sb: sb.storage.from_(BUCKET).upload(
                key, data, {"upsert": "true", "content-type": mime}))
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Supabase upload failed: {e}") from e
        return f"{os.getenv('SUPABASE_URL', '').rstrip('/')}/storage/v1/object/public/{BUCKET}/{key}"
    return None


def put_image(local_path: str | Path, key: str) -> str | None:
    """Upload to R2 / Supabase Storage; return public URL. Local mode: return the path."""
    local_path = Path(local_path)
    if not local_path.exists():
        return None
    if not (_r2() or _supabase()):
        return str(local_path)
    try:
        url = _upload_image_bytes(local_path.read_bytes(), key, local_path.suffix.lower())
    except RuntimeError:
        return str(local_path)  # already-exists-style errors are fine to ignore, same as before
    return url or str(local_path)
