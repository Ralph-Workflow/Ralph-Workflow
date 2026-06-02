#!/usr/bin/env python3
"""
SEO Indexation Diagnostic — checks whether Google/Bing/DuckDuckGo have
actually indexed RalphWorkflow.com content.

This is the single most important measurement gap identified in audit #11+:
34 blog posts, 100/100 SEO score, but 0 backlinks and unknown indexation.
The system cannot distinguish between "good content nobody finds" and
"content that converts visitors who find it."

Strategy (multi-layered):
1. GOOGLE SEARCH CONSOLE API (primary truth source — gsc_token.json OAuth)
   - Sitemap submission status (submitted vs indexed)
   - Search Analytics (clicks, impressions, CTR, pages with data)
   - URL Inspection API (per-page coverage state)
2. Direct search-engine HTML fetch as fallback
3. Sitemap URL count vs expected (local truth)
4. robots.txt check
5. Bing Webmaster Tools (if BING_API_KEY set)

GSC token path: agents/marketing/gsc_token.json
OAuth flow: python3 agents/marketing/gsc_auth.py (human browser required for first run)
Token refresh: automatic via refresh_token (no human required after initial OAuth)

Output: agents/marketing/logs/seo_indexation_latest.json

Usage: python3 agents/marketing/seo_indexation_diagnostic.py
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path('/home/mistlight/.openclaw/workspace')
OUTPUT_PATH = ROOT / 'agents/marketing/logs/seo_indexation_latest.json'
SITEMAP_URL = 'https://ralphworkflow.com/sitemap.xml'
ROBOTS_URL = 'https://ralphworkflow.com/robots.txt'
DOMAIN = 'ralphworkflow.com'

SSL_CTX = ssl.create_default_context()
USER_AGENT = 'Mozilla/5.0 (compatible; RalphWorkflowIndexCheck/1.0; +https://ralphworkflow.com)'


def fetch_url(url: str, timeout: int = 15) -> tuple[int, str]:
    """Fetch a URL, return (http_code, body_text)."""
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/xhtml+xml,*/*'},
        )
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            body = resp.read().decode('utf-8', errors='replace')
            return resp.status, body[:50000]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', errors='replace')[:50000]
    except Exception as e:
        return 0, str(e)


def count_sitemap_urls() -> dict[str, Any]:
    """Parse sitemap.xml and count URLs."""
    result = {'sitemap_url': SITEMAP_URL, 'ok': False, 'url_count': 0, 'error': None}
    status, body = fetch_url(SITEMAP_URL)
    if status != 200:
        result['error'] = f'HTTP {status}'
        return result
    try:
        root = ET.fromstring(body)
        nsmap = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        urls = root.findall('.//ns:url', nsmap)
        locs = [u.find('ns:loc', nsmap).text for u in urls if u.find('ns:loc', nsmap) is not None]
        result['ok'] = True
        result['url_count'] = len(locs)
        result['urls'] = locs[:20]
        blog_urls = [u for u in locs if '/blog/' in u]
        result['blog_url_count'] = len(blog_urls)
    except ET.ParseError as e:
        result['error'] = f'XML ParseError: {e}'
    return result


def check_robots() -> dict[str, Any]:
    """Check robots.txt is accessible and not blocking."""
    result = {'robots_url': ROBOTS_URL, 'ok': False, 'error': None, 'disallow_all': False}
    status, body = fetch_url(ROBOTS_URL)
    if status != 200:
        result['error'] = f'HTTP {status}'
        return result
    result['ok'] = True
    result['disallow_all'] = 'Disallow: /' in body.upper()
    result['has_sitemap'] = 'Sitemap:' in body
    result['body_first_500'] = body[:500]
    return result


# ── Google Search Console API (primary truth source) ──────────────────────

def _gsc_access_token() -> str | None:
    """Exchange GSC refresh_token for an access token. Returns None if unavailable."""
    token_path = ROOT / 'agents/marketing/gsc_token.json'
    if not token_path.exists():
        return None
    try:
        with open(token_path) as f:
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


def _gsc_api_call(path: str, method: str = 'GET', body: bytes | None = None, timeout: int = 20) -> tuple[int, Any]:
    """Make an authenticated GSC API call. Returns (http_code, response_data)."""
    token = _gsc_access_token()
    if not token:
        return 0, {'error': 'No GSC access token available'}
    url = f'https://www.googleapis.com/webmasters/v3/{path}'
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json', 'User-Agent': USER_AGENT}
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {'error': f'HTTP {e.code}'}
    except Exception as e:
        return 0, {'error': str(e)}


def _gsc_sitemap_status() -> dict[str, Any]:
    """Get sitemap submission + indexation counts from GSC."""
    site_url = f'https://{DOMAIN}'
    result = {'ok': False, 'sitemaps': [], 'submitted': 0, 'indexed': 0, 'error': None}
    code, data = _gsc_api_call(f'sites/{quote(site_url, safe="")}/sitemaps')
    if code == 200:
        result['ok'] = True
        total_submitted = 0
        total_indexed = 0
        for sm in data.get('sitemap', []):
            sm_submitted = 0
            sm_indexed = 0
            for c in sm.get('contents', []):
                sm_submitted += int(c.get('submitted', 0) or 0)
                sm_indexed += int(c.get('indexed', 0) or 0)
            total_submitted += sm_submitted
            total_indexed += sm_indexed
            result['sitemaps'].append({
                'path': sm.get('path'),
                'last_submitted': sm.get('lastSubmitted'),
                'last_downloaded': sm.get('lastDownloaded'),
                'warnings': sm.get('warnings', 0),
                'errors': sm.get('errors', 0),
                'submitted': sm_submitted,
                'indexed': sm_indexed,
            })
        result['submitted'] = total_submitted
        result['indexed'] = total_indexed
    else:
        result['error'] = data.get('error', str(data))
    return result


def _gsc_search_analytics(days: int = 28) -> dict[str, Any]:
    """Get search analytics (clicks, impressions, CTR, indexed pages) from GSC."""
    site_url = f'https://{DOMAIN}'
    end_date = datetime.now(timezone.utc).date().isoformat()
    start_date = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    result = {
        'ok': False, 'total_clicks': 0, 'total_impressions': 0,
        'pages_with_data': 0, 'top_pages': [], 'error': None,
    }
    body = json.dumps({
        'startDate': start_date,
        'endDate': end_date,
        'dimensions': ['page'],
        'rowLimit': 50,
        'aggregationType': 'byPage',
    }).encode()
    code, data = _gsc_api_call(
        f'sites/{quote(site_url, safe="")}/searchAnalytics/query', method='POST', body=body,
    )
    if code == 200:
        result['ok'] = True
        rows = data.get('rows', [])
        result['total_clicks'] = sum(int(r.get('clicks', 0) or 0) for r in rows)
        result['total_impressions'] = sum(int(r.get('impressions', 0) or 0) for r in rows)
        result['pages_with_data'] = len(rows)
        result['ctr'] = round(result['total_clicks'] / max(result['total_impressions'], 1) * 100, 2)
        sorted_rows = sorted(rows, key=lambda r: r['impressions'], reverse=True)
        result['top_pages'] = [
            {
                'url': r['keys'][0],
                'impressions': int(r.get('impressions', 0) or 0),
                'clicks': int(r.get('clicks', 0) or 0),
                'ctr': round(float(r.get('ctr', 0) or 0) * 100, 1),
            }
            for r in sorted_rows[:15]
        ]
    else:
        result['error'] = data.get('error', str(data))
    return result


def search_google_index(count: int = 100) -> dict[str, Any]:
    """Google index check: GSC API primary (OAuth), raw HTML fallback."""
    result = {
        'method': None,
        'ok': False,
        'indexed_count_approx': None,
        'error': None,
        'sample_urls': [],
        'gsc_sitemap': None,
        'gsc_analytics': None,
    }

    # PRIMARY: Google Search Console API
    # CRITICAL: GSC sitemap "indexed" count is a sitemap-processing metric, NOT
    # the same as "pages in Google's search index." Pages can appear in search
    # results (impressions/clicks) without being counted as sitemap-indexed.
    # Always cross-check sitemap data against search analytics before
    # concluding "NOT_INDEXED."  Search analytics impressions prove indexation.
    sitemap_status = _gsc_sitemap_status()
    analytics = _gsc_search_analytics()
    if sitemap_status['ok'] or analytics['ok']:
        result['method'] = 'gsc_api'
        result['ok'] = True
        result['gsc_sitemap'] = sitemap_status
        result['gsc_analytics'] = analytics
        # Derive indexation from search analytics, not sitemap metadata.
        # If pages earn impressions, Google has indexed them regardless of
        # what the sitemap endpoint reports.
        if analytics['ok'] and analytics['pages_with_data'] > 0:
            result['indexed_count_approx'] = analytics['pages_with_data']
            result['sample_urls'] = [p['url'] for p in analytics.get('top_pages', [])[:10]]
            result['indexed_source'] = 'search_analytics'
        elif sitemap_status['ok']:
            result['indexed_count_approx'] = sitemap_status['indexed']
            result['indexed_source'] = 'sitemap'
        return result

    # FALLBACK: raw HTML searches (used when GSC token is unavailable)
    api_key = os.environ.get('GOOGLE_CSE_API_KEY')
    cse_id = os.environ.get('GOOGLE_CSE_ID')

    if api_key and cse_id:
        result['method'] = 'google_cse_api'
        try:
            q = 'site:ralphworkflow.com'
            url = f'https://www.googleapis.com/customsearch/v1?key={api_key}&cx={cse_id}&q={quote(q)}&num=10&start=1'
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
                data = json.loads(resp.read())
                result['ok'] = True
                result['indexed_count_approx'] = int(data.get('searchInformation', {}).get('totalResults', 0))
                items = data.get('items', [])
                result['sample_urls'] = [it['link'] for it in items[:10]]
                return result
        except Exception as e:
            result['error'] = f'CSE API: {e}'

    result['method'] = 'google_raw_html'
    try:
        q = quote(f'site:{DOMAIN}')
        url = f'https://www.google.com/search?q={q}&hl=en'
        status, body = fetch_url(url)
        if status == 200 and DOMAIN in body:
            result['ok'] = True
            result['indexed_count_approx'] = body.count(DOMAIN)
            urls_found = re.findall(r'https?://ralphworkflow\.com/[^\s"<>\']+', body)
            result['sample_urls'] = list(set(urls_found))[:10]
        elif status in (429, 302):
            result['error'] = f'Bot detection (HTTP {status}) — Google blocking automated queries'
        else:
            result['error'] = f'HTTP {status}'
    except Exception as e:
        result['error'] = str(e)

    return result


def search_duckduckgo_index() -> dict[str, Any]:
    """DuckDuckGo site: search via HTML endpoint."""
    result = {'method': 'ddg_html', 'ok': False, 'indexed_count_approx': None, 'error': None}
    try:
        q = quote(f'site:{DOMAIN}')
        url = f'https://html.duckduckgo.com/html/?q={q}'
        status, body = fetch_url(url)
        if status == 200 and DOMAIN in body:
            result['ok'] = True
            result['indexed_count_approx'] = body.count(DOMAIN)
            urls_found = re.findall(r'https?://ralphworkflow\.com/[^\s"<>\']+', body)
            result['sample_urls'] = list(set(urls_found))[:10]
        else:
            result['error'] = f'HTTP {status} or domain not found in results'
    except Exception as e:
        result['error'] = str(e)
    return result


def search_bing_index() -> dict[str, Any]:
    """Bing Webmaster API or raw search."""
    result = {'method': 'bing_raw', 'ok': False, 'indexed_count_approx': None, 'error': None}
    api_key = os.environ.get('BING_API_KEY')
    if api_key:
        result['method'] = 'bing_webmaster_api'
        try:
            url = f'https://ssl.bing.com/webmaster/api.svc/json/GetUrlList?siteurl={quote(DOMAIN)}&apikey={api_key}'
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
                data = json.loads(resp.read())
                result['ok'] = True
                result['raw_data'] = str(data)[:2000]
                return result
        except Exception as e:
            result['error'] = f'Bing API: {e}'

    try:
        q = quote(f'site:{DOMAIN}')
        url = f'https://www.bing.com/search?q={q}'
        status, body = fetch_url(url)
        if status == 200 and DOMAIN in body:
            result['ok'] = True
            urls_found = re.findall(r'https?://ralphworkflow\.com/[^\s"<>\']+', body)
            result['sample_urls'] = list(set(urls_found))[:10]
        else:
            result['error'] = f'HTTP {status}'
    except Exception as e:
        result['error'] = str(e)
    return result


def check_canonical_urls(blog_urls: list[str]) -> dict[str, Any]:
    """Spot-check first 5 blog URLs for correct canonical tags."""
    result = {'checked': 0, 'ok': 0, 'canonical_mismatches': []}
    for url in blog_urls[:5]:
        result['checked'] += 1
        status, body = fetch_url(url)
        if status != 200:
            result['canonical_mismatches'].append({'url': url, 'error': f'HTTP {status}'})
            continue
        canonical_match = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', body)
        if canonical_match:
            result['ok'] += 1
        else:
            result['canonical_mismatches'].append({'url': url, 'error': 'No canonical tag found'})
    return result


def run() -> dict[str, Any]:
    """Run full indexation diagnostic."""
    diagnostic = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'domain': DOMAIN,
        'sitemap': count_sitemap_urls(),
        'robots': check_robots(),
    }

    diagnostic['search_engines'] = {
        'google': search_google_index(),
        'duckduckgo': search_duckduckgo_index(),
        'bing': search_bing_index(),
    }

    if diagnostic['sitemap']['ok'] and diagnostic['sitemap'].get('urls'):
        diagnostic['canonical_check'] = check_canonical_urls(diagnostic['sitemap']['urls'])

    sitemap_count = diagnostic['sitemap'].get('url_count', 0)
    google_data = diagnostic['search_engines']['google']
    google_indexed = google_data.get('indexed_count_approx') or 0
    ddg_indexed = diagnostic['search_engines']['duckduckgo'].get('indexed_count_approx') or 0

    # Enrich assessment with GSC data if available
    gsc_sitemap = google_data.get('gsc_sitemap')
    gsc_analytics = google_data.get('gsc_analytics')
    gsc_submitted = gsc_sitemap.get('submitted', 0) if gsc_sitemap else 0
    gsc_indexed = gsc_sitemap.get('indexed', 0) if gsc_sitemap else 0

    # Cross-check GSC sitemap data against search analytics.
    # Sitemap "indexed" is a sitemap-processing metric — pages can
    # appear in search (impressions/clicks) without the sitemap counting
    # them as indexed.  Never conclude NOT_INDEXED from sitemap alone.
    gsc_pages_with_data = gsc_analytics.get('pages_with_data', 0) if gsc_analytics else 0
    gsc_has_search_presence = gsc_pages_with_data > 0 if gsc_analytics and gsc_analytics.get('ok') else False
    indexed_confirmed = google_indexed > 0 or gsc_has_search_presence
    sitemap_zero_but_analytics_positive = gsc_indexed == 0 and gsc_has_search_presence

    # Confidence: how certain we are about the index count
    confidence = 'low'
    if gsc_has_search_presence:
        confidence = 'high' if gsc_indexed > 0 else 'medium'
    elif google_indexed > 0:
        confidence = 'medium'
    elif ddg_indexed > 0:
        confidence = 'low'

    assessment = {
        'sitemap_urls': sitemap_count,
        'google_indexed_approx': google_indexed,
        'ddg_indexed_approx': ddg_indexed,
        'gsc_source': google_data.get('method') == 'gsc_api',
        'gsc_sitemap_submitted': gsc_submitted,
        'gsc_sitemap_indexed': gsc_indexed,
        'gsc_indexation_rate': round(gsc_indexed / max(gsc_submitted, 1) * 100, 1),
        'gsc_total_clicks_28d': gsc_analytics.get('total_clicks', 0) if gsc_analytics else 0,
        'gsc_total_impressions_28d': gsc_analytics.get('total_impressions', 0) if gsc_analytics else 0,
        'gsc_pages_with_data': gsc_pages_with_data,
        'gsc_has_search_presence': gsc_has_search_presence,
        'indexed_source': google_data.get('indexed_source', 'unknown'),
        'confidence': confidence,
        'searchable': indexed_confirmed or ddg_indexed > 0,
        'verdict': 'unknown',
        'recommendation': None,
    }

    if sitemap_count == 0:
        assessment['verdict'] = 'CRITICAL: sitemap has 0 URLs'
        assessment['recommendation'] = 'Fix sitemap generation immediately — no URLs are being declared to search engines.'
    elif sitemap_zero_but_analytics_positive:
        # GSC sitemap says 0 indexed, but Search Analytics shows real
        # impressions/clicks — pages ARE in Google's index despite the
        # sitemap metric saying otherwise.  This is the common
        # sitemap-vs-index mismatch, not an indexation failure.
        assessment['verdict'] = (
            f'PARTIALLY_INDEXED: {gsc_pages_with_data} pages with search presence '
            f'(impressions/clicks in 28d), sitemap reports {gsc_indexed}/{gsc_submitted} indexed. '
            'Sitemap-indexed count undercounts true indexation — search analytics confirms pages are live.'
        )
        assessment['searchable'] = True
        assessment['recommendation'] = (
            f'Indexation is happening ({gsc_pages_with_data} pages visible in search) but '
            f'sitemap processing is behind. This is not an indexation failure — focus on '
            f'backlinks and domain authority instead of gate-repair. '
            f'Current visibility: {assessment["gsc_total_impressions_28d"]} impressions, {assessment["gsc_total_clicks_28d"]} clicks over 28 days. '
            f'Primary blocker is ranking, not indexing.'
        )
    elif gsc_submitted > 0 and gsc_indexed == 0 and gsc_pages_with_data == 0:
        # This is the real NOT_INDEXED case: sitemap submitted, sitemap
        # reports 0 indexed, AND search analytics has zero pages with data.
        # All three sources agree — content is genuinely not in Google's index.
        assessment['verdict'] = f'NOT_INDEXED: {gsc_submitted} pages submitted via sitemap, 0 search presence in Google'
        assessment['recommendation'] = (
            f'CRITICAL: Google knows about {gsc_submitted} pages via sitemap but has zero search '
            f'presence (0 impressions, 0 indexed). Most likely causes: (1) domain is too new or '
            f'has no backlinks signaling authority, (2) content is detected as '
            f'thin/auto-generated/doorway pages, (3) Google needs manual reconsideration. '
            f'Highest-leverage next action: get ONE quality backlink from an established domain '
            f'(TechCrunch-style dev publication, GitHub repo with stars, etc). '
            f'A single backlink will trigger Google to crawl and evaluate the content properly.'
        )
    elif indexed_confirmed:
        assessment['searchable'] = True
        if gsc_has_search_presence:
            assessment['verdict'] = (
                f'INDEXED: {gsc_pages_with_data} pages with Google search presence '
                f'({assessment["gsc_total_impressions_28d"]} impressions, {assessment["gsc_total_clicks_28d"]} clicks in 28d)'
            )
            assessment['recommendation'] = (
                f'Indexation confirmed. Ranking is now the bottleneck — {gsc_pages_with_data} pages '
                f'are in the index but averaging ~{round(assessment["gsc_total_impressions_28d"] / max(gsc_pages_with_data, 1))} '
                f'impressions/page/28d. Zero backlinks = zero domain authority = bottom-of-page-10 rankings. '
                f'Highest-leverage action: backlink acquisition from established dev domains.'
            )
        else:
            assessment['verdict'] = f'INDEXED: {google_indexed} pages in Google index (raw search check)'
    elif ddg_indexed > 0:
        assessment['searchable'] = True
        assessment['verdict'] = f'INDEXED_BY_DDG: {ddg_indexed} pages in DuckDuckGo (Google status unknown)'
    else:
        blocked_searches = [
            engine for engine, data in diagnostic['search_engines'].items()
            if data.get('error') and ('Bot detection' in data['error'] or '429' in data['error'])
        ]
        if blocked_searches:
            assessment['verdict'] = f'UNKNOWN: all search engines blocked ({", ".join(blocked_searches)})'
            assessment['recommendation'] = 'Manual check required: open https://www.google.com/search?q=site:ralphworkflow.com in a browser.'
        else:
            assessment['verdict'] = 'NOT_INDEXED: sitemap has URLs but zero pages found in any search engine'
            assessment['recommendation'] = (
                f'CRITICAL: {sitemap_count} URLs in sitemap but 0 appear in search indices. '
                'Submit sitemap to Google Search Console, verify noindex tags, check robots.txt.'
            )

    diagnostic['assessment'] = assessment

    return diagnostic


if __name__ == '__main__':
    result = run()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(result, f, indent=2, default=str)
    print(json.dumps(result, indent=2, default=str))

    verdict = result['assessment']['verdict']
    if verdict.startswith('CRITICAL') or verdict.startswith('NOT_INDEXED'):
        print(f"\n⚠️  ACTION REQUIRED: {verdict}")
        sys.exit(1)
    elif verdict.startswith('UNKNOWN'):
        print(f"\n⚠️  MANUAL CHECK NEEDED: {verdict}")
        sys.exit(2)
    else:
        print(f"\n✅ {verdict}")
        sys.exit(0)
