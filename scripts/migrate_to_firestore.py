"""One-time migration: Supabase -> Firestore + Cloudflare R2.

Run AFTER setting GCP_PROJECT_ID / GCP_SERVICE_ACCOUNT_JSON / R2_ACCOUNT_ID /
R2_BUCKET / R2_PUBLIC_URL / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY in .env,
while SUPABASE_URL/SUPABASE_KEY are STILL also set -- reads go straight to
Supabase via a raw client (bypassing store.py's backend priority, which now
favours Firestore); writes go through the normal store.* functions, which
route to Firestore/R2 now that those env vars are present.

Also writes a full local JSON backup to data/supabase_backup.json before
touching anything, in case something needs to be redone.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from rootfinder import store
from rootfinder.paths import DATA


def _raw_supabase():
    import httpx
    from supabase import ClientOptions, create_client
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key, options=ClientOptions(httpx_client=httpx.Client(http2=False, timeout=30)))


def main() -> None:
    assert store._firestore() is not None, (
        "GCP_PROJECT_ID / GCP_SERVICE_ACCOUNT_JSON not set in .env -- set them first")
    assert os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"), (
        "SUPABASE_URL / SUPABASE_KEY must still be set so this script can read the old data")

    sb = _raw_supabase()
    print("Reading from Supabase...", flush=True)
    decisions = {r["ad_id"]: r for r in sb.table("decisions").select("*").execute().data}
    roots = sb.table("roots").select("*").execute().data
    cands = sb.table("candidates").select("*").execute().data
    ledger_rows = sb.table("ledger").select("*").execute().data
    matches_rows: list[dict] = []
    page = 0
    while True:
        chunk = sb.table("matches").select("*").range(page * 1000, page * 1000 + 999).execute().data
        matches_rows += chunk
        if len(chunk) < 1000:
            break
        page += 1
    print(f"  {len(decisions)} decisions, {len(roots)} roots, {len(cands)} candidates, "
          f"{len(ledger_rows)} ledger rows, {len(matches_rows)} matches", flush=True)

    backup = DATA / "supabase_backup.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps({
        "decisions": decisions, "roots": roots, "candidates": cands,
        "ledger": ledger_rows, "matches": matches_rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  backed up to {backup}", flush=True)

    # -- images: re-host every distinct image on R2 (parallel, idempotent), rewrite references --
    from concurrent.futures import ThreadPoolExecutor

    marker = "/storage/v1/object/public/" + store.BUCKET + "/"

    def _rehost(url: str) -> str:
        """Return the R2 URL for `url` (a Supabase Storage URL or a local file path)."""
        try:
            if url.startswith("http"):
                if marker not in url:
                    return url
                key = url.split(marker, 1)[-1]
            else:
                lp = Path(url)
                if not lp.exists():
                    return url
                key = f"{lp.parent.name}/{lp.name}"
            new_url = f"{store.R2_PUBLIC_URL}/{key}"
            try:
                store._r2().head_object(Bucket=store.R2_BUCKET, Key=key)
                return new_url                                  # already copied on a previous run
            except Exception:  # noqa: BLE001
                pass
            if url.startswith("http"):
                data = requests.get(url, timeout=30).content
                ext = "." + key.rsplit(".", 1)[-1].lower() if "." in key else ".jpg"
                return store._upload_image_bytes(data, key, ext) or url
            return store.put_image(url, key) or url
        except Exception as e:  # noqa: BLE001
            print(f"  rehost failed for {url}: {e}", flush=True)
            return url

    all_urls: set[str] = set()
    for m in matches_rows:
        if m.get("image_url"):
            all_urls.add(m["image_url"])
    for r in roots:
        for coll in ("competitor_examples", "our_executions"):
            for e in (r.get(coll) or []):
                if e.get("image_url"):
                    all_urls.add(e["image_url"])
    for c in cands:
        for e in (c.get("examples") or []):
            if e.get("image_url"):
                all_urls.add(e["image_url"])
    print(f"Re-hosting {len(all_urls)} distinct images to R2 (parallel)...", flush=True)
    url_map: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, (u, new_u) in enumerate(zip(all_urls, ex.map(_rehost, all_urls)), 1):
            url_map[u] = new_u
            if i % 100 == 0:
                print(f"  {i}/{len(all_urls)} images done", flush=True)
    moved = sum(1 for u, n in url_map.items() if u != n)
    print(f"  {moved} images now on R2, {len(url_map) - moved} left unchanged", flush=True)

    for m in matches_rows:
        if m.get("image_url"):
            m["image_url"] = url_map.get(m["image_url"], m["image_url"])
    for r in roots:
        for coll in ("competitor_examples", "our_executions"):
            for e in (r.get(coll) or []):
                if e.get("image_url"):
                    e["image_url"] = url_map.get(e["image_url"], e["image_url"])
    for c in cands:
        for e in (c.get("examples") or []):
            if e.get("image_url"):
                e["image_url"] = url_map.get(e["image_url"], e["image_url"])

    print("Writing to Firestore...", flush=True)
    for ad_id, d in decisions.items():
        store.decision_set(ad_id, d["status"], root_id=d.get("root_id"), note=d.get("note", ""),
                           phash=d.get("phash"), by=d.get("decided_by", ""))
    print(f"  {len(decisions)} decisions written", flush=True)

    cat = {
        "roots": [store._root_from_row(r) for r in roots],
        "candidates": [{"candidate_name": c["name"], **{k: c.get(k) for k in
                        ("mechanism", "visual_motif", "fits_our_brand") + store._STORY_FIELDS},
                        "examples": c.get("examples") or []} for c in cands],
    }
    store.catalogue_save(cat)
    print(f"  {len(roots)} roots, {len(cands)} candidates written", flush=True)

    ledger_dict = {r["ad_id"]: r for r in ledger_rows}
    store.ledger_save(ledger_dict)
    print(f"  {len(ledger_rows)} ledger rows written", flush=True)

    by_competitor: dict[str, list[dict]] = {}
    for r in matches_rows:
        m = store._match_from_row(r)
        by_competitor.setdefault(r.get("competitor") or "unknown", []).append(m)
    for competitor, ms in by_competitor.items():
        store.matches_save(competitor, ms)
    print(f"  {len(matches_rows)} matches written across {len(by_competitor)} competitors", flush=True)

    print("done -- verify with `python -c \"from rootfinder import store; "
          "print(store.mode(), len(store.matches_all()))\"`", flush=True)


if __name__ == "__main__":
    main()
