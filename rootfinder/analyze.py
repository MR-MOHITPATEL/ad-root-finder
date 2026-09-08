"""
Root-match + compliance for scraped competitor ads.

For each ad:
  1. vision model reads the image + copy -> is it a real root? match to a catalogue
     root, or propose a candidate root
  2. judge whether the root can work for our brand (Dr. Bimal's Arjuna tea)

No compliance check here — competitors don't follow our rules and it doesn't matter.
Compliance only gates what WE generate (rootfinder/brief.py).

Output: data/rf/matches/{slug}.json
Side effect: matched ads are appended to the root's competitor_examples in
             data/roots/catalogue.json (candidates are listed separately for
             the team to name/approve).
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .llm import gemini_json
from .paths import ADS_DIR, slug

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def load_catalogue() -> dict:
    return store.catalogue_load()


def save_catalogue(cat: dict) -> None:
    store.catalogue_save(cat)


def _pop_candidate(cat: dict, name: str) -> dict | None:
    for i, c in enumerate(cat.get("candidates", [])):
        if c.get("candidate_name") == name:
            return cat["candidates"].pop(i)
    return None


def promote_candidate(name: str, *, root_id: str, why_it_works: str = "",
                      compliance_notes: str = "", fits_our_brand: str | None = None) -> None:
    """Turn a discovered candidate into a named root in the catalogue."""
    cat = load_catalogue()
    c = _pop_candidate(cat, name)
    if not c:
        raise ValueError(f"candidate not found: {name}")
    cat["roots"].append({
        "root_id": root_id,
        "name": name,
        "status": "named",
        "mechanism": c.get("mechanism", ""),
        "why_it_works": why_it_works,
        "visual_motif": c.get("visual_motif", ""),
        "fits_our_brand": fits_our_brand or c.get("fits_our_brand") or "conditional",
        "compliance_notes": compliance_notes,
        "competitor_examples": c.get("examples", []),
        "our_executions": [],
    })
    save_catalogue(cat)


def merge_candidate_into_root(name: str, root_id: str) -> None:
    """Fold a candidate's examples into an existing named root and drop the candidate."""
    cat = load_catalogue()
    root = next((r for r in cat["roots"] if r["root_id"] == root_id), None)
    if not root:
        raise ValueError(f"root not found: {root_id}")
    c = _pop_candidate(cat, name)
    if not c:
        raise ValueError(f"candidate not found: {name}")
    ex = root.setdefault("competitor_examples", [])
    for e in c.get("examples", []):
        if not any(x.get("ad_id") == e.get("ad_id") for x in ex):
            ex.append(e)
    save_catalogue(cat)


_SYSTEM = (
    "You are a senior performance-creative strategist for Indian health & wellness brands. "
    "You reverse-engineer the AD ROOT of a competitor ad: the reusable creative concept = "
    "persuasion mechanism + visual motif (NOT the product, condition or copy wording). "
    "Two ads share a root if a strategist would rebuild them from the same recipe. "
    "Return ONLY JSON."
)


def _prompt(ad: dict, roots: list[dict], candidates: list[dict] | None = None) -> str:
    known = "\n".join(
        f'  - id="{r["root_id"]}" | {r["name"]} | mechanism: {r["mechanism"]} | motif: {r["visual_motif"]}'
        for r in roots
    )
    cand_names = "\n".join(
        f'  - "{c["candidate_name"]}" | {(c.get("visual_motif") or "")[:90]}'
        for c in (candidates or [])
    )
    if cand_names:
        known += ("\n\nEXISTING CANDIDATE ROOTS (reuse the EXACT name if this ad fits one — "
                  "do not invent a near-duplicate name):\n" + cand_names)
    copy = "\n".join(x for x in [ad.get("headline"), ad.get("body"), ad.get("link_description")] if x)
    return f"""KNOWN ROOTS (match to one of these ids if the recipe is the same):
{known}

COMPETITOR AD
  brand: {ad.get('page_name')}
  headline: {ad.get('headline')}
  body: {(ad.get('body') or '')[:500]}
  cta: {ad.get('cta_type')}
  (image is attached)

First decide if this is even a ROOT worth cataloguing:
  - designed_creative = a deliberately built ad (headline/copy baked into the image, a
    persuasion structure, infographic / comparison / lifestyle / testimonial layout).
  - NOT a root = plain product-on-white pack shot, flavour/variant showcase, bare catalog
    image, price-only banner, logo card. Mark these designed_creative=false.

Return JSON:
{{
  "designed_creative": true|false,
  "creative_type": "infographic"|"comparison"|"lifestyle"|"testimonial"|"ugc"|"ingredient-showcase"|"product-shot"|"catalog"|"minimal"|"text-post",
  "root_strength": 0.0-1.0,   // how much this is a reusable, transplantable concept vs a one-off photo
  "noise_reason": "<if root_strength < 0.4, one line why this isn't a root>",
  "root_id": "<known id if it matches, else null>",
  "match_confidence": 0.0-1.0,
  "is_candidate": <true if designed_creative AND no known root fits>,
  "candidate_name": "<short name for the new root, or null>",
  "mechanism": "<one line: how this ad persuades>",
  "visual_motif": "<one line: the reusable visual recipe>",
  "fits_our_brand": "yes"|"no"|"conditional",
  "fit_reason": "<why it does / doesn't work for Dr. Bimal's Arjuna Cardio Care Tea (a 14-herb ayurvedic tea, NOT research-backed at product level, must follow NMC/FSSAI: ingredient-led claims only, no disease promises, no outcome timelines, doctor only as 'Formulated by')>",
  "adaptation_hint": "<one line: what to change to run this root compliantly for our tea>"
}}"""


def analyze_ad(ad: dict, roots: list[dict], candidates: list[dict] | None = None) -> dict:
    imgs = (ad.get("local_image_paths") or [])[:1] or (ad.get("image_urls") or [])[:1]
    rec: dict = {
        "ad_id": ad["ad_id"],
        "page_name": ad.get("page_name"),
        "headline": ad.get("headline"),
        "snapshot_url": ad.get("snapshot_url"),
        "image": ad.get("image_url") or (imgs[0] if imgs else None),
        "image_phash": ad.get("image_phash"),
        "start_time": ad.get("start_time"),
        "is_active": ad.get("is_active"),
    }
    try:
        rec["root"] = gemini_json(_SYSTEM, _prompt(ad, roots, candidates), images=imgs)
    except Exception as e:  # noqa: BLE001
        rec["root"] = {"error": str(e)}
    r = rec["root"]
    rec["is_noise"] = bool(
        r.get("error")
        or not r.get("designed_creative", True)
        or (r.get("root_strength") or 0) < 0.4
    )
    # NOTE: no compliance check here — competitors don't follow our rules and it
    # doesn't matter. Compliance only gates what WE generate (see brief.py).
    return rec


def _attach_to_catalogue(cat: dict, matches: list[dict], competitor: str) -> None:
    by_id = {r["root_id"]: r for r in cat["roots"]}
    candidates = cat.setdefault("candidates", [])
    today = datetime.now(timezone.utc).date().isoformat()
    for m in matches:
        r = m.get("root") or {}
        if r.get("error") or m.get("is_noise"):
            continue
        rid = r.get("root_id")
        entry = {
            "brand": m.get("page_name") or competitor,
            "ad_id": m["ad_id"],
            "image_url": m.get("image"),
            "snapshot_url": m.get("snapshot_url"),
            "note": (m.get("headline") or "")[:120],
            "first_seen": today,
        }
        if rid and rid in by_id and (r.get("match_confidence") or 0) >= 0.6:
            ex = by_id[rid].setdefault("competitor_examples", [])
            if not any(e.get("ad_id") == m["ad_id"] for e in ex):
                ex.append(entry)
        elif r.get("is_candidate"):
            name = r.get("candidate_name") or "unnamed"
            cand = next((c for c in candidates if c.get("candidate_name") == name), None)
            if not cand:
                cand = {
                    "candidate_name": name,
                    "mechanism": r.get("mechanism"),
                    "visual_motif": r.get("visual_motif"),
                    "fits_our_brand": r.get("fits_our_brand"),
                    "examples": [],
                }
                candidates.append(cand)
            if not any(e.get("ad_id") == m["ad_id"] for e in cand["examples"]):
                cand["examples"].append(entry)


def run(terms: list[str] | None = None, *, force: bool = False, workers: int = 4,
        limit: int | None = None) -> None:
    cat = load_catalogue()
    roots = cat["roots"]
    files = (
        [ADS_DIR / f"{slug(t)}.json" for t in terms]
        if terms else sorted(ADS_DIR.glob("*.json"))
    )
    for f in files:
        if not f.exists():
            print(f"skip (no scrape): {f.name}")
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        competitor = data.get("term", f.stem)
        ads = data.get("ads", [])

        seen_ids = set() if force else store.matches_existing_ids(competitor)
        todo = [a for a in ads if a["ad_id"] not in seen_ids]
        if limit:
            todo = todo[:limit]

        print(f">> {competitor}: {len(todo)} new / {len(ads)} ads")
        results = []
        if todo:
            cands_hint = cat.get("candidates", [])
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(analyze_ad, a, roots, cands_hint): a for a in todo}
                for i, fut in enumerate(as_completed(futs), 1):
                    m = fut.result()
                    results.append(m)
                    r = m.get("root") or {}
                    if m.get("is_noise"):
                        tag = f"noise ({r.get('creative_type','?')})"
                    else:
                        tag = r.get("root_id") or (f"candidate:{r.get('candidate_name')}" if r.get("is_candidate") else "?")
                    print(f"   [{i}/{len(todo)}] {m['ad_id']}  strength={r.get('root_strength')}  {tag}  fits={r.get('fits_our_brand')}")

        if results:
            _attach_to_catalogue(cat, results, competitor)
            store.matches_save(competitor, results)

    save_catalogue(cat)
    n_cand = len(cat.get("candidates", []))
    print(f"catalogue ({store.mode()}): {len(cat['roots'])} named roots, {n_cand} candidates")
    print("Review candidates in the UI and Promote / Merge them by hand.")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("terms", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    run(a.terms or None, force=a.force, limit=a.limit)
