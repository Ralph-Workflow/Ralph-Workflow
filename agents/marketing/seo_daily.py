#!/usr/bin/env python3
"""SEO Daily — RalphWorkflow self-improving SEO loop.

This is the SEO intelligence layer. It runs daily and collects:
- Site health (technical SEO)
- Keyword rank positions
- Backlink counts
- On-page SEO score
- Competitor overview

Results feed into run.py's weekly decisions and content strategy.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

AGENTS_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing")
LOG_DIR = AGENTS_DIR / "logs"
REPORTS_DIR = Path("/home/mistlight/.openclaw/workspace/seo-reports")
LOG_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

SITE = "ralphworkflow.com"
SITE_URL = f"https://{SITE}"

# Priority keywords from May 2026 SEO strategy review
PRIORITY_KEYWORDS = [
    "unattended coding agent",
    "AI agent orchestration CLI",
    "spec-driven AI agent",
    "AI coding workflow automation",
    "Claude Code automation",
    "Claude Code unattended",
    "AI agent workflow composer",
    "Ralph Workflow",
]

# Competitors to monitor
COMPETITORS = [
    "claude.ai/code",
    "cursor.com",
    "github.com/features/copilot",
    "aider.chat",
    "continue.dev",
]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def http_get(url: str, headers: dict | None = None, timeout: int = 12) -> tuple[int, str]:
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; SEOBot/1.0)"}
    if headers:
        hdrs.update(headers)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        return 0, str(exc)


def fetch_robots_txt() -> dict:
    status, body = http_get(f"{SITE_URL}/robots.txt")
    directives: list[str] = []
    sitemap_line = ""
    if status == 200:
        for line in body.splitlines():
            line = line.strip()
            if line.lower().startswith("sitemap:"):
                sitemap_line = line.split(":", 1)[1].strip()
            elif line and not line.startswith("#") and not line.startswith("sitemap"):
                directives.append(line)
    return {
        "status": status,
        "directives": directives,
        "sitemap": sitemap_line,
    }


def fetch_sitemap_index() -> dict:
    status, body = http_get(f"{SITE_URL}/sitemap.xml")
    urls: list[str] = []
    if status == 200:
        urls = re.findall(r"<loc>(.*?)</loc>", body)
    return {"status": status, "url_count": len(urls), "urls": urls[:20]}


def _homepage_from_html(status: int, body: str) -> dict:
    if status != 200:
        return {"ok": False, "status": status, "error": body}
    return {
        "ok": True,
        "status": status,
        "title": _extract_title(body),
        "meta_description": _extract_meta(body, "description"),
        "canonical": _extract_canonical(body),
        "og_tags": _extract_og(body),
        "twitter_card": _extract_twitter(body),
        "json_ld": bool(re.search(r'<script[^>]+type=["\']application/ld\+json["\']', body)),
        "has_h1": bool(re.search(r"<h1", body, re.I)),
        "word_count": len(body.split()),
        "has_nav": bool(re.search(r"<nav", body, re.I)),
        "has_main": bool(re.search(r"<main", body, re.I)),
        "lang_attr": _extract_lang(body),
    }


def _homepage_looks_suspicious(homepage: dict) -> bool:
    if not homepage.get("ok"):
        return False
    critical_missing = sum(
        1 for value in (
            homepage.get("meta_description"),
            homepage.get("canonical"),
            homepage.get("twitter_card"),
            homepage.get("lang_attr"),
        ) if not value
    )
    if not homepage.get("og_tags"):
        critical_missing += 1
    return homepage.get("word_count", 0) < 150 and critical_missing >= 3


def fetch_homepage(retries: int = 3, delay_s: float = 2.0) -> dict:
    """Fetch homepage with retries on network errors (status=0) and suspicious content.

    Bug fixed 2026-05-30: transient network failures (status=0) returned ok=False,
    which skipped the suspicious-content retry path entirely, producing permanent
    0/100 SEO scores.
    """
    last_homepage: dict = {}
    for attempt in range(retries):
        status, body = http_get(SITE_URL)
        homepage = _homepage_from_html(status, body)
        if homepage.get("ok"):
            # Got a successful fetch — check if content looks suspicious
            if _homepage_looks_suspicious(homepage):
                probe_url = f"{SITE_URL}?seo_probe={int(time.time())}"
                retry_status, retry_body = http_get(probe_url, headers={"Cache-Control": "no-cache"})
                retry_homepage = _homepage_from_html(retry_status, retry_body)
                if retry_homepage.get("ok") and retry_homepage.get("word_count", 0) > homepage.get("word_count", 0):
                    retry_homepage["retried_after_suspicious_probe"] = True
                    return retry_homepage
                homepage["retried_after_suspicious_probe"] = True
            return homepage
        # Network error (status=0, ok=False) — retry
        last_homepage = homepage
        if attempt < retries - 1:
            time.sleep(delay_s)
    # All retries exhausted — return last failure with retry metadata
    last_homepage["fetch_retries_exhausted"] = retries
    last_homepage["fetch_error"] = last_homepage.get("error", "all fetch attempts failed")
    return last_homepage


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return m.group(1).strip() if m else ""


def _extract_meta(html: str, name: str) -> str:
    patterns = [
        rf"<meta[^>]+name=['\"]{re.escape(name)}['\"][^>]+content=['\"]([^'\"]+)['\"]",
        rf"<meta[^>]+content=['\"]([^'\"]+)['\"][^>]+name=['\"]{re.escape(name)}['\"]",
    ]
    for p in patterns:
        m = re.search(p, html, re.I)
        if m:
            return m.group(1).strip()
    return ""


def _extract_canonical(html: str) -> str:
    m = re.search(r"<link[^>]+rel=['\"]canonical['\"][^>]+href=['\"]([^'\"]+)['\"]", html, re.I)
    return m.group(1).strip() if m else ""


def _extract_og(html: str) -> dict:
    tags = {}
    for m in re.finditer(r"<meta[^>]+property=['\"]og:([^'\"]+)['\"][^>]+content=['\"]([^'\"]+)['\"]", html, re.I):
        tags[m.group(1)] = m.group(2)
    return tags


def _extract_twitter(html: str) -> str:
    m = re.search(r"<meta[^>]+name=['\"]twitter:card['\"][^>]+content=['\"]([^'\"]+)['\"]", html, re.I)
    return m.group(1) if m else ""


def _extract_lang(html: str) -> str:
    m = re.search(r"<html[^>]+lang=['\"]([^'\"]+)['\"]", html, re.I)
    return m.group(1) if m else ""


# ── On-page SEO score ─────────────────────────────────────────────────────────

def onpage_score(homepage: dict) -> dict:
    score = 0
    max_score = 100
    issues: list[dict] = []
    recommendations: list[str] = []

    def add(pts: int, issue: str, rec: str):
        nonlocal score
        issues.append({"severity": "error" if pts <= 0 else "warning", "item": issue})
        recommendations.append(rec)
        score += pts

    title_val = homepage.get("title", "")
    if not title_val:
        add(-15, "Missing <title> tag", "Add a descriptive title under 60 characters")
    elif len(title_val) > 60:
        add(-5, "Title too long", f"Keep title under 60 characters (current: {len(title_val)} chars)")
    else:
        score += 15

    if not homepage.get("meta_description"):
        add(-10, "Missing meta description", "Add a 150-160 char meta description")
    elif len(homepage.get("meta_description", "")) > 160:
        issues.append({"severity": "warning", "item": "Meta description too long", "detail": "Keep between 150-160 characters"})
    else:
        score += 10

    if not homepage.get("canonical"):
        add(-10, "Missing canonical tag", "Add a self-referencing canonical tag")
    else:
        score += 10

    og_keys = homepage.get("og_tags", {})
    missing_og = [k for k in ("title", "description", "url", "type") if k not in og_keys]
    if not og_keys:
        add(-10, "Missing Open Graph tags", "Add og:title, og:description, og:type, og:url")
    elif missing_og:
        add(-5, f"Incomplete Open Graph tags (missing: {', '.join(missing_og)})",
             "Ensure og:title, og:description, og:url, og:type are all present")
    else:
        score += 20  # OG tags are high-impact for social sharing and SEO

    if not homepage.get("twitter_card"):
        add(-5, "Missing Twitter card", "Add twitter:card meta tag")
    else:
        score += 10  # Twitter card is important for social distribution

    if not homepage.get("json_ld"):
        add(-10, "Missing JSON-LD structured data", "Add Schema.org Organization or WebSite structured data")
    else:
        score += 10

    if not homepage.get("has_h1"):
        add(-15, "Missing <h1> tag", "Add exactly one <h1> with your primary keyword")
    else:
        score += 15

    if not homepage.get("has_nav") or not homepage.get("has_main"):
        add(-5, "Missing semantic HTML (nav/main)", "Use <nav> for navigation and <main> for main content")
    else:
        score += 5

    lang = homepage.get("lang_attr", "")
    if lang and lang.startswith("en"):
        score += 5
    elif not lang:
        issues.append({"severity": "warning", "item": "Missing lang attribute on <html>", "detail": "Add lang='en' to <html> tag for clarity"})

    if homepage.get("word_count", 0) < 200:
        issues.append({"severity": "warning", "item": "Homepage content seems thin", "detail": f"~{homepage.get('word_count', 0)} words. Aim for 300+ words."})
        recommendations.append("Expand homepage content with clear value proposition, features, and use cases")

    score = max(0, min(100, score))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
    return {"score": score, "grade": grade, "issues": issues, "recommendations": recommendations}


# ── Keyword rank tracking (Collosus free tier) ────────────────────────────────

def _get_gsc_credentials():
    """Load GSC credentials from stored token file. Returns (refresh_token, client_id, client_secret) or None."""
    token_path = AGENTS_DIR / "gsc_token.json"
    if not token_path.exists():
        return None
    try:
        data = json.loads(token_path.read_text())
        return data.get("refresh_token"), data.get("client_id"), data.get("client_secret")
    except (json.JSONDecodeError, OSError):
        return None


def _refresh_gsc_access_token(refresh_token: str, client_id: str, client_secret: str) -> str | None:
    """Get a fresh GSC API access token from a refresh token."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        import google.oauth2.credentials
        creds = google.oauth2.credentials.Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
        )
        creds.refresh(Request())
        return creds.token
    except Exception:
        return None


def track_ranks(keywords: list[str]) -> dict:
    """Check keyword positions via Google Search Console API (free, OAuth-based).

    GSC only returns data for keywords your site already ranks for — it won't
    show arbitrary keyword positions. This is still the most accurate free data
    source since it comes directly from Google.

    Setup: Run `python3 gsc_auth.py` once to authorize.
    """
    results = {}

    creds = _get_gsc_credentials()
    if not creds:
        results["_note"] = "GSC not configured. Run `python3 agents/marketing/gsc_auth.py` to set up rank tracking."
        return results

    refresh_token, client_id, client_secret = creds
    access_token = _refresh_gsc_access_token(refresh_token, client_id, client_secret)
    if not access_token:
        results["_note"] = "GSC token refresh failed. Run `python3 agents/marketing/gsc_auth.py` to re-authorize."
        return results

    # GSC API: searchanalytics.query
    # We query for all keywords over the last 28 days
    import datetime as dt_module
    end_date = dt_module.date.today()
    start_date = end_date - dt_module.timedelta(days=28)

    payload = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["query"],
        "rowLimit": 1000,
    }

    # GSC site URL — must match the property type registered in GSC.
    # Bug fixed 2026-05-31: was using "sc-domain:" format which returned
    # silently-empty data because the GSC token was authorized for the
    # https:// URL-prefix property, not a domain property.
    site_url = f"https://{SITE}/"
    gsc_site = urllib.parse.quote(site_url, safe="")
    api_url = f"https://www.googleapis.com/webmasters/v3/sites/{gsc_site}/searchAnalytics/query"

    try:
        req = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "User-Agent": "RalphWorkflow/SEO/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = json.loads(e.read().decode())
        results["_note"] = f"GSC HTTP {e.code}: {err_body['error']['message']}"
        return results
    except Exception as exc:
        results["_note"] = f"GSC API error: {exc}"
        return results

    # Index known keywords by name for quick lookup
    keyword_data = {row["keys"][0]: row for row in data.get("rows", [])}

    for kw in keywords:
        if kw in keyword_data:
            row = keyword_data[kw]
            results[kw] = {
                "position": _avg_position_from_impressions(row),
                "impressions": row.get("impressions", 0),
                "clicks": row.get("clicks", 0),
                "ctr": round(row.get("ctr", 0) * 100, 2),
                "source": "GSC",
            }
        else:
            results[kw] = {"position": None, "impressions": 0, "clicks": 0, "source": "GSC"}

    # Include actual top queries from GSC (not just priority keywords).
    # Added 2026-05-31: priority keyword list didn't match actual search traffic,
    # so we surface real query data for decision-making.
    all_rows = sorted(data.get("rows", []), key=lambda r: r.get("impressions", 0), reverse=True)
    results["_top_queries"] = [
        {
            "query": r["keys"][0],
            "position": round(r.get("position", 0), 0),
            "impressions": r.get("impressions", 0),
            "clicks": r.get("clicks", 0),
            "ctr": round(r.get("ctr", 0) * 100, 1),
        }
        for r in all_rows[:20] if r.get("impressions", 0) > 0
    ]

    return results


def _avg_position_from_impressions(row: dict) -> int | None:
    """Estimate average position from GSC position data if available."""
    # GSC returns 'position' as the weighted average position by impressions
    pos = row.get("position")
    return round(pos, 0) if pos else None


# ── Backlink checks ───────────────────────────────────────────────────────────

def check_backlinks_google() -> dict:
    """Check backlinks via Google search (no API key needed, rate-limited)."""
    try:
        query = urllib.parse.quote(f"link:{SITE}")
        url = f"https://www.google.com/search?q={query}&num=5"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; SEOBot/1.0)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        # Parse result URLs
        links = re.findall(r'href="(https?://[^"&]+)"', html)
        # Filter to exclude google.com, youtube.com, etc.
        external = [l for l in links if SITE in l and "google" not in l]
        return {"count_approx": len(external) if external else 0, "note": "Google search approximation — set COLLOSUS_API_KEY for accurate data"}
    except Exception as exc:
        return {"count_approx": 0, "error": str(exc)}


def check_ahref_domain_rating() -> dict:
    """Free ahref domain rating check via page fetch."""
    try:
        url = f"https://ahrefs.com/site-info/{SITE}"
        status, body = http_get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if status == 200:
            dr_match = re.search(r'Domain Rating\s*(\d+)', body)
            return {"dr": int(dr_match.group(1)) if dr_match else None, "source": "ahrefs_free"}
        return {"dr": None, "status": status}
    except Exception as exc:
        return {"dr": None, "error": str(exc)}


# ── Competitor overview ───────────────────────────────────────────────────────

def competitor_overview(competitors: list[str]) -> dict:
    """Fetch homepage status for each competitor as a baseline."""
    results = {}
    for comp in competitors:
        status, body = http_get(f"https://{comp}" if not comp.startswith("http") else comp)
        results[comp] = {
            "status": status,
            "ok": status == 200,
            "title": _extract_title(body) if status == 200 else "",
        }
        time.sleep(0.2)
    return results


# ── SERP feature opportunities ────────────────────────────────────────────────

def serp_features_for_keyword(keyword: str) -> dict:
    """Check if our site appears in SERP features for priority keywords."""
    try:
        encoded = urllib.parse.quote(keyword)
        url = f"https://www.google.com/search?q={encoded}&num=5"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; SEOBot/1.0)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        our_position = None
        for i, m in enumerate(re.finditer(r'href="(https?://[^"&]+)"', html)):
            if SITE in m.group(1):
                our_position = i + 1
                break
        has_people_also_ask = "people also ask" in html.lower()
        has_related = "related:" in html.lower()
        return {
            "position": our_position,
            "people_also_ask": has_people_also_ask,
            "related_searches": has_related,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ── Content gap analysis ─────────────────────────────────────────────────────

def _fetch_sitemap_urls() -> list[str]:
    """Retrieve all URLs from the site sitemap (cached per run)."""
    # Per-run cache to avoid repeated HTTP calls
    if _fetch_sitemap_urls._cache is not None:
        return _fetch_sitemap_urls._cache
    urls: list[str] = []
    try:
        status, body = http_get(f"{SITE_URL}/sitemap.xml")
        if status == 200:
            urls = list(set(re.findall(r"<loc>\s*(https?://[^<]+)\s*</loc>", body, re.I)))
    except Exception:
        pass
    _fetch_sitemap_urls._cache = urls
    return urls
_fetch_sitemap_urls._cache = None


def _check_url_for_keywords(url: str, keywords_lower: list[str]) -> set[str]:
    """Fetch a URL and return which keywords appear in its content (title + meta + H1 + H2 + body text)."""
    found: set[str] = set()
    try:
        status, body = http_get(url)
        if status != 200:
            return found
        # Extract all searchable text: title, meta description, H1, H2, and first 8KB of body
        text_parts = [
            _extract_title(body),
            _extract_meta(body, "description"),
        ]
        for tag in ("h1", "h2"):
            for m in re.finditer(rf"<{tag}[^>]*>(.*?)</{tag}>", body, re.I | re.S):
                text_parts.append(m.group(1))
        # Strip HTML tags from body for keyword matching
        body_text = re.sub(r"<[^>]+>", " ", body[:8192])
        text_parts.append(body_text)
        combined = " ".join(text_parts).lower()
        for kw in keywords_lower:
            if kw in combined:
                found.add(kw)
    except Exception:
        pass
    return found


def content_gap_analysis(keywords: list[str], competitors: list[str]) -> dict:
    """Identify content gaps: keywords our site doesn't cover anywhere.

    Scans the homepage plus all sitemap URLs (blog posts, pages, docs) to
    determine which priority keywords are covered by at least one page.
    Falls back to homepage-only scan if sitemap is unreachable.
    """
    keywords_lower = [kw.lower() for kw in keywords]
    covered: set[str] = set()

    # Always scan homepage first (fast, always available)
    homepage_status, homepage_body = http_get(SITE_URL)
    if homepage_status == 200:
        title = _extract_title(homepage_body).lower()
        desc = _extract_meta(homepage_body, "description").lower()
        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", homepage_body, re.I | re.S)
        h1_text = h1_match.group(1).lower() if h1_match else ""
        combined = f"{title} {desc} {h1_text}"
        for kw in keywords_lower:
            if kw in combined:
                covered.add(kw)

    # Scan sitemap URLs — prioritize blog posts (highest keyword relevance), cap at 50 fetches
    sitemap_urls = _fetch_sitemap_urls()
    # Sort: blog URLs first (highest keyword density), then everything else
    sitemap_urls_sorted = sorted(sitemap_urls, key=lambda u: 0 if '/blog/' in u else 1)
    remaining = {kw for kw in keywords_lower if kw not in covered}
    scanned = 0
    for url in sitemap_urls_sorted[:50]:
        if not remaining:
            break
        found = _check_url_for_keywords(url, list(remaining))
        covered |= found
        remaining -= found
        scanned += 1

    gaps = [kw for kw in keywords if kw.lower() not in covered]
    covered_list = [kw for kw in keywords if kw.lower() in covered]
    coverage_pct = round(len(covered_list) / len(keywords) * 100, 1) if keywords else 0

    return {
        "gaps": gaps,
        "covered": covered_list,
        "coverage_pct": coverage_pct,
        "scan_method": "homepage+sitemap" if sitemap_urls else "homepage-only",
        "urls_scanned": 1 + scanned,
    }


# ── Write daily SEO report ────────────────────────────────────────────────────

def delta_issues(prev_issues: list[dict], curr_issues: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """Compare previous and current on-page issues.

    Returns (fixed, new, unchanged):
      - fixed: issues in prev but not in curr
      - new: issues in curr but not in prev
      - unchanged: issues present in both
    """
    def sig(issue: dict) -> tuple[str, str]:
        return (issue.get("item", ""), issue.get("severity", ""))

    prev_sigs = {sig(i) for i in prev_issues}
    curr_sigs = {sig(i) for i in curr_issues}

    fixed = [item for (item, _) in prev_sigs - curr_sigs]
    new = [item for (item, _) in curr_sigs - prev_sigs]
    unchanged = [item for (item, _) in prev_sigs & curr_sigs]
    return fixed, new, unchanged


def delta_metrics(prev: dict, curr: dict) -> dict:
    """Detect which SEO metrics regressed or improved vs previous report.

    Compares: onpage_score, backlinks_approx, ranked_keywords count,
    domain_rating, content_gap_pct.
    """
    regressed = {}
    improved = {}

    def num(v) -> float | None:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str) and "/" in v:
            try:
                return float(v.split("/")[0])
            except ValueError:
                return None
        return None

    def compare(key: str, prev_val, curr_val, higher_is_better: bool = True):
        p = num(prev_val)
        c = num(curr_val)
        if p is None or c is None or p == c:
            return
        if higher_is_better:
            if c < p:
                regressed[key] = {"prev": prev_val, "curr": curr_val, "delta": round(c - p, 2)}
            elif c > p:
                improved[key] = {"prev": prev_val, "curr": curr_val, "delta": round(c - p, 2)}
        else:
            if c > p:
                regressed[key] = {"prev": prev_val, "curr": curr_val, "delta": round(c - p, 2)}
            elif c < p:
                improved[key] = {"prev": prev_val, "curr": curr_val, "delta": round(c - p, 2)}

    compare("onpage_score", prev.get("onpage_score"), curr.get("onpage_score"), higher_is_better=True)
    compare("backlinks_approx",
            prev.get("backlinks", {}).get("count_approx", 0) if isinstance(prev.get("backlinks"), dict) else prev.get("backlinks_approx", 0),
            curr.get("backlinks", {}).get("count_approx", 0) if isinstance(curr.get("backlinks"), dict) else curr.get("backlinks_approx", 0),
            higher_is_better=True)  # More backlinks = better
    compare("ranked_keywords",
            sum(1 for v in prev.get("ranks", {}).values() if isinstance(v, dict) and v.get("position")),
            sum(1 for v in curr.get("ranks", {}).values() if isinstance(v, dict) and v.get("position")),
            higher_is_better=True)

    prev_dr = prev.get("domain_rating", {})
    curr_dr = curr.get("domain_rating", {})
    if isinstance(prev_dr, dict) and isinstance(curr_dr, dict):
        compare("domain_rating", prev_dr.get("dr"), curr_dr.get("dr"), higher_is_better=True)
    elif isinstance(prev_dr, (int, float)) and isinstance(curr_dr, (int, float)):
        compare("domain_rating", prev_dr, curr_dr, higher_is_better=True)

    prev_gap = prev.get("content_gap", {})
    curr_gap = curr.get("content_gap", {})
    if isinstance(prev_gap, dict) and isinstance(curr_gap, dict):
        compare("content_gap_pct", prev_gap.get("coverage_pct", 0), curr_gap.get("coverage_pct", 0), higher_is_better=True)

    return {"regressed": regressed, "improved": improved}


def load_previous_log(now: datetime) -> dict | None:
    """Load the most recent seo_YYYY-MM-DD.json log before `now`.

    Searches LOG_DIR for the most recent seo_*.json file dated before today.
    """
    candidates = []
    for f in LOG_DIR.glob("seo_*.json"):
        try:
            dt = datetime.fromisoformat(f.stem.replace("seo_", ""))
            if dt.date() < now.date():
                candidates.append((dt, f))
        except ValueError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    try:
        return json.loads(candidates[0][1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None



def write_daily_report(now: datetime, data: dict) -> Path:
    report_path = REPORTS_DIR / f"{now.strftime('%Y-%m-%d')}.md"

    # ── Retroactive analysis: compare vs previous report ────────────────────
    prev_log = load_previous_log(now)
    delta_section_lines = []
    if prev_log:
        prev_onpage = prev_log.get("onpage", {})
        curr_onpage = data.get("onpage", {})
        fixed, new_issues, unchanged = delta_issues(
            prev_onpage.get("issues", []),
            curr_onpage.get("issues", []),
        )
        metric_deltas = delta_metrics(prev_log, data)
        delta_section_lines.extend(["", "## Delta vs Previous Report"])
        if metric_deltas.get("regressed"):
            lines_regressed = []
            for key, info in metric_deltas["regressed"].items():
                lines_regressed.append(f"- 🔴 **{key}** went from {info['prev']} → {info['curr']}")
            delta_section_lines.append("### Regressed")
            delta_section_lines.extend(lines_regressed)
        if metric_deltas.get("improved"):
            lines_improved = []
            for key, info in metric_deltas["improved"].items():
                lines_improved.append(f"- ✅ **{key}** improved from {info['prev']} → {info['curr']}")
            delta_section_lines.append("### Improved")
            delta_section_lines.extend(lines_improved)
        if fixed:
            delta_section_lines.append(f"### Fixed Issues ({len(fixed)})")
            for item in fixed:
                delta_section_lines.append(f"- ✅ {item}")
        if new_issues:
            delta_section_lines.append(f"### New Issues ({len(new_issues)})")
            for item in new_issues:
                delta_section_lines.append(f"- 🔴 {item}")
        if not delta_section_lines or len(delta_section_lines) == 1:
            delta_section_lines.append("- No significant changes detected.")
        prev_date = prev_log.get("timestamp", "unknown")
        prev_date_str = prev_log.get("timestamp", "unknown")
        delta_section_lines.append("_Comparing to previous report (" + prev_date_str[:10] + ")_")
    else:
        delta_section_lines = ["", "## Delta vs Previous Report", "- No previous report found — this is the first run."]

    lines = [
        f"# SEO Report — RalphWorkflow — {now.strftime('%B %d, %Y')}",
        "",
        "## On-Page SEO Score",
        f"- **Score:** {data['onpage']['score']}/100 ({data['onpage']['grade']})",
    ]
    if data['onpage'].get('issues'):
        lines.append("### On-Page Issues")
        for issue in data['onpage']['issues']:
            sev = "🔴" if issue.get('severity') == 'error' else "🟡"
            detail = issue.get('detail', '')
            lines.append(f"- {sev} {issue['item']} {f'— {detail}' if detail else ''}")
    if data['onpage'].get('recommendations'):
        lines.append("### On-Page Recommendations")
        for rec in data['onpage']['recommendations']:
            lines.append(f"- {rec}")

    lines.extend(["", "## Site Health"])
    for name, status in data['site_health'].items():
        badge = "✅" if status.get("ok", status.get("status") == 200) else "❌"
        detail = status.get("status", status.get("error", "unknown"))
        lines.append(f"- {badge} {name}: {detail}")

    lines.extend(["", "## Sitemap"])
    # Sitemap info lives in site_health.sitemap, not at top level
    sm = data.get('site_health', {}).get('sitemap', data.get('sitemap', {}))
    lines.append(f"- Status: {sm.get('status', 'unknown')}")
    lines.append(f"- URLs in index: {sm.get('url_count', 0)}")

    lines.extend(["", "## Keyword Rankings"])
    ranks = data.get('ranks', {})
    if "_note" in ranks:
        lines.append(f"- _Note: {ranks['_note']}_")
    else:
        # GSC returns {keyword: {position, clicks, impressions, ctr, source}}
        ranked = [(kw, d) for kw, d in ranks.items()
                  if isinstance(d, dict) and d.get("position")]
        ranked.sort(key=lambda x: x[1].get("position") or 999)
        if ranked:
            for kw, d in ranked[:8]:
                pos = d.get("position", "N/A")
                clicks = d.get("clicks", 0)
                impressions = d.get("impressions", 0)
                ctr = d.get("ctr", "N/A")
                lines.append(f"- **{kw}**: pos {pos} | clicks: {clicks} | impr: {impressions} | CTR: {ctr}%")
        else:
            lines.append("- No ranking data in GSC yet (new domain or not indexed for these keywords).")
    
    # Surface actual search queries from GSC (not just priority keywords).
    # Added 2026-05-31: priority keyword list didn't match what people actually
    # search for. Real data helps calibrate content strategy.
    top_queries = ranks.get('_top_queries', [])
    if top_queries:
        lines.extend(["### Top Search Queries (Last 28 Days, GSC)"])
        for q in top_queries[:12]:
            lines.append(f"- **{q['query']}**: pos {q['position']}, {q['impressions']} impr, {q['clicks']} clicks ({q.get('ctr', q.get('ctr', 0))}% CTR)")

    lines.extend(["", "## Backlinks"])
    bl = data.get('backlinks', {})
    lines.append(f"- Approx count: {bl.get('count_approx', 'N/A')}")
    if bl.get('error'):
        lines.append(f"- Error: {bl['error']}")
    dr = data.get('domain_rating', {})
    if dr.get('dr'):
        lines.append(f"- Domain Rating (ahrefs): {dr['dr']}")

    lines.extend(["", "## Content Gap Analysis"])
    gap = data.get('content_gap', {})
    lines.append(f"- Keywords covered on homepage: {gap.get('coverage_pct', 0)}%")
    if gap.get('gaps'):
        lines.append("- **Gaps** (keywords not yet targeted on homepage):")
        for kw in gap['gaps'][:5]:
            lines.append(f"  - {kw}")

    lines.extend(["", "## SERP Features"])
    serp = data.get('serp', {})
    if serp.get('keyword'):
        lines.append(f"- **{serp['keyword']}**: position {serp.get('position', 'N/A')}, PAA: {serp.get('people_also_ask', False)}, related: {serp.get('related_searches', False)}")

    lines.extend(["", "## Competitors at a Glance"])
    for comp, info in data.get('competitors', {}).items():
        badge = "✅" if info.get("ok") else "❌"
        lines.append(f"- {badge} {comp}: {info.get('title', info.get('status', 'unknown'))}")

    lines.extend(["", "## Priority Actions"])
    actions = data.get('priority_actions', [])
    if actions:
        for i, action in enumerate(actions, 1):
            lines.append(f"{i}. {action}")
    else:
        lines.append("- Continue publishing targeted content. No critical SEO issues detected.")

    # Append delta section before writing
    lines.extend(delta_section_lines)
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    now = datetime.now()
    print(f"[seo_daily] Starting SEO collection for {now.strftime('%Y-%m-%d %H:%M')}", flush=True)

    # 1. Site health
    homepage = fetch_homepage()
    robots = fetch_robots_txt()
    sitemap = fetch_sitemap_index()
    site_health = {
        "homepage": homepage,
        "robots": {"status": robots["status"], "directive_count": len(robots["directives"]), "sitemap_url": robots["sitemap"]},
        "sitemap": sitemap,
    }

    # 2. On-page SEO score
    onpage = onpage_score(homepage)

    # 3. Keyword ranks
    ranks = track_ranks(PRIORITY_KEYWORDS)

    # 4. Backlinks
    backlinks = check_backlinks_google()
    dr = check_ahref_domain_rating()

    # 5. Competitors
    competitors = competitor_overview(COMPETITORS)

    # 6. Content gap
    content_gap = content_gap_analysis(PRIORITY_KEYWORDS, COMPETITORS)

    # 7. SERP feature check (just first keyword to avoid rate limits)
    serp_kw = PRIORITY_KEYWORDS[0] if PRIORITY_KEYWORDS else ""
    serp = serp_features_for_keyword(serp_kw) if serp_kw else {}
    if serp_kw:
        serp["keyword"] = serp_kw

    # 8. Derive priority actions
    priority_actions: list[str] = []
    if onpage["score"] < 75:
        priority_actions.append(f"Fix top on-page SEO issues (current score: {onpage['score']}/100)")
    if content_gap.get("gaps"):
        priority_actions.append(f"Create content targeting: {', '.join(content_gap['gaps'][:3])}")
    if ranks.get("_note"):
        priority_actions.append("Set up Google Search Console: python3 agents/marketing/gsc_auth.py")
    bl_count = backlinks.get("count_approx", 0)
    if bl_count == 0:
        priority_actions.append("Build backlinks: submit to directories, guest post, earn citations")

    data = {
        "timestamp": now.isoformat(),
        "site_health": site_health,
        "onpage": onpage,
        "ranks": ranks,
        "backlinks": backlinks,
        "domain_rating": dr,
        "competitors": competitors,
        "content_gap": content_gap,
        "serp": serp,
        "priority_actions": priority_actions,
    }

    # Write daily log
    log_file = LOG_DIR / f"seo_{now.strftime('%Y-%m-%d')}.json"
    log_file.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    # Write human-readable report
    report_path = write_daily_report(now, data)

    # Summary printout
    summary = {
        "timestamp": now.isoformat(),
        "onpage_score": f"{onpage['score']}/100 ({onpage['grade']})",
        "homepage_ok": homepage.get("ok", False),
        "sitemap_urls": sitemap.get("url_count", 0),
        "ranked_keywords": sum(1 for v in ranks.values() if isinstance(v, dict) and v.get("position")),
        "backlinks_approx": backlinks.get("count_approx", 0),
        "domain_rating": dr.get("dr"),
        "content_gap_pct": content_gap.get("coverage_pct", 0),
        "content_gap": content_gap,
        "priority_actions": priority_actions,
        "report": str(report_path),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
