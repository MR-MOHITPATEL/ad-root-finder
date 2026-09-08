"""Shared data access for the web UI — reads the same files the CLI writes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from rootfinder import decisions as dec
from rootfinder.imagehash import hamming, phash_file
from rootfinder.paths import ADS_DIR, BRIEFS_DIR, MATCHES_DIR, ROOTS_CATALOGUE

NEAR = 8


def _load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def catalogue() -> dict:
    return _load(ROOTS_CATALOGUE, {"roots": [], "candidates": []})


def days_running(m: dict) -> int:
    s = (m.get("start_time") or "")[:10]
    try:
        d = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        return max((datetime.now(timezone.utc) - d).days, 0)
    except ValueError:
        return 0


@lru_cache(maxsize=4096)
def _phash_cached(path: str, mtime: float) -> str | None:
    return phash_file(path)


def ph(m: dict) -> str | None:
    if m.get("image_phash"):
        return m["image_phash"]
    p = m.get("image")
    if not p:
        return None
    try:
        mt = Path(p).stat().st_mtime
    except OSError:
        return None
    return _phash_cached(p, mt)


def all_matches() -> list[dict]:
    out: list[dict] = []
    for f in sorted(MATCHES_DIR.glob("*.json")):
        for m in _load(f, {}).get("matches", []):
            m["_competitor_file"] = f.stem
            m["_days"] = days_running(m)
            out.append(m)
    return out


def _auto_carry(real: list[dict]) -> int:
    D = dec.load()
    decided_ph = dec.decided_phashes()
    n = 0
    for m in real:
        if m["ad_id"] in D:
            continue
        h = ph(m)
        for phash, info in decided_ph.items():
            if hamming(h, phash) <= NEAR:
                dec.set_decision(m["ad_id"], info["status"], root_id=info.get("root_id"),
                                 note="auto (same creative)", phash=h)
                n += 1
                break
    return n


def cluster(items: list[dict]) -> list[list[dict]]:
    clusters: list[list[dict]] = []
    for m in items:
        h = ph(m)
        for c in clusters:
            if hamming(h, ph(c[0])) <= NEAR:
                c.append(m)
                break
        else:
            clusters.append([m])
    return clusters


def review_state():
    """Returns (pending_clusters, approved, maybe, D, auto_count)."""
    matches = all_matches()
    real = [m for m in matches if not m.get("is_noise")]
    auto = _auto_carry(real)
    D = dec.load()
    pending = [m for m in real if m["ad_id"] not in D]
    approved = [m for m in real if D.get(m["ad_id"], {}).get("status") == "works"]
    maybe = [m for m in real if D.get(m["ad_id"], {}).get("status") == "maybe"]
    clusters = cluster(pending)
    return clusters, approved, maybe, D, auto


def brief_exists(ad_id: str, product: str = "arjuna-tea") -> bool:
    return (BRIEFS_DIR / f"{ad_id}__{product}.md").exists()
