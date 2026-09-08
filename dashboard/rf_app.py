"""
Competitor Ad Root Finder — local dashboard.
Run:  python run.py dashboard      (or)  streamlit run dashboard/rf_app.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rootfinder import competitors as comp  # noqa: E402
from rootfinder import decisions as dec  # noqa: E402
from rootfinder import ledger  # noqa: E402
from rootfinder.analyze import merge_candidate_into_root, promote_candidate  # noqa: E402
from rootfinder.imagehash import hamming, phash_file  # noqa: E402
from rootfinder.paths import BRIEFS_DIR, MATCHES_DIR, ROOTS_CATALOGUE, slug  # noqa: E402

NEAR = 8  # Hamming distance -> "same creative"


@st.cache_data(show_spinner=False)
def _phash(path: str | None, _mtime: float = 0.0) -> str | None:
    return phash_file(path) if path else None


def _ph(m: dict) -> str | None:
    if m.get("image_phash"):
        return m["image_phash"]
    p = m.get("image")
    try:
        mt = Path(p).stat().st_mtime if p and Path(p).exists() else 0.0
    except OSError:
        mt = 0.0
    return _phash(p, mt)


def _cluster(items: list[dict]) -> list[list[dict]]:
    """Group ads whose images are the same creative (Hamming <= NEAR)."""
    clusters: list[list[dict]] = []
    for m in items:
        h = _ph(m)
        for c in clusters:
            if hamming(h, _ph(c[0])) <= NEAR:
                c.append(m)
                break
        else:
            clusters.append([m])
    return clusters

st.set_page_config(page_title="Ad Root Finder", page_icon="🌱", layout="wide")


def _load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _img(src: str | None):
    if not src:
        return
    try:
        if src.startswith("http") or Path(src).exists():
            st.image(src, use_column_width=True)
    except Exception:  # noqa: BLE001
        pass


def _days_running(m: dict) -> int:
    s = (m.get("start_time") or "")[:10]
    try:
        d = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        return max((datetime.now(timezone.utc) - d).days, 0)
    except ValueError:
        return 0


def _run(cmd: list[str], label: str):
    with st.spinner(f"{label}…"):
        r = subprocess.run([sys.executable, *cmd], cwd=str(ROOT), capture_output=True, text=True)
    out = (r.stdout[-3000:] + r.stderr[-1500:]).strip() or "(no output)"
    (st.success if r.returncode == 0 else st.error)(f"{label} — done. Reload the page (R).")
    st.code(out)


cat = _load(ROOTS_CATALOGUE, {"roots": [], "candidates": []})
root_ids = [r["root_id"] for r in cat.get("roots", [])]
cand_names = [c["candidate_name"] for c in cat.get("candidates", [])]

all_matches: list[dict] = []
for f in sorted(MATCHES_DIR.glob("*.json")):
    for m in _load(f, {}).get("matches", []):
        m["_competitor_file"] = f.stem
        m["_days"] = _days_running(m)
        all_matches.append(m)

D = dec.load()
real = [m for m in all_matches if not m.get("is_noise")]

# auto-carry a decision to any undecided ad that is the SAME creative
_decided_ph = dec.decided_phashes()
_auto = 0
for m in real:
    if m["ad_id"] in D:
        continue
    h = _ph(m)
    for ph, info in _decided_ph.items():
        if hamming(h, ph) <= NEAR:
            dec.set_decision(m["ad_id"], info["status"], root_id=info.get("root_id"),
                             note="auto (same creative)", phash=h)
            _auto += 1
            break
if _auto:
    D = dec.load()

pending = [m for m in real if m["ad_id"] not in D]
approved = [m for m in real if D.get(m["ad_id"], {}).get("status") == "works"]

st.title("🌱 Competitor Ad Root Finder")
st.caption(f"{len(root_ids)} named roots · {len(cand_names)} candidates · "
           f"{len(real)} real creatives · **{len(pending)} to review** · {len(approved)} approved")

with st.sidebar:
    st.subheader("Run a scan")

    _brands = comp.brands()
    _labels = {f"{b['name']}"
               + ("  ·  every run" if b.get("tier") == "every_run" else ""): b for b in _brands}
    default_sel = [k for k, b in _labels.items() if b.get("tier") == "every_run"]
    chosen = st.multiselect("Competitors", list(_labels), default=default_sel,
                            help="Saved list — pick any. New brands you add below stay here.")

    new_brand = st.text_input("➕ Add a brand", placeholder="e.g. Traya")
    ac1, ac2 = st.columns(2)
    if ac1.button("Add", disabled=not new_brand, use_container_width=True):
        comp.add(new_brand, tier="broad_only")
        st.rerun()
    if ac2.button("Add + every-run", disabled=not new_brand, use_container_width=True):
        comp.add(new_brand, tier="every_run")
        st.rerun()

    _terms = [_labels[c]["search"] for c in chosen]
    if st.button("▶ Fetch + analyze selected", disabled=not chosen, use_container_width=True,
                 type="primary"):
        _run(["run.py", "fetch", *_terms], f"Fetching {len(_terms)} brand(s)")
        _run(["run.py", "analyze", *_terms], "Analyzing")

    if st.button("🕳️ Deep scan selected (past ads too)", disabled=not chosen,
                 use_container_width=True,
                 help="One-time: pulls the brand's whole history, not just active ads. Slower."):
        _run(["run.py", "fetch", "--deep", *_terms], f"Deep scan {len(_terms)} brand(s)")
        _run(["run.py", "analyze", *_terms], "Analyzing")

    st.divider()
    if st.button("Full scan (all every-run brands)", use_container_width=True):
        _run(["run.py", "scan"], "Full scan")
    st.caption("After a run finishes, reload the page (R).")

    st.divider()
    st.caption("**Freshness** — days since a competitor last had a new ad")
    _fs = ledger.competitor_stats()
    if not _fs:
        st.caption("no scans yet")
    for c, s in sorted(_fs.items(), key=lambda kv: kv[1].get("days_since_new") or 999):
        d = s.get("days_since_new")
        icon = "🔴 today" if d == 0 else (f"🟡 {d}d ago" if d is not None and d <= 7 else f"⚪ {d}d ago")
        st.caption(f"{c} — {s['total']} ads · new {icon}")

tab_review, tab_approved, tab_roots, tab_cand, tab_briefs = st.tabs([
    f"📋 Review ({len(pending)})",
    f"✅ Approved ({len(approved)})",
    "📚 Root catalogue",
    f"🆕 Candidates ({len(cand_names)})",
    "📝 Briefs",
])

# ── REVIEW QUEUE ─────────────────────────────────────────────────────────────
with tab_review:
    if not pending:
        st.success("Nothing to review — run a scan or check the Approved tab.")
    else:
        comps = sorted({m["_competitor_file"] for m in pending})
        f1, f2 = st.columns([2, 1])
        pick = f1.multiselect("Competitor", comps, default=comps)
        sort_by = f2.radio("Sort", ["Days running", "Root strength"], horizontal=True)
        q = [m for m in pending if m["_competitor_file"] in pick]

        clusters = _cluster(q)
        clusters.sort(key=(lambda c: max(m["_days"] for m in c)) if sort_by == "Days running"
                      else (lambda c: max((m.get("root") or {}).get("root_strength") or 0 for m in c)),
                      reverse=True)

        dupes = sum(len(c) - 1 for c in clusters)
        note_auto = f" · {_auto} auto-skipped (already judged this creative)" if _auto else ""
        st.caption(f"{len(clusters)} unique creatives to review "
                   f"({dupes} duplicate copies collapsed){note_auto} · decide once → applies to all copies")

        for c in clusters:
            m = max(c, key=lambda x: x["_days"])          # show the longest-running copy
            ids = [x["ad_id"] for x in c]
            rt = m.get("root") or {}
            guess = rt.get("root_id") or (f"🆕 {rt.get('candidate_name')}" if rt.get("is_candidate") else "?")
            fit = rt.get("fits_our_brand")
            fit_icon = {"yes": "🟢", "conditional": "🟡", "no": "🔴"}.get(fit, "⚪")
            with st.container(border=True):
                c1, c2 = st.columns([1, 2])
                with c1:
                    _img(m.get("image"))
                    st.markdown(f"[🔗 Open in Meta Ad Library]({m.get('snapshot_url','')})")
                    cap = f"{m.get('page_name')} · **{m['_days']}d running**"
                    if len(c) > 1:
                        cap += f" · **{len(c)} copies** of this creative"
                    st.caption(cap)
                with c2:
                    st.markdown(f"**{(m.get('headline') or '(no headline)')}**")
                    st.markdown(f"AI guess: **{guess}** · {fit_icon} fits our brand: **{fit}** · "
                                f"strength {rt.get('root_strength')}")
                    st.markdown(f"**Mechanism:** {rt.get('mechanism','')}")
                    st.markdown(f"**Visual motif:** {rt.get('visual_motif','')}")
                    st.markdown(f"**Why it may / may not fit us:** {rt.get('fit_reason','')}")

                    key = ids[0]
                    tag = st.selectbox(
                        "Which root is this?",
                        ["(use AI guess)"] + root_ids + [f"candidate: {n}" for n in cand_names],
                        key=f"tag_{key}",
                    )
                    root_id = tag if tag in root_ids else None
                    note = st.text_input("note (optional)", key=f"note_{key}")

                    b1, b2, b3 = st.columns(3)
                    if b1.button(f"✅ Works", key=f"w_{key}", use_container_width=True):
                        dec.set_many(ids, "works", root_id=root_id, note=note, phash=_ph(m))
                        st.rerun()
                    if b2.button("🤔 Maybe", key=f"mb_{key}", use_container_width=True):
                        dec.set_many(ids, "maybe", root_id=root_id, note=note, phash=_ph(m))
                        st.rerun()
                    if b3.button("❌ No", key=f"n_{key}", use_container_width=True):
                        dec.set_many(ids, "no", root_id=root_id, note=note, phash=_ph(m))
                        st.rerun()

# ── APPROVED ────────────────────────────────────────────────────────────────
with tab_approved:
    if not approved:
        st.info("Mark ads '✅ Works' in the Review tab and they show up here.")
    else:
        need = [m for m in approved
                if not (BRIEFS_DIR / f"{m['ad_id']}__arjuna-tea.md").exists()]
        if need and st.button(f"📝 Generate briefs for all {len(need)} approved ads without one"):
            _run(["run.py", "brief", "--all-approved"], "Generating briefs")
    for m in sorted(approved, key=lambda x: x["_days"], reverse=True):
        d = D.get(m["ad_id"], {})
        rt = m.get("root") or {}
        rid = d.get("root_id") or rt.get("root_id") or rt.get("candidate_name") or "?"
        with st.expander(f"[{m.get('page_name')}] {(m.get('headline') or m['ad_id'])[:60]}  ·  "
                         f"root: {rid}  ·  {m['_days']}d"):
            c1, c2 = st.columns([1, 2])
            with c1:
                _img(m.get("image"))
                st.markdown(f"[🔗 Ad Library]({m.get('snapshot_url','')})")
            with c2:
                st.markdown(f"**Root:** {rid}")
                st.markdown(f"**Motif:** {rt.get('visual_motif','')}")
                if d.get("note"):
                    st.markdown(f"**Your note:** {d['note']}")
                brief_md = BRIEFS_DIR / f"{m['ad_id']}__arjuna-tea.md"
                if brief_md.exists():
                    st.success("Brief ready — see the Briefs tab")
                if st.button("Generate adaptation brief", key=f"br_{m['ad_id']}"):
                    _run(["run.py", "brief", m["ad_id"]], "Generating brief")
                if st.button("↩ Undo approval", key=f"u_{m['ad_id']}"):
                    dec.clear(m["ad_id"])
                    st.rerun()

# ── ROOT CATALOGUE ──────────────────────────────────────────────────────────
with tab_roots:
    for r in cat.get("roots", []):
        with st.expander(f"**{r['name']}**  ·  `{r['root_id']}`  ·  "
                         f"{len(r.get('competitor_examples', []))} competitor examples"):
            st.markdown(f"**Mechanism:** {r['mechanism']}")
            st.markdown(f"**Why it works:** {r.get('why_it_works','')}")
            st.markdown(f"**Visual motif:** {r['visual_motif']}")
            st.markdown(f"**Fits our brand:** `{r.get('fits_our_brand')}`")
            st.info(f"Compliance when adapting: {r.get('compliance_notes','')}")
            for label, key in [("Competitor executions", "competitor_examples"),
                               ("Our executions", "our_executions")]:
                items = [e for e in r.get(key, []) if e.get("image_url")]
                if items:
                    st.markdown(f"**{label}**")
                    cols = st.columns(min(len(items), 4))
                    for i, e in enumerate(items[:8]):
                        with cols[i % 4]:
                            _img(e.get("image_url"))
                            st.caption(f"{e.get('brand', e.get('note',''))}"[:40])

# ── CANDIDATES ──────────────────────────────────────────────────────────────
with tab_cand:
    st.caption("New roots proposed from competitor ads — Merge into an existing root, or Promote to a new named root.")
    for i, c in enumerate(cat.get("candidates", [])):
        with st.expander(f"🆕 **{c.get('candidate_name')}**  ·  {len(c.get('examples', []))} ads  ·  "
                         f"fits={c.get('fits_our_brand')}"):
            st.markdown(f"**Mechanism:** {c.get('mechanism','')}")
            st.markdown(f"**Visual motif:** {c.get('visual_motif','')}")
            ex = [e for e in c.get("examples", []) if e.get("image_url")]
            if ex:
                cols = st.columns(min(len(ex), 4))
                for j, e in enumerate(ex[:8]):
                    with cols[j % 4]:
                        _img(e.get("image_url"))
                        st.caption(f"{e.get('brand')}")
            st.divider()
            pc1, pc2 = st.columns(2)
            with pc1:
                st.markdown("**Promote to a named root**")
                rid = st.text_input("root_id", value=slug(c.get("candidate_name", "")), key=f"rid_{i}")
                why = st.text_area("why it works", key=f"why_{i}", height=70)
                notes = st.text_area("compliance notes when adapting for us", key=f"cn_{i}", height=70)
                if st.button("✅ Promote", key=f"prom_{i}"):
                    promote_candidate(c["candidate_name"], root_id=rid, why_it_works=why,
                                      compliance_notes=notes)
                    st.success("Promoted — reload")
            with pc2:
                st.markdown("**Or merge into an existing root**")
                target = st.selectbox("root", root_ids, key=f"mg_{i}")
                if st.button("🔗 Merge", key=f"mgb_{i}"):
                    merge_candidate_into_root(c["candidate_name"], target)
                    st.success("Merged — reload")

# ── BRIEFS ──────────────────────────────────────────────────────────────────
with tab_briefs:
    mds = sorted(BRIEFS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mds:
        st.info("No briefs yet — approve an ad, then Generate adaptation brief.")
    for p in mds:
        with st.expander(p.stem, expanded=False):
            st.markdown(p.read_text(encoding="utf-8"))
