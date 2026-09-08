# Rebuild Spec — Competitor Ad Root Finder

Status: **Phase 1 built — local testing** · Owner: ZenJeevani (Dr. Bimal's / Jaadu Diet)
Last updated: 2026-09-07

### Phase 1 — what's built (run locally, no hosting yet)
```
config/competitors.yaml        competitor list (edit freely)
compliance/ruleset.yaml        §5 as data
compliance/validate.py         2-layer checker: Layer1 regex + Layer2 (Groq gpt-oss-120b)
data/roots/catalogue.json      3 seed roots + auto-collected candidates
rootfinder/fetch.py            fast Playwright scrape (OZiva: 40 ads / 35s, anonymous)
rootfinder/analyze.py          Gemini vision -> is-it-a-root + root match/candidate + fits-our-brand
rootfinder/decisions.py        human works/maybe/no decisions per ad (data/rf/decisions.json)
rootfinder/brief.py            adaptation brief + compliance self-check repair loop
run.py                         scan | fetch | analyze | brief | dashboard
dashboard/rf_app.py            Streamlit: Review queue · Approved · Catalogue · Candidates · Briefs
```
Models: **Gemini 2.5 Flash** (vision), **Groq `openai/gpt-oss-120b`** (text —
old `llama-3.3-70b` decommissioned). Data under `data/rf/`.

**Compliance runs on OUR output only** — never on competitor ads (they don't follow
our rules and it's irrelevant). It gates the brief self-check and, later, image gen.

**Flow:** scrape (hero image only, catalog ads dropped) -> vision classifies real-root
vs noise + matches/proposes -> **human Review queue: ✅ works / 🤔 maybe / ❌ no** ->
approved ads -> adaptation brief (compliance-gated).

---

## 1. Goal

Automate the manual process the team uses to find **ad roots** — reusable creative
concepts (layout + persuasion mechanism + visual motif) — from competitor ads, and
turn the ones that fit into **NMC-compliant adaptation briefs** for Dr. Bimal's products.

Two things we want to see:
- what ad roots **competitors** are running (Kapiva, OZiva, …)
- what ad roots **we** are running (Dr. Bimal's / Jaadu Diet)

Priority: **retrieve the data fast**, analyze only where it adds value, then produce the brief.

### End deliverable (agreed)
Output **(a)**: a shortlist of competitor **active image ads** →
matched to a root → with a compliance verdict → plus an **adaptation brief**
for a chosen Dr. Bimal's product.

Not (yet) in scope: generating the final image. Brief only — for the designer.
(Revisit later.)

---

## 2. The manual workflow we are automating

1. Open **Meta Ads Library** → home page → filter **All ads**.
2. Search competitors: **Kapiva** (+ its sub-brands), **OZiva**, a few others.
3. Apply filters: **Media type = Images & Memes**, **Active ads only**.
4. Go through each active image ad. Ask: *does this map to one of our reference roots?*
   The competitor image can look nothing like our example — what matters is the
   underlying root.
5. If it fits a root, ask: *can this work for our brand / our product?*
6. **Compliance gate (NMC + FSSAI + ASCI + DMR Act):**
   - Competitors are research-backed and can (attempt to) say "results in 6 weeks".
     We are **not** research-backed at the product level — our version must strictly
     follow the framework in §5.
   - If the *competitor's* ad breaks the rules, that's fine — but *ours* cannot.
7. If it fits and is compliant-adaptable, the root **gets a name** and goes in the
   catalogue, with our own execution recorded next to it.

### Seed roots (from the 3 competitor→our pairs the team sent)

| Root (working name) | Mechanism | Visual motif | Competitor example | Our execution |
|---|---|---|---|---|
| **Ingredient Bubbles Around Product** | "look how much is inside" / ingredient transparency | product centered, glowing spheres ringing it, one ingredient per sphere | OZiva Sugar Support | Dr. Bimal's Arjuna Cardio Care Tea |
| **Botanical Habitat / Forest Floor** | naturalness / provenance | product sitting in nature, ingredient names floating around it | OZiva HerBalance (PCOS) | Dr. Bimal's Arjuna tea in forest |
| **Pile vs One (VS comparison)** | simplification / loss-aversion | hand full of many pills/bottles vs one hand holding our single product | Kapiva "Why take 20 pills…" | Dr. Bimal's "15 अलग-अलग चीज़ें क्यों लें…" |

Extra competitor executions seen: OZiva Artho Sure juice, Kapiva Shilajit gummies
"One Gummy Four Benefits".

---

## 3. Data source decision

### Meta Ad Library API — DEAD END for our use case
- Token supplied (System User, app `CampAutomation`, scopes `ads_read` / `ads_management`
  / `business_management`) is **valid** but `ads_archive` returns
  `OAuthException code 10 / subcode 2332002` on every call — commercial, political,
  EU, non-EU alike. This is the **identity-confirmation onboarding gate**
  (facebook.com/ads/library/api → "Confirm your identity" → govt ID + country;
  takes hours–days).
- **Even after onboarding it will not help.** Meta's `ads_archive` docs:
  > "Ads that did not reach any location in the EU will only return if they are
  > about social issues, elections or politics."
  Outside the EU the API returns only political/issue, employment, housing and
  financial-services ads. Kapiva / OZiva / Dr. Bimal's product ads are **not in the API**.
- Keep the token anyway — useful later for reading **our own** ad-account performance
  (spend, results) via the Marketing API. Rotate it (it was pasted in chat).

### Decision: browser automation against the Ad Library **website**
The current scraper's approach (Playwright + network interception + SSR + DOM
fallback) is right; it's just **slow and fragile**. Rebuild it fast and resilient.

---

## 4. Competitor list

(Kapiva and the Mosaic brands are **not** related — earlier draft was wrong.)

| Brand | Sells | Why it matters for roots | Ad Library search |
|---|---|---|---|
| **Kapiva** | Ayurvedic juices, teas, gummies, powders (Baidyanath-linked DTC) | Closest match — herbal, tea/juice format, same claim style, heavy infographic + VS ads | `Kapiva`, `Kapiva Ayurveda` |
| **OZiva** | Plant-based nutrition (HerBalance, protein, biotin); HealthKart-owned | Source of 2 seed roots — botanical-habitat, ingredient-bubble | `OZiva` |
| **Wellbeing Nutrition** | Melts, strips, effervescents | New visual formats appear here first | `Wellbeing Nutrition` |
| **Plix** | Plant-based effervescents & gummies | Bold VS-comparison + benefit-callout roots | `Plix`, `Plix Life` |
| **Setu Nutrition** | Supplements (Marico) | Clean infographic roots | `Setu` |
| **&Me** | Women's wellness drinks | Ingredient-forward roots | `&Me` |
| **Man Matters / Be Bodywise** | Men's & women's health DTC (Mosaic Wellness) | Pile-vs-one, problem-agitation, doctor-note roots | `Man Matters`, `Bodywise` |
| **Traya** | Hair (separate company) | Long-copy / authority-note roots | `Traya` |
| **Heritage** — Dabur, Zandu (Emami), Baidyanath, Himalaya, Dr. Vaidya's, Nirogam | Ayurvedic OTC | Traditional-authority roots, festival creatives | brand names |
| **Heart-space** — Saffola Life (Marico), "Cholesterol Relief Community", Arjuna-tea sellers | Cardio / cholesterol positioning | Our product's exact lane — closest compliant claim phrasing to study | `Saffola`, `Arjuna tea`, `cardio tea` |

**Fallback broader search** — non-brand keywords, run only when the tracked list yields
nothing new: `arjuna tea`, `cardio tea`, `cholesterol ayurvedic`,
`heart wellness herbal`, `ashwagandha tea`, `heart care tea`.

**Still to confirm with team:**
- exact Kapiva page name(s) seen in the Ad Library ("2–3 brands of Kapiva")
- which rows are **every-run** vs **broader-search-only**

Competitors are stored in `config/competitors.yaml`, editable without code changes.

### Our own ads
Do **not** scrape "Dr. Bimal's" / "Jaadu Diet". Our executions are added to the root
catalogue **manually** (upload image + pick the root in the dashboard).

---

## 5. Compliance framework (AUTHORITATIVE — team-supplied, do not soften)

For: **Dr. Bimal's Arjuna Cardio Care Tea (Jaadu Diet)**.
Governing: NMC Code of Ethics 2023 · FSSAI Health Claims Regulations 2022 ·
ASCI Guidelines · Drugs & Magic Remedies (Objectionable Advertisements) Act 1954 ·
Consumer Protection Act 2019 · Food Safety and Standards Act 2006.

### Rule 1 — Doctor credential (NMC)
A registered medical practitioner cannot endorse / recommend / promote a health
product commercially.
- ✅ `Formulated by Dr. Bimal Chhajer MBBS MD` (or `Formulated By Dr. Bimal Chhajer (MBBS, MD)`)
- ❌ "Recommended by Dr. Bimal Chhajer"
- ❌ "Dr. Bimal Chhajer says this helps your heart"
- ❌ Dr. Bimal making **any** health claim in any ad; any doctor voice/quote claiming benefits
- **Code rule:** if the doctor's name appears, it may appear **only** as a formulation
  credential. No quote, no recommendation, no claim.

### Rule 2 — No direct product benefit claims (FSSAI + DMR Act)
A food/health product cannot claim to treat / cure / prevent / diagnose any disease.
- ❌ "This tea reduces cholesterol" · "Arjuna Tea cures heart disease" ·
  "Drink this to prevent heart attack" · "Treats high BP" · any benefit attributed to **the product**
- ✅ Ingredient-led claims only (Rule 3)
- **Code rule:** the product name ("Arjuna Cardio Care Tea" / "Jaadu Diet" / "Dr. Bimal's")
  must **never** be the subject of a health-benefit sentence.

### Rule 3 — Ingredient-led claims (FSSAI Health Claims 2022)
Structure: `INGREDIENT NAME + ALLOWED VERB + FUNCTION`, backed by published
peer-reviewed evidence.
- ✅ verbs: supports, helps, aids, contributes to, helps maintain, **helps reduce**,
  in support of, for healthy [function]
- ❌ verbs: treats, cures, prevents, reverses, heals, fights, eliminates,
  controls (as a cure claim)
- Example: "Arjuna Chhal helps reduce bad cholesterol"

### Rule 4 — Approved ingredient claims (the ONLY ones allowed)

| Ingredient | Approved claims | Research |
|---|---|---|
| **Arjuna Chhal** (Terminalia arjuna) | helps reduce bad cholesterol · supports healthy lipid levels · supports heart muscle function · supports cardiovascular antioxidant activity · supports healthy blood vessel function | Gupta et al. 2001, JAPI — PMID 11225136 — RCT, 105 pts, LDL −15.8% (p<0.01) |
| **Green Tea** | supports healthy blood vessel function · supports cardiovascular antioxidant activity | PMC9231383 |
| **Ashwagandha** | supports healthy stress response | PMC3573577 |
| **Tulsi** (Holy Basil) | supports daily stress adaptation | PMC4296439 |
| **Dry Ginger** (Sonth) | supports healthy blood circulation | PMC3848205 |
| **Cinnamon** (Dalchini) | supports healthy blood glucose regulation | multiple peer-reviewed |
| **Clove** (Lavang) | supports overall immune function | PMC10888574 |
| **Brahmi** | supports healthy cognitive function | — |
| **Cardamom** (Elaichi) | supports digestive comfort | — |
| **Black Tea** | supports cardiovascular antioxidant activity | — |

### Rule 5 — Banned words / phrases
- Disease names as a product claim in a headline: Cholesterol, High BP, Heart Disease,
  Diabetes, Heart Attack → use instead: "lipid levels", "blood pressure wellness",
  "cardiovascular wellness", "heart wellness", "blood glucose regulation"
- Product-level benefit verbs: "This tea reduces…", "Drink this to cure…",
  "Arjuna Tea prevents…", "Our product treats…", "Clinically proven to…" (for product)
- Doctor claim phrasings: "Dr. Bimal recommends…", "Dr. Bimal says this will…",
  "As recommended by Dr. Bimal…", "Doctor-approved benefits"

### Rule 6 — Headline types
- **Type A — broad wellness (safest):** no clinical test can measure/disprove it.
  "Your heart deserves daily care." · "A daily ritual for a healthy heart." ·
  "दिल का ख्याल रखो — हर रोज़" · "दिल की देखभाल — जड़ से करें"
- **Type B — consumer-fear question (allowed):** names the consumer's worry, not a
  product claim. "दिल के लिए हर नुस्खा आज़माया?" · "हर नुस्खा आज़माया?"
- **Type C — ingredient claim as headline (allowed):** ingredient is the subject +
  "helps" + published research. "अर्जुन छाल बुरे कोलेस्ट्रॉल को कम करने में मदद करती है"
- **Type D — banned:** disease name as the headline problem · product as subject making
  a claim · doctor making a health statement

> Subtlety the semantic checker must handle: "cholesterol" is banned in a headline
> **as a product promise** (Rule 5) but allowed when the **ingredient is the subject +
> "helps"** (Rule 6C). Subject detection decides it.

### Rule 7 — Disclaimers (ASCI)
- Needed if any claim could read as medical.
- Text: `*This product is not intended to diagnose, treat, cure or prevent any disease.`
  / `*यह उत्पाद किसी बीमारी के निदान, उपचार, इलाज या रोकथाम के लिए नहीं है।`
- Placement: visible, ≥8pt, contrasting colour, bottom of ad.
- Omittable only if headline is broad-wellness only **and** all claims are
  ingredient-attributed **and** no disease name appears anywhere.

### Rule 8 — Price / value claims
- ✅ "₹599 for 50 Tea Bags", "₹12 per day" (599÷50≈12), "50 Sachets" — factual
- Strikethrough MRP must be the **actual declared MRP** on packaging. No fabricated anchor.

### Rule 9 — Social proof
- ✅ "Formulated by Dr. Bimal Chhajer MBBS MD" · genuine verified star ratings ·
  "Trusted by X customers" if verified · ingredient research refs in small text
- ❌ fabricated testimonials · "clinically proven" without a trial citation ·
  celebrity/doctor endorsement (NMC) · before/after medical images · fake reviews

### PASS / FAIL checker (run before publishing anything)
1. Product making the health claim → **FAIL**; ingredient making it → PASS
2. Headline = disease name as a product promise → **FAIL**; consumer emotion or broad wellness → PASS
3. Dr. Bimal recommends/endorses → **FAIL**; "Formulated by" → PASS
4. Claim uses treats/cures/prevents → **FAIL**; supports/helps/aids → PASS
5. Claim backed by published research → PASS; no backing → **FAIL**
6. Ingredient named before the claim → PASS; only the product named → **FAIL**

### Product facts (for code)
```
PRODUCT      Dr. Bimal's Arjuna Cardio Care Tea
BRAND        Jaadu Diet / jaadudiet.com
FORMULATOR   Dr. Bimal Chhajer MBBS MD
PRICE        ₹599 / 50 Tea Bags   (₹12 per sachet/day)
INGREDIENTS  14–15 Ayurvedic herbs
KEY 6        Arjuna Chhal, Green Tea, Ashwagandha, Tulsi, Dry Ginger, Cinnamon
TYPE         Herbal tea — NOT a drug
CATEGORY     Food for Special Dietary Use (FSSAI); FSSAI licence required
```

---

## 6. Root catalogue — data model

```
root
  root_id            slug
  name               human name (assigned when a candidate is "named")
  status             candidate | named | retired
  mechanism          persuasion mechanism, 1 line
  why_it_works       1–2 sentences
  visual_motif       layout / skeleton in words
  fits_our_brand     yes | no | conditional
  compliance_notes   what to strip when adapting for us
  competitor_examples [ { brand, ad_id, image_url, first_seen, last_seen, still_active } ]
  our_executions      [ { product, image_url, ad_id, date } ]
  created_at / updated_at
```

Seeded with the 3 roots in §2. New competitor ads either attach to an existing root
or become a `candidate` for the team to name/approve.

---

## 7. Proposed architecture

```
1. FETCH   fast browser scrape of Meta Ads Library (website)
           - competitor list (Tier 1–3) + fallback keyword searches
           - filters: All ads → Images & Memes → Active only
           - also scrape our own brand ("Dr. Bimal's", "Jaadu Diet")
           - parallel; network-interception first; SSR next; DOM last
           - no fixed 20s / 3.5s sleeps — wait on real signals
           - reuse a logged-in browser session

2. STORE   dedupe by ad_id; Supabase (JSON + images)
           - track first_seen / last_seen / still_active per ad
           - only NEW or newly-inactive ads flow downstream

3. ROOT    vision model reads each new competitor ad → layout + motif + mechanism
  MATCH    - match to an existing root  → append competitor_example
           - no match                   → propose candidate root
           parallelised, proper rate limiter (no blanket per-item sleep)

4. GATE    compliance validator (§5) on the ad + "can this root work for us?"
           - Layer 1 deterministic keyword/regex
           - Layer 2 one LLM call for subject-detection & headline-type
           → PASS / NEEDS-EDIT / FAIL + reasons + compliant rewrite

5. BRIEF   for the fits → adaptation brief for a chosen Dr. Bimal's product
           - which root, which competitor execution to reference
           - compliant headline (Type A/B/C)
           - ingredient-led bullets with citations (Rule 4 table)
           - image prompt following the motif, NMC-safe
           - disclaimer if Rule 7 requires it
```

### Compliance encoding
- `compliance/ruleset.yaml` — all static data from §5 (banned words EN + Devanagari,
  approved verbs, Rule 4 claim/citation table, doctor rule, headline types,
  disclaimer logic, disease→safe-substitute map, product facts)
- `compliance/validate.py` — two-layer checker; same function scores competitor ads
  (catalogue only, marked "not usable as-is") and our generated adaptations (gate)

### Speed targets (vs current)
| Stage | Now | Target |
|---|---|---|
| Fetch 100 ads | 3–6 min, often times out at 360s | < 90 s |
| Vision analysis 50 ads | 7–10 min (blanket 4s/img + serial) | 1–2 min (parallel + rate limiter) |
| Steps 6/7 | +10s / +5s hardcoded sleeps | removed |
| `save_json` | sync Supabase upload every write | batched / async |
| Dashboard start | serial Supabase sync blocks first paint | lazy / parallel |

---

## 8. Decisions (resolved)

| Question | Decision |
|---|---|
| UI | Hosted **dashboard** |
| Cadence | **Both** — daily auto-refresh **and** on-demand "Run now"; each run does root-match + "does it fit our brand" analysis and surfaces only new/changed ads |
| Output | **Brief only** for v1; image generation is a later version |
| Competitor list | Elaborated in §4; two items still to confirm with team |
| Our own ads | **Do not scrape.** Our executions uploaded manually to the catalogue |
| Stack | Lowest cost — see §9 |
| Rollout | **Local first → verify → then host** (see §10) |

---

## 9. Hosting & cost

| Piece | Choice | Cost |
|---|---|---|
| Scraper + AI analysis | **GitHub Actions** — daily cron + manual `workflow_dispatch`. Playwright headless, writes to Supabase. | Free (2,000 min/mo; ~3–5 min per run) |
| Data store | **Supabase** free tier — Postgres (ad metadata + root catalogue), Storage (images) | Free (500 MB DB / 1 GB files) |
| Image overflow | Cloudflare R2 if Storage fills | Free ≤ 10 GB |
| Dashboard | **Streamlit Community Cloud** — read-only from Supabase; "Run now" button hits the GitHub Actions API | Free |
| LLM | Gemini Flash (vision) + Groq (text), free tiers | ~₹0–25/day |

Baseline **₹0/month**. The dashboard never runs a browser → fast, no timeouts.
Secrets (`FACEBOOK_EMAIL/PASSWORD` or saved session cookie, `GOOGLE_API_KEY`,
`GROQ_API_KEY_*`, `SUPABASE_*`) live as GitHub Actions secrets + Streamlit secrets.

---

## 10. Build phases

**Phase 1 — local, no hosting**
- `config/competitors.yaml`, `compliance/ruleset.yaml`, `data/roots/catalogue.json` (seeded with 3)
- fast fetcher (Playwright) → local JSON + images
- root-match (vision) + compliance gate + "fits our brand" → local JSON
- brief generator → local markdown/JSON
- one CLI: `python run.py --scan` (all competitors) / `--competitor "Kapiva"` / `--brief <ad_id> --product arjuna-tea`
- local Streamlit reads the local JSON

**Phase 2 — verify**
- run against Kapiva + OZiva, eyeball root matches, compliance verdicts, briefs
- tune prompts / rules with team

**Phase 3 — host**
- point storage at Supabase; move fetch+analysis into a GitHub Actions workflow
  (cron + dispatch); deploy dashboard to Streamlit Cloud; wire the "Run now" button

---

## 11. Still needed from team
- exact Kapiva Ad Library page name(s)
- prune §4 to every-run vs broader-search-only
- (later, Phase 3) GitHub repo + Supabase project + secrets
