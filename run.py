"""
Competitor Ad Root Finder — CLI.

  python run.py scan                    # fetch + analyze every_run competitors
  python run.py scan --broad            # + broad_only competitors + keyword searches
  python run.py scan --deep             # include past (inactive) ads
  python run.py fetch "Kapiva"          # fetch one term only
  python run.py analyze                 # analyze whatever's already fetched
  python run.py web                     # local review UI at localhost:8000

Storage: Supabase when SUPABASE_URL is set, else local files under data/rf/.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from rootfinder.fetch import FetchOptions, fetch
from rootfinder import analyze
from rootfinder import versions as versions_mod
from rootfinder.paths import CONFIG_DIR, ROOT

COMPETITORS_YAML = CONFIG_DIR / "competitors.yaml"


def _load_config() -> dict:
    return yaml.safe_load(COMPETITORS_YAML.read_text(encoding="utf-8"))


def _opts(cfg: dict, **over) -> FetchOptions:
    d = cfg.get("defaults", {})
    base = dict(
        country=d.get("country", "IN"),
        media_type=d.get("media_type", "all"),
        active_status=d.get("active_status", "active"),
        max_ads=d.get("max_ads", 60),
    )
    base.update(over)
    if base.pop("deep", False):
        base["active_status"] = "all"          # every ad ever run, active or not
        base["max_ads"] = max(base.get("max_ads", 60), 150)
        base["hard_cap_seconds"] = 300
    return FetchOptions(**base)


def _page_ids_map(cfg: dict) -> dict[str, list[str]]:
    m: dict[str, list[str]] = {}
    for c in cfg.get("competitors", []):
        pids = [str(p) for p in (c.get("page_ids") or [])]
        for key in (c.get("search"), c.get("name")):
            if key:
                m[key] = pids
    return m


def cmd_scan(broad: bool, headed: bool, login: bool, skip_analyze: bool, deep: bool = False,
            skip_versions: bool = False) -> None:
    cfg = _load_config()
    comps = [c for c in cfg["competitors"] if broad or c.get("tier") == "every_run"]
    # Pinned-page brands: the page itself only ever runs India-targeted ads, so
    # country=IN vs All returns the same ads -- keep it narrow (less noise, less
    # Gemini spend). No-page-id brands + keyword searches rely on Meta's text
    # search instead of a pinned page, so country=All can genuinely surface ads
    # India-scoped search misses -- worth the extra noise there specifically.
    pinned_terms = [c["search"] for c in comps if c.get("page_ids")]
    unpinned_terms = [c["search"] for c in comps if not c.get("page_ids")]
    kw_terms = cfg.get("keyword_searches", []) if broad else []

    o = _opts(cfg, headless=not headed, login=login, deep=deep)
    if pinned_terms:
        print(f"=== FETCH pinned-page brands ({len(pinned_terms)}), country=IN ===")
        fetch(pinned_terms, o, _page_ids_map(cfg))
    if unpinned_terms:
        print(f"=== FETCH no-page-id brands ({len(unpinned_terms)}), country=All ===")
        fetch(unpinned_terms, FetchOptions(**{**o.__dict__, "country": "ALL"}), _page_ids_map(cfg))
    if kw_terms:
        print(f"=== FETCH keyword searches ({len(kw_terms)}), country=All ===")
        fetch(kw_terms, FetchOptions(**{**o.__dict__, "country": "ALL", "brand_filter": False}))
    brand_terms = pinned_terms + unpinned_terms

    if not skip_analyze:
        print("=== ANALYZE ===")
        analyze.run(brand_terms + kw_terms)

        # Every ad that just got analyzed is brand-new to the matches table, so it
        # has no versions yet -- run_for_all_ads() only processes ads without one
        # (force=False), so this only ever costs Gemini calls for what's actually new.
        if not skip_versions:
            print("=== VERSIONS (new ads only) ===")
            versions_mod.run_for_all_ads()


def cmd_fetch(terms: list[str], headed: bool, login: bool, deep: bool = False) -> None:
    cfg = _load_config()
    fetch(terms, _opts(cfg, headless=not headed, login=login, deep=deep), _page_ids_map(cfg))


def cmd_web(port: int = 8000) -> None:
    subprocess.run([sys.executable, "-m", "uvicorn", "web.app:app",
                    "--host", "127.0.0.1", "--port", str(port), "--reload"],
                   cwd=str(ROOT), check=False)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--broad", action="store_true")
    s.add_argument("--deep", action="store_true", help="all-time: pull past (inactive) ads too")
    s.add_argument("--headed", action="store_true")
    s.add_argument("--login", action="store_true")
    s.add_argument("--no-analyze", action="store_true")
    s.add_argument("--no-versions", action="store_true",
                   help="skip generating the 3-version slate for newly analyzed ads")

    f = sub.add_parser("fetch")
    f.add_argument("terms", nargs="+")
    f.add_argument("--deep", action="store_true", help="all-time: pull past (inactive) ads too")
    f.add_argument("--headed", action="store_true")
    f.add_argument("--login", action="store_true")

    a = sub.add_parser("analyze")
    a.add_argument("terms", nargs="*")
    a.add_argument("--force", action="store_true")
    a.add_argument("--limit", type=int)
    a.add_argument("--no-versions", action="store_true",
                   help="skip generating the 3-version slate for newly analyzed ads")

    w = sub.add_parser("web")
    w.add_argument("--port", type=int, default=8000)

    args = ap.parse_args()
    if args.cmd == "scan":
        cmd_scan(args.broad, args.headed, args.login, args.no_analyze, args.deep, args.no_versions)
    elif args.cmd == "fetch":
        cmd_fetch(args.terms, args.headed, args.login, args.deep)
    elif args.cmd == "analyze":
        analyze.run(args.terms or None, force=args.force, limit=args.limit)
        if not args.no_versions:
            # Independent of --force above: that's about re-analyzing, this only
            # ever generates versions for ads that don't have one yet.
            print("=== VERSIONS (new ads only) ===")
            versions_mod.run_for_all_ads(limit=args.limit)
    elif args.cmd == "web":
        cmd_web(args.port)


if __name__ == "__main__":
    main()
