"""Read/write config/competitors.yaml — the brand list shown in the dashboard.

A brand entered in the dashboard is saved here automatically, so you pick it
from a list next time instead of retyping.
"""
from __future__ import annotations

import yaml

from .paths import CONFIG_DIR

PATH = CONFIG_DIR / "competitors.yaml"


def load() -> dict:
    return yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}


def save(cfg: dict) -> None:
    PATH.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


def brands() -> list[dict]:
    """[{name, search, tier}] — every_run first."""
    cs = load().get("competitors", [])
    return sorted(cs, key=lambda c: (c.get("tier") != "every_run", c.get("name", "")))


def keyword_searches() -> list[str]:
    return load().get("keyword_searches", [])


def add(name: str, *, search: str | None = None, tier: str = "broad_only") -> dict:
    """Add a brand if not already present (matched case-insensitively on name/search)."""
    name = name.strip()
    search = (search or name).strip()
    cfg = load()
    cfg.setdefault("competitors", [])
    key = name.lower()
    for c in cfg["competitors"]:
        if c.get("name", "").lower() == key or c.get("search", "").lower() == search.lower():
            return c
    entry = {"name": name, "search": search, "tier": tier}
    cfg["competitors"].append(entry)
    save(cfg)
    return entry


def set_tier(name: str, tier: str) -> None:
    cfg = load()
    for c in cfg.get("competitors", []):
        if c.get("name", "").lower() == name.lower():
            c["tier"] = tier
    save(cfg)
