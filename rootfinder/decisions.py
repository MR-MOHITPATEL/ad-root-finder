"""Human review decisions on competitor ads — keep/maybe/skip.

Storage is handled by rootfinder.store (Supabase in prod, SQLite locally).
Many reviewers can decide at once without losing each other's writes.
"""
from __future__ import annotations

from . import store

STATUSES = ("works", "maybe", "no")


def load() -> dict:
    return store.decisions_load()


def set_decision(ad_id: str, status: str, *, root_id: str | None = None,
                 note: str = "", phash: str | None = None, by: str = "") -> None:
    if status not in STATUSES:
        raise ValueError(status)
    store.decision_set(ad_id, status, root_id=root_id, note=note, phash=phash, by=by)


def set_many(ad_ids: list[str], status: str, *, root_id=None, note="", phash=None, by="") -> None:
    for aid in ad_ids:
        set_decision(aid, status, root_id=root_id, note=note, phash=phash, by=by)


def clear(ad_id: str) -> None:
    store.decision_clear(ad_id)


def decided_phashes() -> dict[str, dict]:
    return store.decided_phashes()
