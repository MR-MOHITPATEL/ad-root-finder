# Deploy — free, no card

```
GitHub (code + scraper Action)  ──dispatch──►  scraper runs on GitHub Actions
        │                                              │
        │ push to main mirrors ─────►  writes to  ◄────┘
        ▼                              Supabase (data + images)
Hugging Face Space (web UI)  ◄──reads/writes──┘
```

Everything below is free and needs no credit card.

---

## 1. GitHub repo secrets

Repo → **Settings → Secrets and variables → Actions**.

**Secrets** (New repository secret):

| Name | Value |
|---|---|
| `GOOGLE_API_KEY` | your Gemini key |
| `GROQ_API_KEY_1` | Groq key |
| `GROQ_API_KEY_2` | Groq key (optional) |
| `GROQ_API_KEY_3` | Groq key (optional) |
| `SUPABASE_URL` | `https://qbivdsztqwfgpwqzgxkd.supabase.co` |
| `SUPABASE_KEY` | the `sb_secret_…` key |
| `FACEBOOK_EMAIL` | a Facebook account for the scraper |
| `FACEBOOK_PASSWORD` | its password |
| `HF_TOKEN` | Hugging Face token — see step 3 |

**Variables** tab → New variable:

| Name | Value |
|---|---|
| `HF_SPACE` | `<your-hf-username>/ad-root-finder` |

---

## 2. Facebook account for the scraper

GitHub Actions runs from a datacenter IP, so anonymous scraping mostly fails.
Use a **throwaway / secondary Facebook account** (not your personal one — Meta may
flag automated logins). It only needs to be able to view the public Ad Library.
Put its email + password in the secrets above.

---

## 3. Hugging Face Space

1. **huggingface.co** → your profile → **New Space**
   - Owner: you · Space name: **ad-root-finder** · License: any
   - **SDK: Docker** · **Blank** template · Visibility: **Private**
   - Create
2. Space → **Settings → Variables and secrets** → add **secrets**:
   `GOOGLE_API_KEY`, `GROQ_API_KEY_1..3`, `SUPABASE_URL`, `SUPABASE_KEY`,
   `APP_PASSWORD` (the shared password your team types to get in),
   `GH_REPO` = `MR-MOHITPATEL/ad-root-finder`,
   `GH_DISPATCH_TOKEN` (a GitHub token with `actions: write` on the repo — a
   fine-grained PAT scoped to this one repo).
3. **huggingface.co/settings/tokens** → **New token** → type **Write** → copy it →
   this is `HF_TOKEN` for GitHub (step 1).

---

## 4. Push — it deploys itself

```bash
git add -A && git commit -m "deploy config" && git push
```

- The **deploy-space** workflow mirrors the repo to your HF Space → the Space builds
  the Docker image and starts the web UI.
- Open the Space URL, enter `APP_PASSWORD`, and you're in.

---

## 5. First data load

The Supabase tables start empty. Trigger the first scan:

- In the web UI sidebar → tick a few brands → **Fetch and analyze**
  (this dispatches the GitHub Action), **or**
- GitHub → **Actions → scan → Run workflow** → leave inputs blank for the every-run
  list, or type brand names, tick **deep** for the full back-catalogue.

The Action runs ~15–40 min, writes everything to Supabase, and the Space shows it.

The scan also runs **automatically every day at 07:30 IST**.

---

## Costs

| | Free tier | Our usage |
|---|---|---|
| GitHub Actions | 2000 min/mo (private) | ~1 daily scan ≈ 450 min/mo |
| Supabase | 500 MB DB · 1 GB storage | plenty for text + hero images |
| Hugging Face Space | 2 vCPU · 16 GB RAM, sleeps after 48h idle | fine for a workday tool |

**₹0/month.**

## Housekeeping

- Rotate the Supabase DB password (Settings → Database) — it was pasted in chat setup.
- If the scraper's Facebook account gets a checkpoint, log into it once from a
  normal browser to clear it, then re-run.
