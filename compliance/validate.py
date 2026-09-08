"""
Two-layer compliance checker for Dr. Bimal's Arjuna Cardio Care Tea ad copy.

Layer 1 — deterministic keyword / regex (fast, offline).
Layer 2 — one LLM call for the judgment calls (subject detection, headline type,
          doctor role) and a compliant rewrite.

Used for:
  - our generated adaptation briefs  -> hard gate
  - competitor ads                   -> "how usable is this for us as-is"

Rules: compliance/ruleset.yaml  (REBUILD_SPEC.md §5)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

RULESET_PATH = Path(__file__).with_name("ruleset.yaml")

PASS, NEEDS_EDIT, FAIL = "PASS", "NEEDS_EDIT", "FAIL"
_ORDER = {PASS: 0, NEEDS_EDIT: 1, FAIL: 2}


@dataclass
class Finding:
    rule: str
    severity: str          # NEEDS_EDIT | FAIL
    message: str
    evidence: str = ""


@dataclass
class Verdict:
    status: str = PASS
    findings: list[Finding] = field(default_factory=list)
    rewrite: str | None = None
    headline_type: str | None = None
    notes: str = ""

    def add(self, f: Finding) -> None:
        self.findings.append(f)
        if _ORDER[f.severity] > _ORDER[self.status]:
            self.status = f.severity

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "headline_type": self.headline_type,
            "findings": [f.__dict__ for f in self.findings],
            "rewrite": self.rewrite,
            "notes": self.notes,
        }


@lru_cache(maxsize=1)
def load_ruleset() -> dict:
    return yaml.safe_load(RULESET_PATH.read_text(encoding="utf-8"))


# ── Layer 1 ──────────────────────────────────────────────────────────────────

def _layer1(text: str, v: Verdict) -> None:
    r = load_ruleset()
    low = text.lower()
    lines = [ln.strip() for ln in re.split(r"[\n\.\!\?]", text) if ln.strip()]

    for verb in r["verbs"]["banned"]:
        if re.search(rf"\b{re.escape(verb)}\b", low):
            v.add(Finding("verbs.banned", FAIL,
                          f"Banned claim word: '{verb}'", verb))
    for verb in r["verbs"].get("banned_hindi", []):
        if verb in text:
            v.add(Finding("verbs.banned_hindi", FAIL,
                          f"Banned claim word (Hindi): '{verb}'", verb))

    for pat in r.get("timeline_patterns", []):
        m = re.search(pat, low)
        if m:
            v.add(Finding("timeline", FAIL,
                          "Outcome-timeline claim — not allowed (no product-level study)",
                          m.group(0)))

    doc = r["doctor"]
    name_res = [n.lower() for n in doc["names"]]
    allowed = [ap.lower() for ap in doc["allowed_patterns"]]
    for ln in text.split("\n"):
        ll = ln.lower().strip()
        if not any(n in ll for n in name_res):
            continue
        if any(b.lower() in ll for b in doc["banned_near_name"]):
            v.add(Finding("doctor.endorsement", FAIL,
                          "Doctor name appears with a recommendation/endorsement — "
                          "only 'Formulated by' is allowed", ln.strip()))
        elif not any(ap in ll or ap in low for ap in allowed):
            v.add(Finding("doctor.unverified", NEEDS_EDIT,
                          "Doctor name present but not in the exact 'Formulated by' form — "
                          "check it makes no claim", ln.strip()))

    dt = {**r.get("disease_terms", {}), **r.get("disease_terms_hindi", {})}
    hit = [term for term in dt if term.lower() in low or term in text]
    if hit:
        v.add(Finding("disease_terms", NEEDS_EDIT,
                      "Disease/biomarker term present — allowed only if an INGREDIENT is the "
                      "subject + 'helps' (Rule 6C); otherwise swap for a safe term "
                      f"({', '.join(f'{k}->{dt[k]}' for k in hit)})",
                      ", ".join(hit)))


# ── Layer 2 ──────────────────────────────────────────────────────────────────

_L2_SYSTEM = (
    "You are a compliance analyst for Indian health-product advertising "
    "(NMC 2023, FSSAI Health Claims 2022, ASCI, DMR Act 1954). "
    "You judge ad copy for Dr. Bimal's Arjuna Cardio Care Tea — a herbal tea, "
    "NOT research-backed at the product level. Return ONLY JSON."
)


def _l2_prompt(text: str, r: dict) -> str:
    claims = "\n".join(
        f"  - {ing} ({d['research'] or 'no citation'}): " + "; ".join(d["claims"])
        for ing, d in r["approved_ingredient_claims"].items()
    )
    return f"""AD COPY (headline + body + visible text):
\"\"\"{text}\"\"\"

Approved ingredient claims (the ONLY health claims allowed for us):
{claims}

Answer as JSON:
{{
  "product_is_claim_subject": true|false,   // is the PRODUCT (tea/brand) the subject of a health-benefit sentence? (Rule 2 -> FAIL if true)
  "ingredient_is_claim_subject": true|false, // is an INGREDIENT the subject of a 'helps/supports' claim? (Rule 3 -> ok)
  "headline_type": "A"|"B"|"C"|"D",         // A broad wellness, B consumer-fear question, C ingredient-claim, D banned
  "doctor_role": "none"|"credential"|"endorsement", // endorsement -> FAIL
  "disease_name_as_product_promise": true|false,    // disease word used as the product's promise (Rule 5 -> FAIL)
  "claims_lack_research_backing": true|false,       // any health claim not on the approved list above
  "unverified_social_proof": true|false,            // 'clinically proven', fake-looking testimonials, before/after
  "issues": ["short phrase", ...],
  "compliant_rewrite": "A fully compliant version of this copy for our product: broad-wellness or consumer-fear headline (Type A/B) OR ingredient-led claim (Type C) with the citation; ingredient-attributed bullets only; 'Formulated by Dr. Bimal Chhajer MBBS MD' if the doctor appears; keep price/ritual/heritage language. Keep the original language (English/Hindi)."
}}"""


def _layer2(text: str, v: Verdict, *, use_llm: bool) -> None:
    if not use_llm or not text.strip():
        return
    r = load_ruleset()
    try:
        from rootfinder.llm import groq_json
        ans = groq_json(_L2_SYSTEM, _l2_prompt(text, r))
    except Exception as e:  # noqa: BLE001
        v.notes += f" [layer2 skipped: {e}]"
        return

    v.headline_type = ans.get("headline_type")
    v.rewrite = ans.get("compliant_rewrite") or v.rewrite

    # Reconcile the context-dependent Layer-1 flags with Layer-2 judgment.
    # Rule 6C: a disease term IS allowed when an ingredient is the subject + "helps"
    # and the product is not.
    if ans.get("ingredient_is_claim_subject") and not ans.get("product_is_claim_subject"):
        v.findings = [f for f in v.findings if f.rule != "disease_terms"]
        v.status = max((f.severity for f in v.findings), key=lambda s: _ORDER[s], default=PASS)

    if ans.get("product_is_claim_subject"):
        v.add(Finding("rule2.product_subject", FAIL,
                      "The product is the subject of a health-benefit claim — "
                      "must be ingredient-led", "; ".join(ans.get("issues", []))))
    if ans.get("doctor_role") == "endorsement":
        v.add(Finding("rule1.doctor", FAIL, "Doctor endorses/recommends the product"))
    if ans.get("disease_name_as_product_promise"):
        v.add(Finding("rule5.disease_promise", FAIL,
                      "Disease name used as the product's promise"))
    if ans.get("headline_type") == "D":
        v.add(Finding("rule6.headline_D", FAIL,
                      "Headline is a banned type (disease-as-problem / product-claim / doctor-claim)"))
    if ans.get("claims_lack_research_backing"):
        v.add(Finding("rule4.unbacked", NEEDS_EDIT,
                      "A health claim is not on the approved ingredient list"))
    if ans.get("unverified_social_proof"):
        v.add(Finding("rule9.social_proof", FAIL,
                      "Unverified social proof (clinically-proven / testimonial / before-after)"))


# ── Public ───────────────────────────────────────────────────────────────────

def validate_copy(text: str, *, is_ours: bool = True, use_llm: bool = True) -> Verdict:
    """
    is_ours=True  -> hard gate for our own copy.
    is_ours=False -> same checks, read as 'how usable is this competitor ad for us as-is'.
    """
    v = Verdict()
    text = (text or "").strip()
    if not text:
        v.notes = "no text to check"
        return v
    _layer1(text, v)
    _layer2(text, v, use_llm=use_llm)
    if not is_ours and v.status == FAIL:
        v.notes = ("Competitor copy — not usable verbatim; adapt to ingredient-led "
                   "per the rewrite.") + v.notes
    return v


if __name__ == "__main__":
    import json
    import sys

    src = sys.stdin.read() if not sys.argv[1:] else " ".join(sys.argv[1:])
    print(json.dumps(validate_copy(src).to_dict(), indent=2, ensure_ascii=False))
