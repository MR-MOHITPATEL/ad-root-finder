"""Resume scan_new_brands.py after a transient network blip crashed it
mid-fetch: fetch only the brands still missing their ads file, then run
analyze + versions for the full new-brand set (analyze/versions both skip
already-done work, so this is safe to run over the full 63 again).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rootfinder import analyze, versions as versions_mod
from rootfinder.fetch import FetchOptions, fetch
from rootfinder.paths import ADS_DIR, slug
from run import _load_config, _opts, _page_ids_map

_PRE_EXISTING = {"andme", "Man Matters", "Bodywise", "Traya", "Setu Nutrition", "Saffola"}


def main() -> None:
    cfg = _load_config()
    new_terms = [c["search"] for c in cfg["competitors"]
                if c.get("tier") == "broad_only" and c["search"] not in _PRE_EXISTING]
    remaining = [t for t in new_terms if not (ADS_DIR / f"{slug(t)}.json").exists()]
    print(f"{len(remaining)} of {len(new_terms)} still need fetching", flush=True)

    if remaining:
        o = _opts(cfg, headless=True, login=False)
        fetch(remaining, FetchOptions(**{**o.__dict__, "country": "ALL"}), _page_ids_map(cfg))

    print("=== ANALYZE (all new brands) ===", flush=True)
    analyze.run(new_terms)

    print("=== VERSIONS (new ads only) ===", flush=True)
    versions_mod.run_for_all_ads()
    print("done", flush=True)


if __name__ == "__main__":
    main()
