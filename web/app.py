"""
Competitor Ad Root Finder — web UI (FastAPI + htmx).
Run:  python run.py web        (or)  uvicorn web.app:app --reload
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import Cookie, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from rootfinder import competitors as comp
from rootfinder import decisions as dec
from rootfinder import ledger
from rootfinder.analyze import merge_candidate_into_root, promote_candidate
from rootfinder.paths import IMAGES_DIR, ROOT, BRIEFS_DIR

from . import data as D

app = FastAPI(title="Ad Root Finder")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["comp"] = comp

_APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()


@app.middleware("http")
async def _password_gate(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    if not _APP_PASSWORD:
        return await call_next(request)                      # local dev: open
    hdr = request.headers.get("authorization", "")
    ok = False
    if hdr.startswith("Basic "):
        try:
            _, pw = base64.b64decode(hdr[6:]).decode("utf-8").split(":", 1)
            ok = secrets.compare_digest(pw.strip().encode(), _APP_PASSWORD.encode())
        except Exception:  # noqa: BLE001
            ok = False
    if not ok:
        return Response("Sign in", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="Ad Root Finder"'})
    return await call_next(request)   # outside try — real errors surface as 500

# ── background job (scan) ────────────────────────────────────────────────────
_job = {"running": False, "label": "", "log": "", "rc": None}


def _run_job(cmds: list[list[str]], label: str) -> None:
    _job.update(running=True, label=label, log="", rc=None)
    buf = []
    for cmd in cmds:
        p = subprocess.Popen([sys.executable, *cmd], cwd=str(ROOT),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            buf.append(line.rstrip())
            _job["log"] = "\n".join(buf[-400:])
        p.wait()
        _job["rc"] = p.returncode
    _job.update(running=False)


# ── pages ───────────────────────────────────────────────────────────────────
def _cards_ctx():
    clusters, approved, maybe, _, auto = D.review_state()
    cat = D.catalogue()
    clusters.sort(key=lambda c: max(m["_days"] for m in c), reverse=True)
    cards = [{
        "lead": max(c, key=lambda x: x["_days"]),
        "ids": [x["ad_id"] for x in c], "copies": len(c),
        "root": max(c, key=lambda x: x["_days"]).get("root") or {},
        "phash": D.ph(max(c, key=lambda x: x["_days"])),
    } for c in clusters]
    return {
        "cards": cards, "auto": auto,
        "n_pending": sum(len(c) for c in clusters), "n_unique": len(clusters),
        "n_approved": len(approved), "n_maybe": len(maybe),
        "root_ids": [r["root_id"] for r in cat["roots"]],
    }


@app.get("/", response_class=HTMLResponse)
def review(request: Request, rf_user: str = Cookie(default="")):
    try:
        ctx = _cards_ctx()
        ctx.update(request=request, nav="review", fresh=ledger.competitor_stats(), me=rf_user)
        return templates.TemplateResponse(request, "review.html", ctx)
    except Exception as e:  # noqa: BLE001 — surface the real error while stabilising
        import traceback
        return HTMLResponse(f"<pre>{type(e).__name__}: {e}\n\n{traceback.format_exc()}</pre>",
                            status_code=500)


@app.get("/queue", response_class=HTMLResponse)
def queue_partial(request: Request):
    ctx = _cards_ctx()
    ctx.update(request=request)
    return templates.TemplateResponse(request, "_queue.html", ctx)


@app.post("/whoami")
def whoami(name: str = Form(...)):
    r = RedirectResponse("/", status_code=303)
    r.set_cookie("rf_user", name.strip()[:40], max_age=60 * 60 * 24 * 365)
    return r


@app.post("/decide", response_class=HTMLResponse)
def decide(status: str = Form(...), ad_ids: str = Form(...),
           root_id: str = Form(""), note: str = Form(""), phash: str = Form(""),
           rf_user: str = Cookie(default="")):
    dec.set_many(json.loads(ad_ids), status, root_id=root_id or None,
                 note=note, phash=phash or None, by=rf_user)
    return ""  # htmx removes the card


@app.get("/approved", response_class=HTMLResponse)
def approved_page(request: Request):
    _, approved, _, dec_map, _ = D.review_state()
    approved.sort(key=lambda m: m["_days"], reverse=True)
    for m in approved:
        m["_decision"] = dec_map.get(m["ad_id"], {})
        m["_brief"] = D.brief_exists(m["ad_id"])
    return templates.TemplateResponse(request, "approved.html", {
        "request": request, "approved": approved, "nav": "approved",
        "need_brief": [m for m in approved if not m["_brief"]],
    })


@app.get("/catalogue", response_class=HTMLResponse)
def catalogue_page(request: Request):
    return templates.TemplateResponse(request, "catalogue.html", {
        "request": request, "cat": D.catalogue(), "nav": "catalogue"})


@app.get("/candidates", response_class=HTMLResponse)
def candidates_page(request: Request):
    cat = D.catalogue()
    return templates.TemplateResponse(request, "candidates.html", {
        "request": request, "cat": cat, "nav": "candidates",
        "root_ids": [r["root_id"] for r in cat["roots"]]})


@app.post("/candidates/promote")
def do_promote(name: str = Form(...), root_id: str = Form(...),
               why: str = Form(""), notes: str = Form("")):
    promote_candidate(name, root_id=root_id, why_it_works=why, compliance_notes=notes)
    return RedirectResponse("/candidates", status_code=303)


@app.post("/candidates/merge")
def do_merge(name: str = Form(...), root_id: str = Form(...)):
    merge_candidate_into_root(name, root_id)
    return RedirectResponse("/candidates", status_code=303)


@app.get("/briefs", response_class=HTMLResponse)
def briefs_page(request: Request):
    mds = sorted(BRIEFS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    briefs = [{"name": p.stem, "md": p.read_text(encoding="utf-8")} for p in mds]
    return templates.TemplateResponse(request, "briefs.html", {
        "request": request, "briefs": briefs, "nav": "briefs"})


@app.post("/brief/{ad_id}", response_class=HTMLResponse)
def make_brief(ad_id: str):
    from rootfinder.brief import generate
    try:
        out = generate(ad_id)
        sc = out["_self_check"]["status"]
        return f'<span class="text-green-600">brief ready — self-check {sc} — see Briefs tab</span>'
    except Exception as e:  # noqa: BLE001
        return f'<span class="text-red-600">failed: {e}</span>'


# ── scan control ────────────────────────────────────────────────────────────
_GH_REPO = os.getenv("GH_REPO", "").strip()
_GH_TOKEN = os.getenv("GH_DISPATCH_TOKEN", "").strip()


def _gh_dispatch(terms: list[str], deep: bool, full: bool) -> tuple[bool, str]:
    import urllib.error
    import urllib.request
    body = json.dumps({"ref": "main", "inputs": {
        "terms": "" if full else " ".join(terms),
        "deep": "true" if deep else "false",
        "broad": "true" if full else "false",
    }}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{_GH_REPO}/actions/workflows/scan.yml/dispatches",
        data=body, method="POST", headers={
            "Authorization": f"Bearer {_GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
    try:
        urllib.request.urlopen(req)
        return True, "Scan started on GitHub Actions — results land here as it runs."
    except urllib.error.HTTPError as e:
        return False, f"GitHub API {e.code}: {e.read().decode()[:200]}"


@app.post("/scan", response_class=HTMLResponse)
def start_scan(request: Request, terms: list[str] = Form(default=[]),
               deep: str = Form(""), full: str = Form("")):
    if _GH_TOKEN and _GH_REPO:
        ok, msg = _gh_dispatch(list(terms), bool(deep), bool(full))
        _job.update(running=False, label=msg, log="",
                    rc=0 if ok else 1)
        return _job_partial(request)

    if _job["running"]:
        return _job_partial(request)
    if full:
        cmds, label = [["run.py", "scan"]], "Full scan"
    elif terms:
        fetch_cmd = ["run.py", "fetch"] + (["--deep"] if deep else []) + list(terms)
        cmds = [fetch_cmd, ["run.py", "analyze", *terms]]
        label = ("Deep scan " if deep else "Scan ") + ", ".join(terms)
    else:
        return _job_partial(request)
    threading.Thread(target=_run_job, args=(cmds, label), daemon=True).start()
    time.sleep(0.3)
    return _job_partial(request)


@app.get("/scan/status", response_class=HTMLResponse)
def scan_status(request: Request):
    return _job_partial(request)


def _job_partial(request: Request):
    return templates.TemplateResponse(request, "_job.html", {"request": request, "job": _job})


@app.post("/competitors/add")
def add_competitor(name: str = Form(...), tier: str = Form("broad_only")):
    comp.add(name, tier=tier)
    return RedirectResponse("/", status_code=303)


@app.get("/health")
def health():
    import hashlib
    from rootfinder import store
    out = {
        "mode": store.mode(),
        "app_password_set": bool(_APP_PASSWORD),
        "app_password_len": len(_APP_PASSWORD),
        "app_password_sha8": hashlib.sha256(_APP_PASSWORD.encode()).hexdigest()[:8],
        "build": "auth-bytes-v2",
    }
    import traceback
    try:
        out["roots"] = len(store.catalogue_load().get("roots", []))
        out["matches"] = len(store.matches_all())
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    try:
        _cards_ctx()  # the exact thing the review page does
        out["review_render"] = "ok"
    except Exception as e:  # noqa: BLE001
        out["review_render"] = f"{type(e).__name__}: {e}"
        out["review_trace"] = traceback.format_exc()[-800:]
    return out


# ── images ──────────────────────────────────────────────────────────────────
@app.get("/img")
def img(path: str):
    if path.startswith("http://") or path.startswith("https://"):
        return RedirectResponse(path)
    p = Path(path).resolve()
    if IMAGES_DIR.resolve() in p.parents and p.exists():
        return FileResponse(p)
    return HTMLResponse("not found", status_code=404)
