"""Finish the Supabase -> Firestore migration:
  1. load the FULL ledger (the first pass got only Supabase's 1000-row default page)
  2. re-host any image still pointing at Supabase / a local path, and fix the
     references in Firestore (matches, root examples, candidate examples)
Idempotent -- safe to rerun.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from rootfinder import store

MARKER = "/storage/v1/object/public/" + store.BUCKET + "/"


def _raw_supabase():
    import httpx
    from supabase import ClientOptions, create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"],
                         options=ClientOptions(httpx_client=httpx.Client(http2=False, timeout=30)))


def _rehost(url: str) -> str:
    if url.startswith(store.R2_PUBLIC_URL):
        return url
    try:
        if url.startswith("http"):
            if MARKER not in url:
                return url
            key = url.split(MARKER, 1)[-1]
            data = requests.get(url, timeout=60).content
            ext = "." + key.rsplit(".", 1)[-1].lower() if "." in key else ".jpg"
            return store._upload_image_bytes(data, key, ext) or url
        lp = Path(url)
        if lp.exists():
            return store.put_image(url, f"{lp.parent.name}/{lp.name}") or url
    except Exception as e:  # noqa: BLE001
        print(f"  still failing: {url} ({e})", flush=True)
    return url


def main() -> None:
    assert store.mode() == "firestore"

    print("Loading the full ledger from Supabase (paginated)...", flush=True)
    sb = _raw_supabase()
    rows, page = [], 0
    while True:
        chunk = sb.table("ledger").select("*").range(page * 1000, page * 1000 + 999).execute().data
        rows += chunk
        if len(chunk) < 1000:
            break
        page += 1
    store.ledger_save({r["ad_id"]: r for r in rows})
    print(f"  {len(rows)} ledger rows written", flush=True)

    print("Fixing leftover image references...", flush=True)
    fixed = 0
    by_comp: dict[str, list[dict]] = {}
    for m in store.matches_all():
        img = m.get("image")
        if img and not img.startswith(store.R2_PUBLIC_URL):
            new = _rehost(img)
            if new != img:
                m["image"] = new
                by_comp.setdefault(m.get("_competitor_file") or "unknown", []).append(m)
                fixed += 1
    for comp, ms in by_comp.items():
        store.matches_save(comp, ms)
    print(f"  {fixed} match images fixed", flush=True)

    cat = store.catalogue_load()
    changed = 0
    for r in cat["roots"]:
        for coll in ("competitor_examples", "our_executions"):
            for e in r.get(coll) or []:
                if e.get("image_url") and not e["image_url"].startswith(store.R2_PUBLIC_URL):
                    new = _rehost(e["image_url"])
                    if new != e["image_url"]:
                        e["image_url"] = new
                        changed += 1
    for c in cat["candidates"]:
        for e in c.get("examples") or []:
            if e.get("image_url") and not e["image_url"].startswith(store.R2_PUBLIC_URL):
                new = _rehost(e["image_url"])
                if new != e["image_url"]:
                    e["image_url"] = new
                    changed += 1
    if changed:
        store.catalogue_save(cat)
    print(f"  {changed} catalogue images fixed", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
