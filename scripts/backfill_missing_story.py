"""One-off: re-analyze ads whose match record is missing the story field,
rebuilding a pseudo-ad straight from the match record (no raw scrape file
needed -- covers ads whose local data/rf/ads/*.json cache no longer exists).
Preserves any existing versions/version_capacity. Flushes to Supabase every
FLUSH_EVERY ads so a kill mid-run doesn't lose everything.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rootfinder import store
from rootfinder.analyze import _attach_to_catalogue, analyze_ad, load_catalogue, save_catalogue

FLUSH_EVERY = 20


def _pseudo_ad(m: dict) -> dict:
    return {
        "ad_id": m["ad_id"], "page_name": m.get("page_name"), "headline": m.get("headline"),
        "body": m.get("body"), "link_description": m.get("link_description"),
        "snapshot_url": m.get("snapshot_url"), "image_urls": [m["image"]] if m.get("image") else [],
        "image_url": m.get("image"), "image_phash": m.get("image_phash"),
        "start_time": m.get("start_time"), "is_active": m.get("is_active"),
    }


def main() -> None:
    ms = store.matches_all()
    real = [m for m in ms if not m.get("is_noise")]
    missing = [m for m in real if not (m.get("root") or {}).get("story")]
    print(f"{len(missing)} ads missing story", flush=True)

    cat = load_catalogue()
    roots = cat["roots"]
    cands_hint = sorted((c for c in cat.get("candidates", []) if len(c.get("examples") or []) >= 3),
                        key=lambda c: len(c.get("examples") or []), reverse=True)[:25]

    pending: dict[str, list[dict]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(analyze_ad, _pseudo_ad(m), roots, cands_hint): m for m in missing}
        for i, fut in enumerate(as_completed(futs), 1):
            m = futs[fut]
            rec = fut.result()
            rec["versions"], rec["version_capacity"] = m.get("versions"), m.get("version_capacity")
            term = m.get("_competitor_file") or "unknown"
            pending.setdefault(term, []).append(rec)
            r = rec.get("root") or {}
            print(f"[{i}/{len(missing)}] {m['ad_id']} ({term}) story={bool(r.get('story'))} "
                  f"err={r.get('error')}", flush=True)
            done += 1
            if done % FLUSH_EVERY == 0:
                for t, recs in pending.items():
                    _attach_to_catalogue(cat, recs, t)
                    store.matches_save(t, recs)
                print(f"  -- flushed {done} to Supabase --", flush=True)
                pending = {}

    for t, recs in pending.items():
        _attach_to_catalogue(cat, recs, t)
        store.matches_save(t, recs)
    save_catalogue(cat)
    print("done", flush=True)


if __name__ == "__main__":
    main()
