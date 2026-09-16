"""
Version generator — turn one catalogued root into 10+ producible executions
for Dr. Bimal's Arjuna Cardio Care Tea, spread across image / audio / video,
each self-checked against the NMC/FSSAI ruleset (compliance/validate.py).

Works on ANY root, including ones sourced from a competitor ad — by design
the output is always written as our own executable idea for our product,
never a reskin of the competitor's actual copy or ingredients.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from . import store
from .llm import gemini_json

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_SYSTEM = (
    "You are a senior creative director for Dr. Bimal's Arjuna Cardio Care Tea "
    "(a 14-herb Ayurvedic tea, Rs 599 for 50 bags, formulated by Dr. Bimal Chhajer MBBS MD). "
    "Given one ad ROOT (a persuasion mechanism + story), you write a slate of DIFFERENT "
    "executable versions of that root for OUR product -- varying the angle, hook, and "
    "medium, never repeating one idea with a synonym swap. Every version must follow "
    "NMC/FSSAI rules: ingredient-led claims only, no disease/cure claims, no outcome "
    "timelines, the doctor referenced only as 'Formulated by'. Return ONLY JSON."
)


def _prompt(root: dict, counts: dict[str, int]) -> str:
    total = sum(counts.values())
    return f"""ROOT
  name: {root.get('name')}
  mechanism: {root.get('mechanism')}
  visual motif: {root.get('visual_motif')}
  story (product speaking): {root.get('story') or '(not captured for this root -- infer one from the mechanism/motif)'}
  line of attack: {root.get('line_of_attack_type') or '?'} — {root.get('line_of_attack') or ''}
  reason to believe: {root.get('reason_to_believe') or ''}
  attributes — verbal: {root.get('attributes_verbal') or ''}; visual: {root.get('attributes_visual') or ''}

Write {total} DIFFERENT executable versions of this root for our tea:
  - {counts['image']} IMAGE versions
  - {counts['audio']} AUDIO versions (a person speaking -- reel/ad voiceover)
  - {counts['video']} VIDEO versions (short-form, scene by scene)

Vary the angle/hook across versions of the same medium. Every version speaks about OUR
14-herb Ayurvedic tea, not the competitor's product or ingredient.

Separately, ESTIMATE capacity: for each medium, how many genuinely DIFFERENT versions
(different angle/hook/layout — not synonym swaps) could this specific root actually support
before you'd be repeating yourself? A visual concept usually supports more distinct cuts
(different herb headlining, different layout, different color/mood) than a video does
(fewer meaningfully different scene structures before repetition). Give a real number per
medium for THIS root, not a generic default.

Return JSON: {{
  "version_capacity": {{
    "image": {{"count": <int>, "why": "<one line>"}},
    "audio": {{"count": <int>, "why": "<one line>"}},
    "video": {{"count": <int>, "why": "<one line>"}}
  }},
  "versions": [
  {{
    "medium": "image"|"audio"|"video",
    "version_name": "<short label, e.g. 'Morning ritual angle'>",
    "headline": "<headline for this version>",
    "hook": "<one line: the specific angle/twist for this version>",
    "image_prompt": "<only if medium=image: a full image-generation prompt>",
    "key_visual_elements": "<only if medium=image: one line>",
    "script": "<only if medium=audio: the full VO script>",
    "tone": "<only if medium=audio: one line>",
    "scenes": [{{"visual": "...", "vo_line": "...", "on_screen_text": "..."}}],
    "duration_sec": 15,
    "disclaimer": "<required disclaimer line, e.g. 'Formulated by Dr. Bimal Chhajer. Individual results may vary.'>"
  }}
]}}
("scenes" and "duration_sec" only for medium=video; "duration_sec" also for medium=audio.)"""


def _version_text(v: dict) -> str:
    parts = [v.get("headline") or "", v.get("hook") or "", v.get("script") or ""]
    for sc in v.get("scenes") or []:
        parts += [sc.get("vo_line") or "", sc.get("on_screen_text") or ""]
    return "\n".join(p for p in parts if p)


def _build_versions(root_like: dict, counts: dict[str, int]) -> tuple[list[dict], dict]:
    """Shared generation core: root_like just needs mechanism/visual_motif/story/etc
    fields -- works the same whether it's a catalogued root or one ad's own analysis
    (rootfinder.analyze's Gemini output uses the identical field names)."""
    from compliance.validate import validate_copy  # local import: optional dep, keep analyze.py light

    out = gemini_json(_SYSTEM, _prompt(root_like, counts), max_tokens=6000)
    versions = out.get("versions") or []
    if not versions and out.get("error"):
        raise RuntimeError(out["error"])

    now = datetime.now(timezone.utc).isoformat()
    for v in versions:
        verdict = validate_copy(_version_text(v), is_ours=True, use_llm=True)
        v["compliance"] = verdict.to_dict()
        v["generated_at"] = now
    return versions, (out.get("version_capacity") or {})


def generate(root_id: str, *, n_image: int = 1, n_audio: int = 1, n_video: int = 1) -> list[dict]:
    """Generate the version slate for one named root and persist it on that root."""
    cat = store.catalogue_load()
    root = next((r for r in cat["roots"] if r["root_id"] == root_id), None)
    if not root:
        raise ValueError(f"root not found: {root_id}")

    counts = {"image": n_image, "audio": n_audio, "video": n_video}
    versions, capacity = _build_versions(root, counts)
    root["versions"] = versions
    root["version_capacity"] = capacity
    store.catalogue_save(cat)
    return versions


def _pseudo_root(m: dict) -> dict:
    """Build a root_like dict from one analyzed ad's own storyboard fields."""
    r = m.get("root") or {}
    name = m.get("headline") or r.get("root_id") or r.get("candidate_name") or m.get("ad_id")
    return {"name": name, **{k: r.get(k) for k in (
        "mechanism", "visual_motif", "story", "line_of_attack_type", "line_of_attack",
        "reason_to_believe", "attributes_verbal", "attributes_visual")}}


def generate_for_ad(m: dict, *, n_image: int = 1, n_audio: int = 1, n_video: int = 1) -> list[dict]:
    """Generate a version slate straight from one ad's own analysis (no catalogued
    root needed) -- always written as our own pitch for our tea, never a reskin of
    that ad's actual competitor copy."""
    counts = {"image": n_image, "audio": n_audio, "video": n_video}
    versions, _capacity = _build_versions(_pseudo_root(m), counts)
    return versions


def run_for_all_ads(*, force: bool = False, workers: int = 4, limit: int | None = None) -> None:
    """Generate versions for every analyzed, non-noise ad in the matches table."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    matches = store.matches_all()
    todo = [m for m in matches if not m.get("is_noise") and (force or not m.get("versions"))]
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} ads to version (of {len(matches)} total, {len(matches) - len(todo)} skipped)")

    def _do(m: dict):
        try:
            m["versions"] = generate_for_ad(m)
            return m, None
        except Exception as e:  # noqa: BLE001
            return m, str(e)

    by_competitor: dict[str, list[dict]] = {}
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_do, m): m for m in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            m, err = fut.result()
            if err:
                fail += 1
                print(f"  [{i}/{len(todo)}] {m['ad_id']} FAILED: {err}")
            else:
                ok += 1
                by_competitor.setdefault(m["_competitor_file"], []).append(m)
                statuses = [v["compliance"]["status"] for v in m["versions"]]
                print(f"  [{i}/{len(todo)}] {m['ad_id']} ({m.get('_competitor_file')}) -> {statuses}")

    for competitor, ms in by_competitor.items():
        store.matches_save(competitor, ms)
    print(f"done: {ok} ok, {fail} failed")


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("root_id", nargs="?")
    ap.add_argument("--image", type=int, default=1)
    ap.add_argument("--audio", type=int, default=1)
    ap.add_argument("--video", type=int, default=1)
    ap.add_argument("--all-ads", action="store_true", help="generate per-ad, for every analyzed ad")
    ap.add_argument("--force", action="store_true", help="with --all-ads: redo ads that already have versions")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if a.all_ads:
        run_for_all_ads(force=a.force, workers=a.workers, limit=a.limit)
    elif a.root_id:
        vs = generate(a.root_id, n_image=a.image, n_audio=a.audio, n_video=a.video)
        print(json.dumps(vs, indent=2, ensure_ascii=False))
        print(f"\n{len(vs)} versions generated for root '{a.root_id}'")
    else:
        ap.error("give a root_id or --all-ads")
