"""
Fast Meta Ad Library scraper.

Strategy: intercept the GraphQL search responses the page fires as you scroll.
No fixed sleeps — every wait is bounded and driven by "did new ad data arrive".
Falls back to embedded SSR JSON if interception yields nothing.

Filters match the manual workflow: keyword search across advertisers,
Images & Memes, Active ads only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)

from .paths import ADS_DIR, IMAGES_DIR, ROOT, STORAGE_STATE, slug

load_dotenv(ROOT / ".env")

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
except Exception:  # noqa: BLE001
    pass

ADS_LIBRARY = "https://www.facebook.com/ads/library/"
GRAPHQL_MARK = "/api/graphql/"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
_IMG_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RootFinderBot/1.0)"}
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

_STEALTH = """
() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
  Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
}
"""


@dataclass
class FetchOptions:
    country: str = "IN"
    media_type: str = "all"              # image_and_meme filter is unreliable; we filter ourselves
    active_status: str = "active"        # active | inactive | all
    max_ads: int = 60
    headless: bool = True
    login: bool = False                  # log into FB (needed on some IPs / for stability)
    # wait knobs (ms)
    first_batch_timeout: int = 25_000
    scroll_idle_timeout: int = 4_500
    max_stale_scrolls: int = 3
    hard_cap_seconds: int = 200          # never spend more than this on one competitor (2 phases)
    brand_filter: bool = True            # keep only ads from the advertiser page matching `term`
    primary_image_only: bool = True      # download only the first (hero) image per ad
    drop_catalog_ads: bool = True        # drop DPA/catalog ads (template-literal headlines)
    page_scoped: bool = True             # after keyword search, pull the advertiser page's full library


# ── URL ──────────────────────────────────────────────────────────────────────

def build_search_url(term: str, o: FetchOptions) -> str:
    from urllib.parse import urlencode

    params = {
        "active_status": o.active_status,
        "ad_type": "all",
        "country": o.country,
        "q": term,
        "search_type": "keyword_unordered",
        "media_type": o.media_type,
    }
    return f"{ADS_LIBRARY}?{urlencode(params)}"


def build_page_url(page_id: str, o: FetchOptions) -> str:
    from urllib.parse import urlencode

    params = {
        "active_status": o.active_status,
        "ad_type": "all",
        "country": o.country,
        "view_all_page_id": page_id,
        "search_type": "page",
        "media_type": o.media_type,
    }
    return f"{ADS_LIBRARY}?{urlencode(params)}"


# ── GraphQL body parsing ─────────────────────────────────────────────────────

def _iter_json(body: str):
    """Yield every top-level JSON value in a (possibly multi-object) GraphQL body."""
    body = body.strip()
    if body.startswith("for (;;);"):
        body = body[len("for (;;);"):]
    # Fast path: single object
    try:
        yield json.loads(body)
        return
    except json.JSONDecodeError:
        pass
    # Newline-delimited stream
    dec = json.JSONDecoder()
    idx, n = 0, len(body)
    while idx < n:
        while idx < n and body[idx] in " \r\n\t":
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = dec.raw_decode(body, idx)
        except json.JSONDecodeError:
            nxt = body.find("\n", idx)
            if nxt == -1:
                break
            idx = nxt + 1
            continue
        yield obj
        idx = end


def _walk_for_ads(obj, depth: int = 0):
    if depth > 18 or not isinstance(obj, (dict, list)):
        return []
    if isinstance(obj, list):
        out = []
        for it in obj:
            out.extend(_walk_for_ads(it, depth + 1))
        return out
    if "ad_archive_id" in obj and isinstance(obj.get("snapshot"), dict):
        ad = _normalize(obj)
        return [ad] if ad else []
    for wrapper in ("collated_results", "collationUnits", "results", "edges", "node"):
        if wrapper in obj:
            out = []
            v = obj[wrapper]
            for unit in (v if isinstance(v, list) else [v]):
                out.extend(_walk_for_ads(unit, depth + 1))
            if out:
                return out
    out = []
    for v in obj.values():
        if isinstance(v, (dict, list)):
            out.extend(_walk_for_ads(v, depth + 1))
    return out


def _ts(v):
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _normalize(unit: dict) -> dict | None:
    ad_id = str(unit.get("ad_archive_id") or "")
    if not ad_id:
        return None
    snap = unit.get("snapshot") or {}

    body_raw = snap.get("body")
    body = body_raw.get("text") if isinstance(body_raw, dict) else str(body_raw or "")
    body = (body or "").strip() or None

    images: list[str] = []
    for img in snap.get("images") or []:
        u = img.get("original_image_url") or img.get("resized_image_url")
        if u:
            images.append(u)
    videos: list[str] = []
    for vid in snap.get("videos") or []:
        u = vid.get("video_hd_url") or vid.get("video_sd_url") or vid.get("video_preview_image_url")
        if u:
            videos.append(u)
    cards: list[dict] = []
    for card in snap.get("cards") or []:
        ci = card.get("original_image_url") or card.get("resized_image_url")
        if ci and ci not in images:
            images.append(ci)
        cards.append({
            "title": card.get("title"),
            "body": card.get("body"),
            "cta_type": card.get("cta_type"),
            "link_url": card.get("link_url"),
            "image_url": ci,
        })

    page_name = (unit.get("page_name") or snap.get("page_name") or "").strip()
    return {
        "ad_id": ad_id,
        "page_id": str(unit.get("page_id") or ""),
        "page_name": page_name,
        "is_active": bool(unit.get("is_active", True)),
        "headline": snap.get("title") or None,
        "body": body,
        "link_description": snap.get("link_description") or None,
        "cta_type": snap.get("cta_type") or snap.get("cta_text") or None,
        "link_url": snap.get("link_url") or None,
        "display_format": snap.get("display_format") or None,
        "start_time": _ts(unit.get("start_date")),
        "stop_time": _ts(unit.get("end_date")),
        "collation_count": unit.get("collation_count"),
        "snapshot_url": f"https://www.facebook.com/ads/library/?id={ad_id}",
        "image_urls": images,
        "video_urls": videos,
        "cards": cards,
        "primary_image_url": images[0] if images else None,
        "publisher_platforms": unit.get("publisher_platform") or [],
    }


def _extract_ssr(html: str) -> list[dict]:
    if "ad_archive_id" not in html:
        return []
    ads, seen = [], set()
    for m in re.finditer(r'"collated_results":\[', html):
        start = m.end() - 1
        depth, end = 0, start
        for i in range(start, min(start + 400_000, len(html))):
            c = html[i]
            if c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        try:
            for ad in _walk_for_ads(json.loads(html[start:end + 1])):
                if ad["ad_id"] not in seen:
                    seen.add(ad["ad_id"])
                    ads.append(ad)
        except json.JSONDecodeError:
            pass
    return ads


# ── Browser ──────────────────────────────────────────────────────────────────

def _dismiss_cookies(page: Page) -> None:
    for sel in (
        'button:has-text("Allow all cookies")',
        'button:has-text("Only allow essential cookies")',
        'button:has-text("Decline optional cookies")',
        '[data-testid="cookie-policy-manage-dialog-accept-button"]',
    ):
        try:
            page.click(sel, timeout=1500)
            return
        except PWTimeout:
            continue


def _login(context: BrowserContext) -> bool:
    email = os.getenv("FACEBOOK_EMAIL", "")
    pw = os.getenv("FACEBOOK_PASSWORD", "")
    if not (email and pw):
        print("  [login] FACEBOOK_EMAIL/PASSWORD not set — continuing anonymously")
        return False
    page = context.new_page()
    try:
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=45_000)
        _dismiss_cookies(page)
        for sel in ("#email", 'input[name="email"]'):
            try:
                page.fill(sel, email, timeout=4000)
                break
            except PWTimeout:
                continue
        for sel in ("#pass", 'input[name="pass"]'):
            try:
                page.fill(sel, pw, timeout=4000)
                break
            except PWTimeout:
                continue
        for sel in ('[name="login"]', 'button[type="submit"]'):
            try:
                page.click(sel, timeout=4000)
                break
            except PWTimeout:
                continue
        page.wait_for_load_state("networkidle", timeout=20_000)
        ok = "login" not in page.url and "checkpoint" not in page.url
        if ok:
            context.storage_state(path=str(STORAGE_STATE))
            print("  [login] ok — session saved")
        else:
            print(f"  [login] failed — url={page.url}")
        return ok
    except Exception as e:  # noqa: BLE001
        print(f"  [login] error: {e}")
        return False
    finally:
        page.close()


def _collect(page: Page, ads: dict, url: str, o: FetchOptions, started: float,
             target: int | None = None) -> None:
    """Navigate to `url` and harvest ad data by scrolling until it stops growing."""
    target = target if target is not None else o.max_ads
    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    _dismiss_cookies(page)

    deadline = time.monotonic() + o.first_batch_timeout / 1000
    n0 = len(ads)
    while len(ads) == n0 and time.monotonic() < deadline:
        page.wait_for_timeout(250)
    if len(ads) == n0:
        for ad in _extract_ssr(page.content()):
            ads.setdefault(ad["ad_id"], ad)

    stale = 0
    while (
        len(ads) < target
        and stale < o.max_stale_scrolls
        and time.monotonic() - started < o.hard_cap_seconds
    ):
        before = len(ads)
        page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        for btn in ("See more results", "Load more"):
            try:
                page.click(f'div[role="button"]:has-text("{btn}")', timeout=800)
            except PWTimeout:
                pass
        idle_deadline = time.monotonic() + o.scroll_idle_timeout / 1000
        while time.monotonic() < idle_deadline:
            page.wait_for_timeout(250)
            if len(ads) > before:
                break
        stale = stale + 1 if len(ads) == before else 0


def scrape_one(context: BrowserContext, term: str, o: FetchOptions) -> list[dict]:
    ads: dict[str, dict] = {}
    page = context.new_page()
    page.add_init_script(_STEALTH)

    def on_response(resp):
        try:
            if GRAPHQL_MARK not in resp.url or resp.request.method != "POST":
                return
            body = resp.text()
            if "ad_archive_id" not in body:
                return
            for obj in _iter_json(body):
                for ad in _walk_for_ads(obj):
                    ads.setdefault(ad["ad_id"], ad)
        except Exception:  # noqa: BLE001
            pass

    page.on("response", on_response)
    started = time.monotonic()
    try:
        # Phase A — keyword search (small, just to discover the advertiser page)
        _collect(page, ads, build_search_url(term, o), o, started, target=40)

        # Phase B — page-scoped: pull each matching advertiser page's full library.
        # This is the reliable source; the keyword search is only for discovery.
        if o.brand_filter and o.page_scoped:
            def _brand_img_count() -> int:
                return sum(1 for a in ads.values()
                           if a.get("image_urls") and _brand_match(term, a.get("page_name", "")))

            for pid in _dominant_page_ids(ads, term)[:3]:
                if _brand_img_count() >= o.max_ads:
                    break
                if time.monotonic() - started >= o.hard_cap_seconds:
                    break
                print(f"    -> page-scoped: view_all_page_id={pid}")
                _collect(page, ads, build_page_url(pid, o), o, started,
                         target=len(ads) + o.max_ads)
    finally:
        page.close()

    out = list(ads.values())
    raw = len(out)
    if o.active_status == "active":
        out = [a for a in out if a.get("is_active")]
    out = [a for a in out if a.get("image_urls")]  # image ads only
    if o.drop_catalog_ads:
        out = [a for a in out if not _is_catalog(a)]
    if o.brand_filter:
        out = [a for a in out if _brand_match(term, a.get("page_name", ""))]
    if o.primary_image_only:
        for a in out:
            a["image_urls"] = a["image_urls"][:1]
            a["cards"] = (a.get("cards") or [])[:1]
    elapsed = time.monotonic() - started
    print(f"  {term!r}: {len(out)}/{raw} image ads (brand_filter={o.brand_filter}) in {elapsed:0.1f}s")
    return out[: o.max_ads]


def _dominant_page_ids(ads: dict, term: str) -> list[str]:
    """Page ids whose page_name matches the brand, most-ads first."""
    from collections import Counter

    c: Counter[str] = Counter()
    for a in ads.values():
        pid = a.get("page_id")
        if pid and _brand_match(term, a.get("page_name", "")):
            c[pid] += 1
    return [pid for pid, _ in c.most_common()]


def _is_catalog(ad: dict) -> bool:
    """Dynamic product-catalog ads (DPA) — never a deliberate 'root'.
    Signalled by an unresolved template literal like {{product.name}}.
    DCO / dynamic-creative ads are NOT dropped here — the vision step decides
    whether they are a real designed creative or noise.
    """
    if "{{" in (ad.get("headline") or "") or "{{" in (ad.get("body") or ""):
        return True
    if (ad.get("display_format") or "").upper() == "DPA":
        return True
    return False


def _brand_match(term: str, page_name: str) -> bool:
    t, p = slug(term), slug(page_name)
    if not p:
        return False
    if t in p or p in t:
        return True
    tt = {w for w in t.split("_") if w}
    pp = {w for w in p.split("_") if w}
    return bool(tt) and len(tt & pp) >= len(tt) - (1 if len(tt) > 1 else 0)


# ── Images ───────────────────────────────────────────────────────────────────

def _download(url: str, dest: Path) -> str | None:
    existing = list(dest.parent.glob(dest.stem + ".*"))
    if existing:
        return str(existing[0])
    try:
        r = requests.get(url, headers=_IMG_HEADERS, timeout=15)
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if not ct.startswith("image/"):
            return None
        dest = dest.with_suffix(_EXT.get(ct, ".jpg"))
        dest.write_bytes(r.content)
        return str(dest)
    except Exception:  # noqa: BLE001
        return None


def download_images(ads: list[dict], competitor: str, workers: int = 12) -> None:
    out_dir = IMAGES_DIR / slug(competitor)
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[dict, int, str, Path]] = []
    for ad in ads:
        for i, u in enumerate(ad.get("image_urls") or []):
            jobs.append((ad, i, u, out_dir / f"{ad['ad_id']}_{i}"))
    local: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_download, u, dest): (ad, i, u) for ad, i, u, dest in jobs}
        for fut in futs:
            ad, i, u = futs[fut]
            path = fut.result()
            local.setdefault(ad["ad_id"], []).append(path or u)
    from .imagehash import phash_file

    ok = 0
    for ad in ads:
        paths = local.get(ad["ad_id"], [])
        ad["local_image_paths"] = [p for p in paths if p and not p.startswith("http")]
        ok += len(ad["local_image_paths"])
        ad["image_phash"] = phash_file(ad["local_image_paths"][0]) if ad["local_image_paths"] else None
    print(f"  images: {ok}/{len(jobs)} downloaded")


# ── Entry ────────────────────────────────────────────────────────────────────

def _make_context(browser: Browser, o: FetchOptions) -> BrowserContext:
    kwargs = dict(
        viewport={"width": 1440, "height": 900},
        user_agent=_UA,
        locale="en-US",
        timezone_id="Asia/Kolkata",
    )
    if STORAGE_STATE.exists():
        kwargs["storage_state"] = str(STORAGE_STATE)
    return browser.new_context(**kwargs)


def fetch(terms: list[str], o: FetchOptions) -> dict[str, list[dict]]:
    """Scrape each term, save data/rf/ads/{slug}.json, return {term: ads}."""
    results: dict[str, list[dict]] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=o.headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = _make_context(browser, o)
        if o.login and not STORAGE_STATE.exists():
            _login(context)
            context.close()
            context = _make_context(browser, o)

        from . import ledger

        for term in terms:
            mode = "DEEP (all-time)" if o.active_status == "all" else "active"
            print(f">> {term}  [{mode}]")
            try:
                ads = scrape_one(context, term, o)
            except Exception as e:  # noqa: BLE001
                print(f"  ERROR: {e}")
                ads = []
            if ads:
                download_images(ads, term)

            delta = ledger.update(term, ads)
            print(f"  ledger: {len(delta['new'])} new · {delta['returning']} returning"
                  + (f" · {len(delta['newly_inactive'])} went inactive" if delta["newly_inactive"] else ""))

            # merge into the ads file (keep everything ever scraped for this term)
            out = ADS_DIR / f"{slug(term)}.json"
            prev = json.loads(out.read_text(encoding="utf-8")).get("ads", []) if out.exists() else []
            by_id = {a["ad_id"]: a for a in prev}
            by_id.update({a["ad_id"]: a for a in ads})
            merged = list(by_id.values())
            payload = {
                "term": term,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "options": o.__dict__,
                "count": len(merged),
                "new_this_scan": delta["new"],
                "ads": merged,
            }
            out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            results[term] = merged

        context.close()
        browser.close()
    return results


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("terms", nargs="+")
    ap.add_argument("--max-ads", type=int, default=60)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--country", default="IN")
    a = ap.parse_args()
    fetch(a.terms, FetchOptions(
        country=a.country, max_ads=a.max_ads, headless=not a.headed, login=a.login,
    ))
