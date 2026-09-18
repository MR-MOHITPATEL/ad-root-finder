"""One-off: fetch + analyze + version the newly-added broad_only brands only
(skips re-scraping every_run / already-established broad_only brands), using
country=All since these have no pinned page_ids -- keyword search only.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rootfinder import analyze, versions as versions_mod
from rootfinder.fetch import FetchOptions, fetch
from run import _load_config, _opts, _page_ids_map

# The 6 broad_only brands that existed before this batch -- everything else
# tagged broad_only in the config is the new set to scan now.
_PRE_EXISTING = {"andme", "Man Matters", "Bodywise", "Traya", "Setu Nutrition", "Saffola"}


def main() -> None:
    cfg = _load_config()
    new_terms = [c["search"] for c in cfg["competitors"]
                if c.get("tier") == "broad_only" and c["search"] not in _PRE_EXISTING]
    print(f"{len(new_terms)} new brands to scan", flush=True)

    o = _opts(cfg, headless=True, login=False)
    fetch(new_terms, FetchOptions(**{**o.__dict__, "country": "ALL"}), _page_ids_map(cfg))

    print("=== ANALYZE ===", flush=True)
    analyze.run(new_terms)

    print("=== VERSIONS (new ads only) ===", flush=True)
    versions_mod.run_for_all_ads()
    print("done", flush=True)


if __name__ == "__main__":
    main()
