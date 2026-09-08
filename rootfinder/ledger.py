"""Seen-ledger — tracks every competitor ad ever scraped, so we know what's new.
Storage via rootfinder.store (Supabase in prod, local JSON otherwise).
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import store


def load() -> dict:
    return store.ledger_load()


def update(competitor: str, ads: list[dict]) -> dict:
    """Record this scrape. Returns {new: [ids], returning: int, newly_inactive: [ids]}."""
    now = datetime.now(timezone.utc).isoformat()
    d = load()
    scraped = {a["ad_id"] for a in ads}
    new, returning = [], 0

    for a in ads:
        aid = a["ad_id"]
        rec = d.get(aid)
        if rec is None:
            d[aid] = {"competitor": competitor, "page_name": a.get("page_name"),
                      "first_seen": now, "last_seen": now, "times_seen": 1,
                      "was_active": bool(a.get("is_active", True)), "went_inactive_at": None}
            new.append(aid)
        else:
            rec["last_seen"] = now
            rec["times_seen"] = (rec.get("times_seen") or 1) + 1
            rec["was_active"] = bool(a.get("is_active", True))
            returning += 1

    newly_inactive = []
    for aid, rec in d.items():
        if (rec.get("competitor") == competitor and aid not in scraped
                and rec.get("was_active") and not rec.get("went_inactive_at")):
            rec["went_inactive_at"] = now
            rec["was_active"] = False
            newly_inactive.append(aid)

    store.ledger_save(d)
    return {"new": new, "returning": returning, "newly_inactive": newly_inactive}


def competitor_stats() -> dict[str, dict]:
    d = load()
    out: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    for rec in d.values():
        c = rec.get("competitor") or "?"
        s = out.setdefault(c, {"total": 0, "last_seen": "", "last_new": ""})
        s["total"] += 1
        s["last_seen"] = max(s["last_seen"], rec.get("last_seen") or "")
        s["last_new"] = max(s["last_new"], rec.get("first_seen") or "")
    for s in out.values():
        try:
            s["days_since_new"] = (now - datetime.fromisoformat(s["last_new"])).days
        except (ValueError, TypeError):
            s["days_since_new"] = None
    return out
