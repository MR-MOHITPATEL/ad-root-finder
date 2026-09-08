# Deploy

```
GitHub (code + scraper Action)  ──dispatch──►  scraper runs on GitHub Actions
        │                                              │
        │ Railway auto-deploys on push                 ▼
        ▼                                     writes to Supabase (data + images)
Railway (web UI, Dockerfile)  ◄──reads/writes────────┘
```

Web UI runs on **Railway** (already on the $5 plan). Scraper runs on **GitHub Actions**
(free). Data lives in **Supabase** (free). No card needed beyond the Railway account
you already have.

---

## 1. GitHub repo secrets

`github.com/MR-MOHITPATEL/ad-root-finder` → **Settings → Secrets and variables → Actions**

**Secrets** (one Name/Secret pair each — not combined):

| Name | Value |
|---|---|
| `GOOGLE_API_KEY` | your Gemini key |
| `GROQ_API_KEY_1` / `_2` / `_3` | Groq keys |
| `SUPABASE_URL` | `https://qbivdsztqwfgpwqzgxkd.supabase.co` |
| `SUPABASE_KEY` | the `sb_secret_…` key |
| `FACEBOOK_EMAIL` | the scraper's Facebook account |
| `FACEBOOK_PASSWORD` | its password |

---

## 2. Railway — new service in your existing project

1. Railway dashboard → open your project → **+ New** → **GitHub Repo** →
   select `MR-MOHITPATEL/ad-root-finder`
2. It finds the `Dockerfile` at the repo root and builds automatically.
3. New service → **Variables** tab → add:

   | Name | Value |
   |---|---|
   | `GOOGLE_API_KEY` | same as above |
   | `GROQ_API_KEY_1` / `_2` / `_3` | same as above |
   | `SUPABASE_URL` | same as above |
   | `SUPABASE_KEY` | same as above |
   | `APP_PASSWORD` | a password your team types to get in — pick one |
   | `GH_REPO` | `MR-MOHITPATEL/ad-root-finder` |
   | `GH_DISPATCH_TOKEN` | see step 3 |

4. Service → **Settings → Networking → Generate Domain** → gives you a public
   `https://….up.railway.app` URL. That's what your team opens.

---

## 3. GitHub dispatch token (lets the "Run scan" button trigger the Action)

`github.com/settings/tokens?type=beta` → **Generate new token** (fine-grained)
→ Repository access: only `ad-root-finder` → Permissions → **Actions: Read and
write** → Generate → paste as `GH_DISPATCH_TOKEN` in the Railway service
Variables above.

---

## 4. First data load

Supabase tables start empty. Either:
- In the web UI sidebar → tick a few brands → **Fetch and analyze** (dispatches
  the Action), or
- GitHub → **Actions → scan → Run workflow** → tick **deep**, leave terms blank
  → pulls the every-run brands' full back-catalogue (~30–40 min).

The scan also runs automatically every day at 07:30 IST.

---

## Costs

| | Plan | Our usage |
|---|---|---|
| Railway | $5/mo plan you already have | one lightweight service, shared with whatever else is on the plan |
| GitHub Actions | 2000 min/mo free (private repo) | ~1 daily scan ≈ 450 min/mo |
| Supabase | free tier — 500 MB DB, 1 GB storage | plenty for text + hero images |

## Housekeeping

- Rotate the Supabase DB password (Settings → Database) and the Groq keys — both
  were pasted in chat during setup.
- If the scraper's Facebook account gets a checkpoint, log into it once from a
  normal browser to clear it, then re-run.
