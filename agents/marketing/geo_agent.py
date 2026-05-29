#!/usr/bin/env python3
"""
geo_agent.py — Generative Engine Optimization (GEO) agent loop.

Tracks whether ralphworkflow.com and its content are cited by AI search engines
(ChatGPT Search, Perplexity, Google AI Overviews, Claude with web, Phind, Bing Copilot).
Implements the GEO content and technical requirements:
  1. Authoritative entity signals (JSON-LD schema per page type)
  2. Atomic knowledge blocks (direct-answer-first structure)
  3. Quantitative data density (stats, benchmarks, percentages)
  4. AI bot access (robots.txt allow all major AI crawlers)
  5. Semantic HTML (clean section/article/aside hierarchy)
  6. Citation tracking — does AI actually cite us?

Run via cron or manually:
  python3 agents/marketing/geo_agent.py
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
OUT_JSON = LOG_DIR / 'geo_agent_latest.json'
OUT_MD = LOG_DIR / 'geo_agent_latest.md'
SEO_DIR = ROOT / 'seo-reports'
RALPH_SITE = ROOT / 'Ralph-Site'
RALPH_DOMAIN = 'ralphworkflow.com'

# ── AI Search bots to allow in robots.txt ────────────────────────────────────
AI_BOTS = [
    'GPTBot',           # ChatGPT
    'ChatGPT-User',     # ChatGPT
    'PerplexityBot',    # Perplexity
    'Google-Extended',  # Google AI Overviews / Gemini
    'Bytespider',       # ByteDance
    'Amazonbot',        # Amazon
    'FacebookBot',      # Meta
    'anthropic-ai',     # Claude (web)
    'CCBot',            # Common Crawl
    'DataForSeoBot',    # DataForSEO
    'PetalBot',         # Microsoft
    'KrbBot',           # Korb
    'YouBot',           # You.com
    'YandexBot',        # Yandex (for completeness)
]

# ── Page-type → schema type mapping ───────────────────────────────────────────
SCHEMA_TYPES = {
    'blog': 'BlogPosting',
    'comparison': 'Article',
    'docs': 'TechArticle',
    'homepage': 'WebSite',
    'default': 'Article',
}

# ── GEO content requirements per page ─────────────────────────────────────────
GEO_MIN_WORDS = 300       # AI ignores pages under this
GEO_ATOMIC_SECTIONS = 3   # must start each H2 with a direct answer sentence
GEO_STATS_PER_PAGE = 2    # quantitative facts needed for citation
GEO_EXTERNAL_LINKS = 3    # authority signals require outbound links


def load_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def http_get(url, timeout=10):
    """Light fetch via curl — no external deps."""
    try:
        cmd = ['curl', '-s', '--max-time', str(timeout), '-L', '-A',
               'Mozilla/5.0 (compatible; GEO-audit/1.0)', url]
        out = subprocess.check_output(cmd, text=True)
        return out
    except Exception:
        return ''


def fetch_robots_txt(domain):
    """Check if AI bots are allowed."""
    url = f'https://{domain}/robots.txt'
    text = http_get(url)
    allowed = []
    blocked = []
    for line in text.split('\n'):
        line = line.strip()
        if line.lower().startswith('user-agent:'):
            bot = line.split(':', 1)[1].strip()
        elif line.lower().startswith('disallow:'):
            path = line.split(':', 1)[1].strip()
            if bot:
                blocked.append(bot)
        elif line.lower().startswith('allow:'):
            path = line.split(':', 1)[1].strip()
            if bot:
                allowed.append(bot)
    return {'allowed': allowed, 'blocked': blocked, 'raw': text[:1000]}


def check_ai_citation(domain='ralphworkflow.com'):
    """
    Simulate AI citation check by looking at:
    1. Whether the site is referenced in AI-visible sources
    2. Perplexity/ChatGPT search results for branded queries
    Uses available tools — real citation tracking requires paid APIs.
    """
    # Branded queries that AI engines typically surface
    branded_queries = [
        'Ralph Workflow AI agent orchestrator',
        'ralphworkflow.com',
        'Ralph Workflow vs Claude Code',
        'open source AI agent orchestration CLI',
        'Perplexity AI coding workflow',
        'agentic loop CLI tool',
    ]
    results = []
    for q in branded_queries:
        results.append({'query': q, 'cited': False, 'note': 'requires-perplexity-api'})
    return results


def audit_blog_post(path: Path) -> dict:
    """Audit a single blog post for GEO compliance."""
    text = path.read_text(errors='ignore') if path.exists() else ''
    html = 'html' in path.name or 'md' not in str(path)

    # Basic word count
    words = len(text.split())

    # Check for quantitative stats (percentage, number, ratio patterns)
    stat_pattern = re.compile(
        r'\b\d+(?:\.\d+)?%|\b\d+(?:\.\d+)?x\b|\b\d+(?:,\d{3})+(?:\.\d+)?\b|'
        r'\b\d+\s*(?:times|faster|better|worse|slower|higher|lower)\b|'
        r'\b(?:reduce|improve|increase|decrease|boost|achieve|reach|deliver)\s+\w+\s+by\s+\d+%?',
        re.I
    )
    stats = stat_pattern.findall(text)

    # Check atomic block: H2 followed by direct answer sentence
    # Count sections that open with a direct answer (sentence starting with noun/verb, not fluff)
    atomic_lead_pattern = re.compile(r'^#{1,3}\s+[A-Z][a-zA-Z].*?(?:\n\n|\n#{1,3})', re.M)
    h2s = re.findall(r'^##\s+.+', text, re.M)
    direct_answers = []
    for h2 in h2s:
        idx = text.index(h2) + len(h2)
        next_chunk = text[idx:idx+300]
        first_sentence = re.split(r'[.!?]', next_chunk)[0].strip() if next_chunk else ''
        if first_sentence and len(first_sentence) > 20:
            # Direct answer if it starts with a definition/verb/noun pattern
            if re.match(r'^(Ralph|AI|Agent|Orchestrat|An|LLM|Model|Tool|Workflow|A\s+\w)',
                        first_sentence, re.I):
                direct_answers.append(h2)

    # External links (authority signals)
    external_links = re.findall(r'https?://(?!' + re.escape(RALPH_DOMAIN) + r')', text)
    external_links = [l for l in external_links if l.startswith('http')]

    # Schema.org markup check (for HTML pages)
    has_jsonld = bool(re.search(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>', text))

    # Heading hierarchy
    h1s = re.findall(r'^#\s+.+', text, re.M)
    h2s_count = len(h2s)

    return {
        'path': str(path),
        'words': words,
        'stat_count': len(stats),
        'stats_preview': stats[:3],
        'atomic_sections': len(direct_answers),
        'h2_total': h2s_count,
        'external_links': len(external_links),
        'has_jsonld': has_jsonld,
        'h1s': h1s[:1],
        'geo_compliant': (
            words >= GEO_MIN_WORDS and
            len(direct_answers) >= GEO_ATOMIC_SECTIONS and
            len(stats) >= GEO_STATS_PER_PAGE and
            len(external_links) >= GEO_EXTERNAL_LINKS
        ),
        'failures': {
            'thin_content': words < GEO_MIN_WORDS,
            'no_atomic_blocks': len(direct_answers) < GEO_ATOMIC_SECTIONS,
            'no_quantitative_stats': len(stats) < GEO_STATS_PER_PAGE,
            'no_outbound_links': len(external_links) < GEO_EXTERNAL_LINKS,
            'missing_jsonld': not has_jsonld,
        }
    }


def audit_site_for_geo():
    """Full site audit for GEO readiness."""
    blog_dir = RALPH_SITE / 'content' / 'blog'
    pages_audited = []
    geo_failing = []
    geo_passing = []

    if blog_dir.exists():
        for md in sorted(blog_dir.glob('*.md')):
            audit = audit_blog_post(md)
            pages_audited.append(audit)
            if audit['geo_compliant']:
                geo_passing.append(audit['path'])
            else:
                geo_failing.append(audit['path'])

    return pages_audited, geo_passing, geo_failing


def generate_geo_recommendations(audits: list) -> list[dict]:
    """Generate actionable GEO recommendations per page."""
    recs = []
    for audit in audits:
        failures = [k for k, v in audit['failures'].items() if v]
        page_recs = []
        if 'thin_content' in failures:
            page_recs.append('Add more substantive content — AI engines skip pages under 300 words')
        if 'no_atomic_blocks' in failures:
            page_recs.append('Start each H2 section with a direct answer sentence (definition or fact, not fluff)')
        if 'no_quantitative_stats' in failures:
            page_recs.append('Add quantitative data: benchmarks, percentages, time comparisons, ratios')
        if 'no_outbound_links' in failures:
            page_recs.append('Add 3+ outbound links to authoritative sources (specs, research, official docs)')
        if 'missing_jsonld' in failures:
            page_recs.append('Add JSON-LD schema markup for the page type (BlogPosting, TechArticle, etc.)')
        recs.append({
            'path': audit['path'],
            'failures': failures,
            'recommendations': page_recs,
        })
    return recs


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    print(f"[geo_agent] Running GEO audit at {now}")

    # 1. Check AI bot access in robots.txt
    robots = fetch_robots_txt(RALPH_DOMAIN)
    missing_bots = [b for b in AI_BOTS if b not in robots.get('allowed', [])]
    robots_fixing_needed = len(missing_bots) > 0

    # 2. Site content audit
    pages_audited, geo_passing, geo_failing = audit_site_for_geo()

    # 3. Generate recommendations
    recommendations = generate_geo_recommendations(pages_audited)

    # 4. AI citation check (simulated — needs real API for production)
    ai_citations = check_ai_citation(RALPH_DOMAIN)

    # 5. Summary scores
    total_pages = len(pages_audited)
    geo_pass_rate = round(len(geo_passing) / total_pages * 100) if total_pages else 0

    payload = {
        'generated_at': now,
        'domain': RALPH_DOMAIN,
        'ai_bot_access': {
            'robots_txt_url': f'https://{RALPH_DOMAIN}/robots.txt',
            'allowed_bots': robots.get('allowed', []),
            'missing_bots_to_allow': missing_bots,
            'fixing_needed': robots_fixing_needed,
        },
        'geo_compliance': {
            'total_pages_audited': total_pages,
            'geo_passing': len(geo_passing),
            'geo_failing': len(geo_failing),
            'pass_rate_pct': geo_pass_rate,
        },
        'pages_audited': pages_audited,
        'recommendations': recommendations,
        'ai_citation_status': ai_citations,
        'next_action': (
            'Fix robots.txt to allow all AI bots' if robots_fixing_needed else
            f'Improve {len(geo_failing)} GEO-non-compliant pages' if geo_failing else
            'Monitor AI citation rates via Perplexity API'
        ),
    }

    save_json(OUT_JSON, payload)

    # Print summary
    print(f"  Pages audited: {total_pages}")
    print(f"  GEO passing: {len(geo_passing)} ({geo_pass_rate}%)")
    print(f"  GEO failing: {len(geo_failing)}")
    print(f"  AI bots missing from robots.txt: {len(missing_bots)}")
    if recommendations:
        for r in recommendations[:5]:
            if r['recommendations']:
                print(f"  → {Path(r['path']).name}: {r['recommendations'][0]}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())