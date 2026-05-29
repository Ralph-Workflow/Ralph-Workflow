# GEO / AI Search Optimization — Strategy & Agent Loops

> What we know about Generative Engine Optimization (GEO) and how it fits into Ralph Workflow's marketing system.

## The problem

AI search engines (ChatGPT Search, Perplexity, Google AI Overviews, Claude with web, Phind) don't index pages the way Google does. Instead they run RAG pipelines:

```
Query → Vector embedding → Live web retrieval → Information extraction → LLM synthesis → Response with citations
```

They still need the page to clear baseline web standards. But satisfying Google ≠ satisfying AI. The ranking factors are different.

## What matters for AI citation (GEO)

| Factor | Why it matters |
|--------|---------------|
| **Quantitative data density** | LLMs prefer pages with stats, benchmarks, percentages over qualitative claims |
| **Direct-answer structure** | H2 sections that start with a definition/fact get cited; fluff intros get skipped |
| **JSON-LD schema** | Structured entity data helps AI resolve what your page is about |
| **3+ outbound authority links** | Signals that the page cites real sources, not just self-reference |
| **300+ word threshold** | Pages under ~300 words get dropped by most RAG pipelines |
| **AI bot access** | If your robots.txt blocks AI crawlers, you're invisible to AI search |

## What we've done

### 1. geo_agent.py — GEO audit loop
- Scans all blog posts for GEO compliance
- Checks AI bot access in robots.txt
- Flags pages missing: atomic sections, stats, outbound links, JSON-LD
- Runs on a schedule; outputs `geo_agent_latest.json`

**Current state (2026-05-28):**
- 24 pages audited — 0 GEO-compliant
- All 24 fail on `no_atomic_blocks` + `no_quantitative_stats` + `missing_jsonld`
- robots.txt was missing 14 AI bot entries → **FIXED** (commit `7f65174` to Ralph-Site)

### 2. geo_content_improver.py — GEO content fixer
- Fixes atomic blocks (H2 lead sentences)
- Injects quantitative stats
- Adds outbound authority links
- Injects JSON-LD schema markup
- Runs in dry-run first mode

**Usage:**
```bash
# Dry run first
python3 agents/marketing/geo_content_improver.py --dry-run --limit=5

# Apply to top comparison pages
python3 agents/marketing/geo_content_improver.py --limit=10
```

### 3. AI bot access in robots.txt
All major AI search engine crawlers now allowed: GPTBot, PerplexityBot, Google-Extended, anthropic-ai, and 10 others.

## AI search citation tracking

Real citation tracking requires paid APIs:
- **Perplexity API** — direct citation data per domain
- **ChatGPT citation API** — available via OpenAI API
- **Bing Webmaster** — AI overview visibility data

Current state: citation tracking is **simulated** in geo_agent.py. The `ai_citation_status` field needs a real API integration to report actual AI citations.

## GEO compliance thresholds

```
GEO_MIN_WORDS = 300
GEO_ATOMIC_SECTIONS = 3      (H2 sections starting with direct answers)
GEO_STATS_PER_PAGE = 2       (quantitative facts)
GEO_EXTERNAL_LINKS = 3      (authority signals)
```

## What still needs doing

1. **Apply geo_content_improver** to the 8 comparison pages (highest-value for AI search)
2. **Connect Perplexity API** for real citation tracking (marketing budget needed)
3. **Add JSON-LD to Hugo templates** so every new post auto-gets schema markup
4. **Add "AI-optimized" content brief** to the content generation pipeline
5. **Track AI search ranking** separately from Google ranking

## AI search engines to target

| Engine | Crawler | Notes |
|--------|---------|-------|
| ChatGPT | GPTBot, ChatGPT-User | OpenAI API has citation data |
| Perplexity | PerplexityBot | Has an API for citation tracking |
| Google AI Overviews | Google-Extended | Works if page is indexed by Google |
| Claude (web) | anthropic-ai | Uses Common Crawl |
| Microsoft Copilot | PetalBot | Via Bing index |
| Phind | — | Uses its own index |
| You.com | YouBot | Has its own crawler |

## How to incorporate into existing SEO loop

The GEO agent should run as part of the regular content pipeline:
1. **After publishing**: run `geo_agent.py` to check the new post
2. **Weekly**: run `geo_content_improver.py` on posts with GEO failures
3. **Monthly**: review AI citation rates via Perplexity/Bing Webmaster API
4. **Before publishing**: content brief should include the GEO checklist

## GEO vs traditional SEO

Traditional SEO still matters — AI search engines still use Google's index as a primary source for candidate retrieval. GEO is an **additional layer**, not a replacement. The ranking hierarchy:

1. First: must clear standard web/Google requirements (technical SEO, content quality, links)
2. Then: GEO requirements (stats, atomic structure, JSON-LD, AI bot access) for AI-specific citation

A page that ranks well on Google AND satisfies GEO will appear in both traditional SERPs AND AI citations. The goal is dual visibility.