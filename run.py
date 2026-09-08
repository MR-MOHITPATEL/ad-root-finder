"""
Competitor Ad Root Finder — CLI.

  python run.py scan                    # fetch + analyze every_run competitors
  python run.py scan --broad            # + broad_only competitors + keyword searches
  python run.py scan --deep             # include past (inactive) ads
  python run.py fetch "Kapiva"          # fetch one term only
  python run.py analyze                 # analyze whatever's already fetched
  python run.py brief 1234567890        # adaptation brief for a matched ad
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


def cmd_scan(broad: bool, headed: bool, login: bool, skip_analyze: bool, deep: bool = False) -> None:
    cfg = _load_config()
    comps = [c for c in cfg["competitors"] if broad or c.get("tier") == "every_run"]
    brand_terms = [c["search"] for c in comps]
    kw_terms = cfg.get("keyword_searches", []) if broad else []

    o = _opts(cfg, headless=not headed, login=login, deep=deep)
    if brand_terms:
        print(f"=== FETCH brands ({len(brand_terms)}) ===")
        fetch(brand_terms, o)
    if kw_terms:
        print(f"=== FETCH keyword searches ({len(kw_terms)}) ===")
        fetch(kw_terms, FetchOptions(**{**o.__dict__, "brand_filter": False}))

    if not skip_analyze:
        print("=== ANALYZE ===")
        analyze.run(brand_terms + kw_terms)


def cmd_fetch(terms: list[str], headed: bool, login: bool, deep: bool = False) -> None:
    cfg = _load_config()
    fetch(terms, _opts(cfg, headless=not headed, login=login, deep=deep))


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

    f = sub.add_parser("fetch")
    f.add_argument("terms", nargs="+")
    f.add_argument("--deep", action="store_true", help="all-time: pull past (inactive) ads too")
    f.add_argument("--headed", action="store_true")
    f.add_argument("--login", action="store_true")

    a = sub.add_parser("analyze")
    a.add_argument("terms", nargs="*")
    a.add_argument("--force", action="store_true")
    a.add_argument("--limit", type=int)

    b = sub.add_parser("brief")
    b.add_argument("ad_id", nargs="?")
    b.add_argument("--product", default="arjuna-tea")
    b.add_argument("--all-approved", action="store_true",
                   help="generate a brief for every 'works' ad that doesn't have one yet")

    w = sub.add_parser("web")
    w.add_argument("--port", type=int, default=8000)

    args = ap.parse_args()
    if args.cmd == "scan":
        cmd_scan(args.broad, args.headed, args.login, args.no_analyze, args.deep)
    elif args.cmd == "fetch":
        cmd_fetch(args.terms, args.headed, args.login, args.deep)
    elif args.cmd == "analyze":
        analyze.run(args.terms or None, force=args.force, limit=args.limit)
    elif args.cmd == "brief":
        from rootfinder.brief import generate
        from rootfinder import decisions
        from rootfinder.paths import BRIEFS_DIR
        if args.all_approved:
            D = decisions.load()
            approved = [aid for aid, v in D.items() if v.get("status") == "works"]
            todo = [a for a in approved if not (BRIEFS_DIR / f"{a}__{args.product}.md").exists()]
            print(f"{len(todo)} approved ads need a brief")
            for aid in todo:
                try:
                    generate(aid, args.product)
                except Exception as e:  # noqa: BLE001
                    print(f"  {aid} FAILED: {e}")
        elif args.ad_id:
            generate(args.ad_id, args.product)
        else:
            print("give an ad_id or --all-approved")
    elif args.cmd == "web":
        cmd_web(args.port)


if __name__ == "__main__":
    main()
