"""
Adaptation brief — turn a matched competitor ad + its root into an NMC-compliant
creative brief for a Dr. Bimal's product (default: Arjuna Cardio Care Tea).

Output: data/rf/briefs/{ad_id}__{product}.md  (+ .json)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from compliance.validate import load_ruleset, validate_copy

from .analyze import load_catalogue
from .llm import gemini_json
from .paths import BRIEFS_DIR, MATCHES_DIR, slug

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def _find_match(ad_id: str) -> dict | None:
    for f in MATCHES_DIR.glob("*.json"):
        for m in json.loads(f.read_text(encoding="utf-8")).get("matches", []):
            if m.get("ad_id") == ad_id:
                return m
    return None


def _root_for(match: dict, cat: dict) -> dict:
    r = match.get("root") or {}
    rid = r.get("root_id")
    if rid:
        for root in cat["roots"]:
            if root["root_id"] == rid:
                return {**root, "_source": "named"}
    return {
        "root_id": rid or "candidate",
        "name": r.get("candidate_name") or "unnamed candidate",
        "mechanism": r.get("mechanism", ""),
        "visual_motif": r.get("visual_motif", ""),
        "compliance_notes": "",
        "_source": "candidate",
    }


_SYSTEM = (
    "You are a senior creative director + Indian health-advertising compliance lead for "
    "Dr. Bimal's (Jaadu Diet). You produce an adaptation brief that rebuilds a competitor "
    "ad's ROOT for our product while STRICTLY following the compliance ruleset given. "
    "Never let the product be the subject of a health claim. Ingredient-led claims only, "
    "and only the approved ones. Doctor appears only as 'Formulated by Dr. Bimal Chhajer "
    "MBBS MD'. No outcome timelines. Return ONLY JSON."
)


def _prompt(match: dict, root: dict, rules: dict, product: dict) -> str:
    approved = "\n".join(
        f"  - {ing} [{d['research'] or 'no citation'}]: " + "; ".join(d["claims"])
        for ing, d in rules["approved_ingredient_claims"].items()
    )
    return f"""OUR PRODUCT
{json.dumps(product, ensure_ascii=False, indent=1)}

ROOT TO USE: {root['name']}
  mechanism: {root['mechanism']}
  visual motif: {root['visual_motif']}
  compliance notes: {root.get('compliance_notes','')}

COMPETITOR AD WE ARE REFERENCING (image attached)
  brand: {match.get('page_name')}
  headline: {match.get('headline')}
  their compliance verdict for our rules: {match.get('compliance', {}).get('status')}

APPROVED INGREDIENT CLAIMS (the ONLY health claims allowed — use verbatim phrasing):
{approved}

HEADLINE TYPES: A broad-wellness (safest) · B consumer-fear question · C ingredient-claim (subject = ingredient + "helps" + citation) · D banned

Return JSON:
{{
  "root_used": "{root['name']}",
  "reference_note": "<1-2 lines: what to take from the competitor execution and what to drop>",
  "headlines": {{
    "A": ["<2 broad-wellness options, EN or Hindi to match competitor>"],
    "B": ["<1-2 consumer-fear question options>"],
    "C": ["<1 ingredient-claim option using an approved claim + short citation>"]
  }},
  "recommended_headline": "<pick the strongest compliant one>",
  "body_copy": "<2-3 sentences, ritual/heritage/price framing, no product health claim>",
  "benefit_bullets": ["<4 bullets — each is an approved ingredient claim, ingredient named first, with the citation in brackets>"],
  "cta": "<max 5 words>",
  "image_prompt": "<detailed prompt to rebuild the ROOT's visual motif for our product. Square 1:1. Sections: BACKGROUND / LAYOUT ZONES (proportions) / HEADLINE ZONE (the recommended headline) / INGREDIENT ELEMENTS (raw herbs from our key 6, labels = ingredient names only) / PRODUCT ZONE (our tea box/sachets — 'use the user-provided product image') / BENEFIT ZONE (the 4 bullets) / COLOR PALETTE (hex) / 'Formulated by Dr. Bimal Chhajer MBBS MD' line / DISCLAIMER if required / DO NOT INCLUDE (disease words, ECG/heartbeat graphics, timelines, doctor making a claim, before/after)>",
  "disclaimer_required": true|false,
  "disclaimer_text": "<the EN or Hindi disclaimer if required, else empty>",
  "designer_notes": "<2-3 practical notes on layout proportions and what makes this root work>"
}}"""


def generate(ad_id: str, product_key: str = "arjuna-tea") -> dict:
    match = _find_match(ad_id)
    if not match:
        raise SystemExit(f"ad_id {ad_id} not found in data/rf/matches/ — run analyze first")
    cat = load_catalogue()
    rules = load_ruleset()
    root = _root_for(match, cat)
    product = rules["product"]  # only Arjuna tea for now

    imgs = [match["image"]] if match.get("image") else []
    base_prompt = _prompt(match, root, rules, product)
    out = gemini_json(_SYSTEM, base_prompt, images=imgs, max_tokens=5000)

    def _check(o: dict) -> dict:
        txt = "\n".join(filter(None, [
            o.get("recommended_headline"), o.get("body_copy"),
            *(o.get("benefit_bullets") or []),
        ]))
        return validate_copy(txt, is_ours=True).to_dict()

    sc = _check(out)
    for _ in range(2):  # repair loop
        if sc["status"] == "PASS":
            break
        issues = "; ".join(f["message"] for f in sc["findings"])
        repair = base_prompt + (
            f"\n\nYOUR PREVIOUS ATTEMPT FAILED THE COMPLIANCE GATE: {issues}\n"
            f"Fix it. The product must NEVER be the subject of a health/wellness benefit "
            f"sentence — rewrite body_copy around ritual, heritage, taste, herbs and price only. "
            f"Keep disease words out unless an ingredient is the subject + 'helps'. "
            f"Return the full JSON again."
        )
        out = gemini_json(_SYSTEM, repair, images=imgs, max_tokens=5000)
        sc = _check(out)
    out["_self_check"] = sc

    # never trust the model for the disclaimer wording — use the ruleset verbatim
    if out.get("disclaimer_required"):
        hindi = any(ord(c) > 0x900 and ord(c) < 0x980 for c in (out.get("recommended_headline") or ""))
        out["disclaimer_text"] = rules["disclaimer"]["text_hi" if hindi else "text_en"]
    out["_meta"] = {
        "ad_id": ad_id,
        "product": product_key,
        "root_id": root["root_id"],
        "root_source": root["_source"],
        "competitor": match.get("page_name"),
        "reference_image": match.get("image"),
        "snapshot_url": match.get("snapshot_url"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(out, ad_id, product_key)
    return out


def _write(out: dict, ad_id: str, product_key: str) -> None:
    stem = f"{ad_id}__{slug(product_key)}"
    (BRIEFS_DIR / f"{stem}.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    m = out["_meta"]
    sc = out["_self_check"]
    hl = out.get("headlines", {})
    md = [
        f"# Adaptation Brief — {out.get('root_used','?')}",
        "",
        f"**Product:** {product_key}  ·  **Root:** `{m['root_id']}` ({m['root_source']})",
        f"**Referencing:** {m['competitor']} ad [{ad_id}]({m.get('snapshot_url','')})",
        f"**Self-check:** `{sc['status']}`" + (f" — {[f['message'] for f in sc['findings']]}" if sc['findings'] else ""),
        "",
        "## Reference",
        out.get("reference_note", ""),
        "",
        "## Headlines",
        "**A — broad wellness:** " + " / ".join(hl.get("A", [])),
        "**B — consumer-fear question:** " + " / ".join(hl.get("B", [])),
        "**C — ingredient claim:** " + " / ".join(hl.get("C", [])),
        "",
        f"**Recommended:** {out.get('recommended_headline','')}",
        "",
        "## Body copy",
        out.get("body_copy", ""),
        "",
        "## Benefit bullets",
        *[f"- {b}" for b in out.get("benefit_bullets", [])],
        "",
        f"## CTA\n{out.get('cta','')}",
        "",
        "## Image prompt",
        "```",
        out.get("image_prompt", ""),
        "```",
        "",
        f"## Disclaimer\n{'REQUIRED — ' + out.get('disclaimer_text','') if out.get('disclaimer_required') else 'Not required'}",
        "",
        "## Designer notes",
        out.get("designer_notes", ""),
    ]
    (BRIEFS_DIR / f"{stem}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"brief -> {BRIEFS_DIR / (stem + '.md')}  [self-check: {sc['status']}]")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("ad_id")
    ap.add_argument("--product", default="arjuna-tea")
    a = ap.parse_args()
    generate(a.ad_id, a.product)
