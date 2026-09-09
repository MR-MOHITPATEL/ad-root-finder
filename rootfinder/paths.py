"""Local filesystem layout for Phase 1. Everything lives under data/rf/."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = ROOT / "config"
COMPLIANCE_DIR = ROOT / "compliance"

DATA = ROOT / "data" / "rf"
ADS_DIR = DATA / "ads"              # {competitor}.json — raw scraped ads
IMAGES_DIR = DATA / "images"        # {competitor}/{ad_id}_{i}.jpg
MATCHES_DIR = DATA / "matches"      # {competitor}.json — root-match + compliance results
BRIEFS_DIR = DATA / "briefs"        # {ad_id}__{product}.md
STATE_DIR = DATA / "state"          # browser storage_state, run metadata

ROOTS_CATALOGUE = ROOT / "data" / "roots" / "catalogue.json"
DECISIONS = DATA / "decisions.json"        # {ad_id: {status, root_id, note, decided_at}}
SEEN = DATA / "seen.json"                  # {ad_id: {first_seen, last_seen, times_seen, ...}}

for _d in (ADS_DIR, IMAGES_DIR, MATCHES_DIR, BRIEFS_DIR, STATE_DIR, ROOTS_CATALOGUE.parent):
    _d.mkdir(parents=True, exist_ok=True)

STORAGE_STATE = STATE_DIR / "fb_storage_state.json"


def slug(text: str) -> str:
    out = "".join(c if c.isalnum() or c in "_- " else "_" for c in text.strip().lower())
    return "_".join(out.split())
