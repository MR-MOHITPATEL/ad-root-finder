# Ad Root Finder

Finds reusable **ad roots** — creative concepts (persuasion mechanism + visual motif) —
in competitor Meta ads, and turns the ones that fit into NMC/FSSAI-compliant
adaptation briefs for Dr. Bimal's / Jaadu Diet products.

Full design and rules: [REBUILD_SPEC.md](REBUILD_SPEC.md).
Deployment: [DEPLOY.md](DEPLOY.md).

## Pipeline

```
scan  (Playwright, page-scoped)  ─►  fetch competitor ads + hero images
analyze  (Gemini vision)         ─►  is it a real root? match / propose candidate + fits-our-brand
review  (web UI)                 ─►  keep / maybe / skip  (10 reviewers, deduped by image hash)
brief   (Gemini + compliance)    ─►  adaptation brief for a chosen product
```

Compliance (`compliance/ruleset.yaml`) gates **our** output only — never competitor ads.

## Layout

| Path | What |
|---|---|
| `rootfinder/` | scraper, analyzer, brief generator, data store |
| `web/` | FastAPI + htmx review UI |
| `compliance/` | NMC/FSSAI ruleset + two-layer validator |
| `config/` | `competitors.yaml`, `catalogue.seed.json` |
| `.github/workflows/scan.yml` | nightly + on-demand scrape |

## Run locally

```bash
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env      # fill in keys; leave SUPABASE_* blank for local file mode
python run.py scan --deep
python run.py web          # http://localhost:8000
```
