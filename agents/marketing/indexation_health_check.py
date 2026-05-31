#!/usr/bin/env python3
"""
SEO Indexation Health — checks Google index coverage for ralphworkflow.com via GSC.

Runs daily at 05:30 CEST. Uses Google Search Console Search Analytics API
(OAuth, already working) to measure actual search presence — not the Indexing
API (which is not enabled in the GCP project).

Bug fixed 2026-05-31: was calling the disabled Google Indexing API which always
returned "api_not_enabled" and produced a 0.0% false-negative signal. The GSC
Search Analytics API confirmed 13 pages with search presence (339 impressions,
21 clicks over 28d) the whole time.

Strategy:
- Fetch live sitemap from ralphworkflow.com
- Query GSC Search Analytics for actual indexed pages (pages with impressions)
- Report gap: sitemap_urls - pages_with_search_presence = invisible content
- Escalate if >50% pages unindexed for >7 days
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

ROOT = Path("/home/mistlight/.openclaw/workspace")
LOG_DIR = ROOT / "agents/marketing/logs"
SEO_DIR = ROOT / "seo-reports"
SITEMAP_URL = "https://ralphworkflow.com/sitemap.xml"
DOMAIN = "ralphworkflow.com"

GSC_TOKEN_PATH = Path(__file__).parent / "gsc_token.json"

ESCALATION_PATH = LOG_DIR / "indexation_escalation.json"
STATUS_PATH = LOG_DIR / "indexation_health_latest.json"

NO_INDEX_THRESHOLD_PCT = 50  # escalate if >50% unindexed
NO_INDEX_THRESHOLD_DAYS = 7  # escalate if same gap persists 7+ days

SSL_CTX = ssl.create_default_context()


def fetch_sitemap_urls() -> list[str]:
    """Fetch and parse the live sitemap."""
    try:
        req = urllib.request.Request(SITEMAP_URL, headers={"User-Agent": "RalphWorkflow/1.0 (SEO-indexation-check)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except Exception as e:
        print(f"ERROR fetching sitemap: {e}")
        return []

    try:
        root = ElementTree.fromstring(body)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = [el.text.strip() for el in root.findall(".//sm:loc", ns) if el.text]
        # fallback: try without namespace
        if not urls:
            urls = [el.text.strip() for el in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc") if el.text]
        return urls
    except Exception as e:
        print(f"ERROR parsing sitemap: {e}")
        return []


# ── Google Search Console API (Search Analytics — working OAuth path) ──────

def _gsc_access_token() -> str | None:
    """Exchange GSC refresh_token for an access token. Returns None if unavailable."""
    if not GSC_TOKEN_PATH.exists():
        return None
    try:
        with open(GSC_TOKEN_PATH) as f:
            t = json.load(f)
        if not t.get('refresh_token') or not t.get('client_id'):
            return None
        data = urllib.parse.urlencode({
            'client_id': t['client_id'],
            'client_secret': t['client_secret'],
            'refresh_token': t['refresh_token'],
            'grant_type': 'refresh_token',
        }).encode()
        req = urllib.request.Request('https://oauth2.googleapis.com/token', data=data)
        with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
            tok = json.loads(resp.read())
            return tok.get('access_token')
    except Exception:
        return None


def _gsc_search_analytics(days: int = 28) -> dict[str, Any]:
    """Get search analytics (clicks, impressions, CTR, indexed pages) from GSC."""
    token = _gsc_access_token()
    if not token:
        return {"ok": False, "error": "No GSC access token available"}

    site_url = f"https://{DOMAIN}/"
    end_date = datetime.now(timezone.utc).date().isoformat()
    start_date = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()

    path = f'sites/{urllib.parse.quote(site_url, safe="")}/searchAnalytics/query'
    url = f'https://www.googleapis.com/webmasters/v3/{path}'

    body = json.dumps({
        'startDate': start_date,
        'endDate': end_date,
        'dimensions': ['page'],
        'rowLimit': 100,
    }).encode()

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': 'RalphWorkflow/IndexationHealth/1.0',
    }

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return {"ok": False, "error": json.loads(e.read()).get('error', str(e))}
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    rows = data.get('rows', [])
    result = {
        'ok': True,
        'total_clicks': sum(int(r.get('clicks', 0) or 0) for r in rows),
        'total_impressions': sum(int(r.get('impressions', 0) or 0) for r in rows),
        'pages_with_data': len(rows),
        'ctr': round(
            sum(int(r.get('clicks', 0) or 0) for r in rows) /
            max(sum(int(r.get('impressions', 0) or 0) for r in rows), 1) * 100, 2
        ),
        'top_pages': sorted(
            [{'url': r['keys'][0], 'impressions': int(r.get('impressions', 0) or 0),
              'clicks': int(r.get('clicks', 0) or 0)} for r in rows],
            key=lambda x: x['impressions'], reverse=True
        )[:20],
    }
    return result


def check_google_indexing(sitemap_urls: list[str]) -> dict[str, Any]:
    """
    Check Google index coverage via GSC Search Analytics (OAuth — working).

    Uses the same OAuth path as seo_indexation_diagnostic.py which was
    confirmed working on 2026-05-31 (13 pages with search presence,
    339 impressions, 21 clicks over 28 days).
    """
    analytics = _gsc_search_analytics()
    if not analytics.get('ok'):
        return {
            "status": "gsc_unavailable",
            "reason": analytics.get('error', 'unknown'),
            "indexed": 0,
            "total": len(sitemap_urls),
        }

    pages_with_data = analytics['pages_with_data']
    return {
        "status": "gsc_search_analytics",
        "pages_with_search_presence": pages_with_data,
        "total_urls": len(sitemap_urls),
        "total_impressions_28d": analytics['total_impressions'],
        "total_clicks_28d": analytics['total_clicks'],
        "ctr_28d": analytics['ctr'],
        "top_pages": analytics['top_pages'][:10],
        "estimated_indexed": pages_with_data,
    }


def build_report(sitemap_urls: list[str], google_result: dict) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    total = len(sitemap_urls)
    estimated_indexed = google_result.get("estimated_indexed", 0)

    pct_indexed = None
    pct_unindexed = None
    if google_result.get("status") == "gsc_search_analytics" and total > 0:
        pct_indexed = round(estimated_indexed / total * 100, 1)
        pct_unindexed = 100 - pct_indexed

    report = {
        "timestamp": now.isoformat(),
        "sitemap_urls_total": total,
        "google_indexing": google_result,
        "gap_unindexed": total - estimated_indexed,
        "pct_indexed_estimate": pct_indexed,
        "escalation_threshold": {
            "pct_unindexed_limit": NO_INDEX_THRESHOLD_PCT,
            "unindexed_pct": pct_unindexed,
            "triggered": pct_unindexed is not None and pct_unindexed > NO_INDEX_THRESHOLD_PCT,
        },
    }

    # Check escalation history
    if report["escalation_threshold"]["triggered"]:
        prior = load_prior_escalation()
        if prior:
            days_since = (now - datetime.fromisoformat(prior["first_detected"])).days
            report["escalation"] = {
                "first_detected": prior["first_detected"],
                "days_since": days_since,
                "chronic": days_since >= NO_INDEX_THRESHOLD_DAYS,
                "action": "escalate_to_human" if days_since >= NO_INDEX_THRESHOLD_DAYS else "monitoring",
            }
            if days_since >= NO_INDEX_THRESHOLD_DAYS and not prior.get("escalated"):
                save_escalation(report, escalated=True)
        else:
            save_escalation(report, escalated=False)

    return report


def load_prior_escalation() -> dict | None:
    if ESCALATION_PATH.exists():
        try:
            return json.loads(ESCALATION_PATH.read_text())
        except Exception:
            return None
    return None


def save_escalation(report: dict, escalated: bool) -> None:
    ESCALATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    ESCALATION_PATH.write_text(json.dumps({
        "first_detected": datetime.now(timezone.utc).isoformat(),
        "escalated": escalated,
        "latest_report": report["timestamp"],
        "pct_unindexed": report["pct_indexed_estimate"],
    }, indent=2) + "\n")


def main() -> int:
    print("=== SEO Indexation Diagnostic ===")
    print(f"Fetching sitemap from {SITEMAP_URL}...")
    urls = fetch_sitemap_urls()
    print(f"  Sitemap URLs: {len(urls)}")

    if not urls:
        print("  ERROR: Sitemap empty or unparseable — this IS the indexation problem.")
        return 1

    # Blog URLs
    blog_urls = [u for u in urls if "/blog/" in u]
    page_urls = [u for u in urls if "/blog/" not in u]
    print(f"  Blog posts: {len(blog_urls)}")
    print(f"  Pages: {len(page_urls)}")

    # Check Google search presence via GSC Search Analytics (working OAuth path)
    print("\nChecking Google search presence via GSC Search Analytics...")
    google = check_google_indexing(urls)
    if google.get("status") == "gsc_search_analytics":
        pages = google.get("pages_with_search_presence", 0)
        impr = google.get("total_impressions_28d", 0)
        clicks = google.get("total_clicks_28d", 0)
        print(f"  Google: {pages} pages with search presence ({impr} impressions, {clicks} clicks in 28d)")
    else:
        print(f"  Google: {google.get('status')} — {google.get('reason', 'unknown')}")

    # Build and save report
    report = build_report(urls, google)
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(report, indent=2) + "\n")

    # Also write a Markdown summary for human visibility
    md_path = SEO_DIR / f"indexation_health_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Indexation Health — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "",
        f"- **Sitemap URLs:** {len(urls)}",
        f"  - Blog posts: {len(blog_urls)}",
        f"  - Pages: {len(page_urls)}",
        f"- **Google indexing status:** {google.get('status')}",
    ]
    if google.get("status") == "gsc_search_analytics":
        pages = google.get("pages_with_search_presence", 0)
        pct = round(pages / max(len(urls), 1) * 100, 1)
        lines.append(f"- **Google search presence:** {pages}/{len(urls)} pages ({pct}%) — {google.get('total_impressions_28d', 0)} impressions, {google.get('total_clicks_28d', 0)} clicks (28d)")
        lines.append(f"- **CTR:** {google.get('ctr_28d', 0)}%")
    else:
        lines.append(f"- **Google status:** {google.get('status')} — {google.get('reason', 'unknown')}")

    if report.get("escalation"):
        esc = report["escalation"]
        lines.append(f"- **⚠️ ESCALATION:** {esc['days_since']} days above threshold — {'CHRONIC: needs human action' if esc.get('chronic') else 'monitoring'}")

    lines.append("")
    lines.append("## Actions")
    lines.append("- IndexNow ping runs daily at 05:00 CEST (Bing + Yandex + Seznam)")
    lines.append("- Google Indexing API: requires API enablement in GCP console")
    lines.append("- Sitemap is live at https://ralphworkflow.com/sitemap.xml (100 URLs)")
    md_path.write_text("\n".join(lines) + "\n")

    print(f"\nReport saved to {STATUS_PATH}")
    print(f"Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
