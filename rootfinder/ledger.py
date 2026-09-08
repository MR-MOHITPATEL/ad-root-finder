"""Seen-ledger — tracks every competitor ad ever scraped, so we know what's new.

data/rf/seen.json:
  { "<ad_id>": {
      "competitor": str, "page_name": str,
      "first_seen": iso, "last_seen": iso, "times_seen": int,
      "was_active": bool, "went_inactive_at": iso|null
  } }
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .paths import SEEN


def load() -> dict:
    if SEEN.exists():
        try:
            return json.loads(SEEN.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _save(d: dict) -> None:
    SEEN.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def update(competitor: str, ads: list[dict]) -> dict:
    """Record this scrape. Returns {'new': [ad_ids], 'returning': int, 'newly_inactive': [ad_ids]}."""
    now = datetime.now(timezone.utc).isoformat()
    d = load()
    scraped_ids = {a["ad_id"] for a in ads}
    new, returning = [], 0

    for a in ads:
        aid = a["ad_id"]
        rec = d.get(aid)
        if rec is None:
            d[aid] = {
                "competitor": competitor,
                "page_name": a.get("page_name"),
                "first_seen": now,
                "last_seen": now,
                "times_seen": 1,
                "was_active": bool(a.get("is_active", True)),
                "went_inactive_at": None,
            }
            new.append(aid)
        else:
            rec["last_seen"] = now
            rec["times_seen"] = rec.get("times_seen", 1) + 1
            rec["was_active"] = bool(a.get("is_active", True))
            returning += 1

    # ads we've seen before for this competitor that didn't come back this scrape
    newly_inactive = []
    for aid, rec in d.items():
        if (rec.get("competitor") == competitor and aid not in scraped_ids
                and rec.get("was_active") and not rec.get("went_inactive_at")):
            rec["went_inactive_at"] = now
            rec["was_active"] = False
            newly_inactive.append(aid)

    _save(d)
    return {"new": new, "returning": returning, "newly_inactive": newly_inactive}


def competitor_stats() -> dict[str, dict]:
    """Per-competitor: {last_seen, total, days_since_new}."""
    d = load()
    out: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    for rec in d.values():
        c = rec.get("competitor") or "?"
        s = out.setdefault(c, {"total": 0, "last_seen": "", "last_new": ""})
        s["total"] += 1
        s["last_seen"] = max(s["last_seen"], rec.get("last_seen", ""))
        s["last_new"] = max(s["last_new"], rec.get("first_seen", ""))
    for c, s in out.items():
        try:
            s["days_since_new"] = (now - datetime.fromisoformat(s["last_new"])).days
        except ValueError:
            s["days_since_new"] = None
    return out
