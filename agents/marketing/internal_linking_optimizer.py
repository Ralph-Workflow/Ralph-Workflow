#!/usr/bin/env python3
"""
Internal Linking Optimizer — strengthens Google crawl budget by ensuring all blog
posts are reachable through at least 2 internal links from well-linked posts.

Built for: Ralph-Site Rails app (content/blog/*.md with YAML frontmatter)
Purpose: Attack the 15.7% Google indexation crisis (84/102 URLs invisible to search)

Usage:
    python3 internal_linking_optimizer.py                  # analyze + report
    python3 internal_linking_optimizer.py --apply [--dry-run]  # apply cross-links
    python3 internal_linking_optimizer.py --apply --max 3  # apply up to 3 links

Expected outcome: Improvement in Google's pages-with-search-presence (GSC index
coverage report) within 14-28 days as orphan/semi-orphan posts become crawlable
through internal link graph.

Measurement window: 28 days (Google re-crawls on its own schedule)
Kill condition: No improvement in pages-with-search-presence after 28 days
Replacement path: Rebuild as a sitemap-only strategy or Indexing API push agent
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path("/home/mistlight/.openclaw/workspace")
RALPH_SITE = ROOT / "Ralph-Site"
BLOG_DIR = RALPH_SITE / "content/blog"
LOG_DIR = ROOT / "agents/marketing/logs"
ARTIFACT_BASE = "internal_linking_optimizer_latest"

# Posts with fewer than this many inbound links need cross-link coverage
MIN_INBOUND = 2
# Max auto-applied cross-links per run (safety cap)
MAX_AUTO_LINKS = 5
# Only add links from posts whose inbound count >= this threshold
MIN_PARENT_INBOUND = 3


def slug_from_path(path: Path) -> str:
    return path.stem


def parse_post(path: Path) -> dict:
    """Extract title, content body, frontmatter, and outbound blog links."""
    with open(path) as f:
        raw = f.read()

    slug = slug_from_path(path)

    # Extract frontmatter title
    fm_title = None
    fm_match = re.search(r'^title:\s*(.+)$', raw, re.MULTILINE)
    if fm_match:
        fm_title = fm_match.group(1).strip().strip('"').strip("'")

    # Extract H1
    h1_match = re.search(r'^#\s+(.+)$', raw, re.MULTILINE)
    h1 = h1_match.group(1).strip() if h1_match else None

    display_title = fm_title or h1 or slug

    # Strip YAML frontmatter for body analysis
    body = raw
    if raw.startswith("---"):
        end = raw.find("---", 3)
        if end > 0:
            body = raw[end + 3:]

    # Extract all internal blog links: [text](/blog/slug)
    internal_links = re.findall(
        r'\[([^\]]+)\]\(/blog/([^)#\s]+)(?:#[^)]*)?\)',
        body
    )
    outbound_slugs = list(dict.fromkeys(s for _, s in internal_links))

    return {
        "slug": slug,
        "title": display_title,
        "fm_title": fm_title,
        "h1": h1,
        "path": str(path),
        "outbound": outbound_slugs,
        "body": body,
        "raw": raw,
    }


def build_graph(posts: dict[str, dict]) -> dict[str, dict]:
    """Compute inbound links for each post."""
    graph = {}
    for slug, data in posts.items():
        graph[slug] = {**data, "inbound": [], "inbound_count": 0}

    for slug, data in posts.items():
        for out_slug in data["outbound"]:
            if out_slug in graph:
                graph[out_slug]["inbound"].append(slug)
                graph[out_slug]["inbound_count"] = len(graph[out_slug]["inbound"])

    return graph


def classify(graph: dict[str, dict]) -> dict:
    """Classify posts by link health."""
    orphans = {}
    semi_orphans = {}
    well_linked = {}

    for slug, data in graph.items():
        ic = data["inbound_count"]
        if ic == 0:
            orphans[slug] = data
        elif ic == 1:
            semi_orphans[slug] = data
        else:
            well_linked[slug] = data

    return {
        "orphans": orphans,
        "semi_orphans": semi_orphans,
        "well_linked": well_linked,
    }


def extract_keywords(post: dict) -> set[str]:
    """Extract topic keywords from a post's title and body for matching."""
    text = (post.get("title", "") + " " + post.get("body", "")).lower()

    # Domain-specific keyword groups
    groups = [
        # Execution & safety
        ["sandbox", "safe", "execution", "isolation", "contain", "risk", "security", "permission"],
        # Vendor & platform
        ["vendor", "platform", "neutral", "independent", "provider", "swap", "lock-in", "open"],
        # Config & TOML
        ["config", "toml", "configuration", "verbosity", "agent config", "pipeline.toml"],
        # Verification & testing
        ["verification", "contract", "test", "verify", "review output", "trust", "prove"],
        # Offline & local
        ["offline", "local", "llm", "ollama", "privacy", "air-gap", "no internet"],
        # Claude Code specific
        ["claude code", "anthropic", "claude", "unattended session"],
        # Overnight / unattended
        ["overnight", "unattended", "sleep", "morning", "reviewable"],
        # Multi-agent
        ["multi-agent", "orchestrat", "parallel", "compose", "pipeline"],
        # Docker / CI
        ["docker", "ci/cd", "container", "pipeline", "build"],
        # Getting started / first task
        ["first task", "getting started", "start here", "begin", "onboard"],
    ]

    keywords = set()
    for group in groups:
        if any(kw in text for kw in group):
            keywords.update(group)

    return keywords


def suggest_links(graph: dict[str, dict], classification: dict) -> list[dict]:
    """Generate natural cross-link suggestions for underlinked posts.
    Ensures parent diversity — no single parent gets more than 2 suggestions."""
    suggestions = []
    well_linked = classification["well_linked"]
    orphans = classification["orphans"]
    semi_orphans = classification["semi_orphans"]
    needs_links = {**orphans, **semi_orphans}

    for child_slug, child in needs_links.items():
        child_keywords = extract_keywords(child)
        if not child_keywords:
            continue

        candidates = []
        for parent_slug, parent in well_linked.items():
            if parent["inbound_count"] < MIN_PARENT_INBOUND:
                continue
            if child_slug in parent["outbound"]:
                continue

            parent_keywords = extract_keywords(parent)
            overlap = child_keywords & parent_keywords
            if not overlap:
                continue

            # Boost score for parents that are more topically aligned
            score = len(overlap) * 10 + parent["inbound_count"]
            candidates.append({
                "parent_slug": parent_slug,
                "parent_title": parent["title"],
                "parent_inbound": parent["inbound_count"],
                "keyword_overlap": sorted(overlap),
                "score": score,
            })

        candidates.sort(key=lambda c: c["score"], reverse=True)

        if candidates:
            suggestions.append({
                "child_slug": child_slug,
                "child_title": child["title"],
                "child_inbound": child["inbound_count"],
                "classification": "orphan" if child["inbound_count"] == 0 else "semi-orphan",
                "candidates": candidates[:5],  # top 5 for diversity routing
            })

    suggestions.sort(key=lambda s: s["child_inbound"])  # neediest first

    # Diversity routing: cap each parent at 2 suggestions, resolve conflicts
    parent_usage: dict[str, int] = defaultdict(int)
    MAX_PER_PARENT = 2
    diversified = []
    for sug in suggestions:
        chosen = None
        for cand in sug.get("candidates", []):
            ps = cand["parent_slug"]
            if parent_usage.get(ps, 0) < MAX_PER_PARENT:
                chosen = cand
                parent_usage[ps] = parent_usage.get(ps, 0) + 1
                break
        if chosen:
            sug["candidates"] = [chosen] + [c for c in sug["candidates"] if c != chosen][:2]
            diversified.append(sug)

    return diversified


LINK_PHRASES = [
    "For a hands-on look at {topic}, we also wrote **[{title}]({url})**.",
    "If you are thinking about {topic}, **[{title}]({url})** goes deeper.",
    "We have a separate deep-dive on {topic}: **[{title}]({url})**.",
    "Related: **[{title}]({url})** covers {topic} in detail.",
    "See also: **[{title}]({url})** for the full breakdown.",
]

TOPIC_MAP = {
    "security": "running untrusted code safely",
    "safe": "sandbox isolation patterns",
    "sandbox": "sandbox isolation for AI coding agents",
    "isolation": "isolating AI agent execution",
    "vendor": "vendor-neutral workflow design",
    "platform": "platform-independent agent orchestration",
    "provider": "switching between AI providers",
    "neutral": "staying provider-independent",
    "config": "TOML configuration for multi-agent workflows",
    "toml": "TOML-based agent configuration",
    "verification": "verifying AI-generated code output",
    "verify": "output verification strategies",
    "test": "testing autonomous code output",
    "offline": "running coding agents without internet",
    "local": "running AI coding agents fully locally",
    "llm": "local LLM coding setups",
    "privacy": "private, offline AI coding",
    "review": "reviewing autonomous code output",
}


def _topic_phrase(keywords: list[str]) -> str:
    for kw in keywords:
        if kw.lower() in TOPIC_MAP:
            return TOPIC_MAP[kw.lower()]
    return ", ".join(keywords[:2])


def find_insertion_point(body: str, child_slug: str, child_title: str, keyword_overlap: list[str]) -> Optional[dict]:
    """
    Find a natural insertion point in the parent body for a cross-link.
    Uses \n\n paragraph splitting for reliable extraction.
    Returns {"before": str, "after": str, "link_text": str, "inserted": str}
    or None if no natural point found.
    """
    link_url = f"/blog/{child_slug}"
    topic = _topic_phrase(keyword_overlap)
    import random
    phrase_tpl = random.choice(LINK_PHRASES)
    link_sentence = phrase_tpl.format(topic=topic, title=child_title, url=link_url)

    # Split into paragraphs by double newline, tracking positions
    paras = []  # list of (text, start_pos, end_pos)
    pos = 0
    for block in body.split("\n\n"):
        # find this block in body starting from pos
        idx = body.find(block, pos)
        if idx >= 0:
            end = idx + len(block)
            paras.append((block.strip(), idx, end))
            pos = end + 2  # skip double newline
        else:
            paras.append((block.strip(), pos, pos + len(block)))
            pos += len(block) + 2

    keyword_lower = [k.lower() for k in keyword_overlap]
    best_idx = -1
    best_score = 0

    for i, (text, start, end) in enumerate(paras):
        if not text or len(text) < 40:
            continue
        if text.startswith("#") or text.startswith("---") or text.startswith(">") or text.startswith("|"):
            continue
        text_lower = text.lower()
        hits = sum(1 for k in keyword_lower if k in text_lower)
        # Boost longer paragraphs (more natural context for inserting a link)
        boost = min(len(text) / 200.0, 2.0)
        score = hits + boost
        if score > best_score:
            best_score = score
            best_idx = i

    if best_idx < 0:
        return None

    text, start, end = paras[best_idx]
    # Find the actual end of this paragraph block in the original body
    # (including any trailing whitespace before \n\n)
    after_block = body[end:]
    next_double_nl = after_block.find("\n\n")
    if next_double_nl >= 0:
        real_end = end + next_double_nl
    else:
        real_end = len(body)

    # Insert link paragraph after the target paragraph
    insert_pos = real_end
    new_body = body[:insert_pos] + "\n\n" + link_sentence + body[insert_pos:]

    return {
        "before": text[:150] + ("..." if len(text) > 150 else ""),
        "inserted_sentence": link_sentence,
        "after_body": new_body,
        "para_context": text[:120] + ("..." if len(text) > 120 else ""),
        "keyword_context": topic,
    }


def apply_cross_link(parent_path: str, child_slug: str, child_title: str, keyword_overlap: list[str]) -> Optional[dict]:
    """Apply a single cross-link from parent to child post. Returns result dict or None."""
    with open(parent_path) as f:
        body = f.read()

    # Skip frontmatter
    fm_end = 0
    if body.startswith("---"):
        end = body.find("---", 3)
        if end > 0:
            fm_end = end + 3

    fm = body[:fm_end]
    content = body[fm_end:]

    insertion = find_insertion_point(content, child_slug, child_title, keyword_overlap)
    if insertion is None:
        return None

    new_body = fm + insertion["after_body"]

    # Check we're not creating a broken reference (child_slug should exist)
    child_path = BLOG_DIR / f"{child_slug}.md"
    if not child_path.exists():
        return {"error": f"Child post {child_slug}.md does not exist"}

    with open(parent_path, "w") as f:
        f.write(new_body)

    return {
        "parent": Path(parent_path).stem,
        "child": child_slug,
        "link_text": child_title,
        "context": insertion["keyword_context"],
        "paragraph_hint": insertion["para_context"],
    }


def analyze() -> dict:
    """Full analysis run — returns the link graph, classification, and suggestions."""
    posts = {}
    for path in sorted(BLOG_DIR.glob("*.md")):
        slug = slug_from_path(path)
        posts[slug] = parse_post(path)

    graph = build_graph(posts)
    classification = classify(graph)
    suggestions = suggest_links(graph, classification)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_posts": len(posts),
        "orphan_count": len(classification["orphans"]),
        "semi_orphan_count": len(classification["semi_orphans"]),
        "well_linked_count": len(classification["well_linked"]),
        "orphans": [
            {"slug": s, "title": d["title"], "inbound": d["inbound_count"]}
            for s, d in classification["orphans"].items()
        ],
        "semi_orphans": [
            {"slug": s, "title": d["title"], "inbound": d["inbound_count"],
             "inbound_from": d["inbound"]}
            for s, d in classification["semi_orphans"].items()
        ],
        "suggestions": suggestions,
        "graph": graph,
    }


def format_md_report(result: dict) -> str:
    """Generate a human-readable Markdown report."""
    lines = [
        "# Internal Linking Health Report",
        f"Generated: {result['generated_at']}",
        "",
        f"**Total posts:** {result['total_posts']}",
        f"**Orphans (0 inbound):** {result['orphan_count']}",
        f"**Semi-orphans (1 inbound):** {result['semi_orphan_count']}",
        f"**Well-linked (2+ inbound):** {result['well_linked_count']}",
        "",
    ]

    if result["orphans"]:
        lines.append("## 🚨 Orphans — not linked from any other post")
        lines.append("")
        lines.append("Google may not discover these — they have zero internal inbound links.")
        lines.append("")
        for o in result["orphans"]:
            lines.append(f"- **{o['slug']}** — {o['title']}")
        lines.append("")

    if result["semi_orphans"]:
        lines.append("## ⚠️ Semi-Orphans — only 1 inbound link")
        lines.append("")
        for so in result["semi_orphans"]:
            lines.append(f"- **{so['slug']}** — {so['title']}  ")
            lines.append(f"  Linked from: {so['inbound_from'][0]}")
        lines.append("")

    if result.get("suggestions"):
        lines.append("## 🔗 Suggested cross-links (top 5)")
        lines.append("")
        for s in result["suggestions"][:5]:
            lines.append(f"### {s['child_slug']} ({s['classification']}, {s['child_inbound']} inbound)")
            lines.append(f"*{s['child_title']}*")
            lines.append("")
            for c in s.get("candidates", [])[:2]:
                lines.append(f"- **→ {c['parent_slug']}** (score={c['score']}, {c['parent_inbound']} inbound)")
                lines.append(f"  Keywords: {', '.join(c['keyword_overlap'])}")
            lines.append("")

    return "\n".join(lines)


def main():
    apply_mode = "--apply" in sys.argv
    dry_run = "--dry-run" in sys.argv
    max_links = MAX_AUTO_LINKS
    for i, arg in enumerate(sys.argv):
        if arg == "--max" and i + 1 < len(sys.argv):
            try:
                max_links = int(sys.argv[i + 1])
            except ValueError:
                pass

    # Always analyze
    result = analyze()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Write JSON artifact
    json_path = LOG_DIR / f"{ARTIFACT_BASE}.json"
    # Strip graph from JSON (too large)
    json_result = {k: v for k, v in result.items() if k != "graph"}
    with open(json_path, "w") as f:
        json.dump(json_result, f, indent=2, default=str)

    # Write MD artifact
    md_path = LOG_DIR / f"{ARTIFACT_BASE}.md"
    with open(md_path, "w") as f:
        f.write(format_md_report(result))

    print(f"Analyzed {result['total_posts']} posts.")
    print(f"  Orphans: {result['orphan_count']}")
    print(f"  Semi-orphans: {result['semi_orphan_count']}")
    print(f"  Well-linked: {result['well_linked_count']}")
    print(f"  Suggestions: {len(result.get('suggestions', []))}")
    print(f"Reports: {json_path}, {md_path}")

    if not apply_mode:
        print("\nRun with --apply to auto-apply up to 5 cross-links.")
        print("Run with --apply --dry-run to preview without writing.")
        return

    # Apply mode: add cross-links from well-linked parents to neediest children
    applied = []
    suggestions = result.get("suggestions", [])

    # Sort: neediest first (0 inbound > 1 inbound)
    suggestions.sort(key=lambda s: s["child_inbound"])

    for sug in suggestions:
        if len(applied) >= max_links:
            break

        child_slug = sug["child_slug"]
        child_title = sug["child_title"]
        candidates = sug.get("candidates", [])

        for cand in candidates:
            if len(applied) >= max_links:
                break

            parent_slug = cand["parent_slug"]
            parent_path = BLOG_DIR / f"{parent_slug}.md"

            if not parent_path.exists():
                continue

            link_result = apply_cross_link(
                str(parent_path),
                child_slug,
                child_title,
                cand["keyword_overlap"],
            )

            if link_result and "error" not in link_result:
                if dry_run:
                    print(f"\n[DRY RUN] Would add link:")
                    print(f"  {parent_slug} → {child_slug}")
                    print(f"  Context: {link_result['context']}")
                    print(f"  Para: {link_result['paragraph_hint'][:120]}")
                else:
                    print(f"\n✅ Added: {parent_slug} → {child_slug}")
                applied.append({**link_result, "dry_run": dry_run})
                break  # one link per child

    # Write applied links log
    if applied:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "links_applied": applied,
            "dry_run": dry_run,
        }
        applied_path = LOG_DIR / f"{ARTIFACT_BASE}_applied.json"
        with open(applied_path, "w") as f:
            json.dump(log_entry, f, indent=2, default=str)

        if dry_run:
            print(f"\n[Dry run complete] {len(applied)} links previewed.")
        else:
            print(f"\nApplied {len(applied)} cross-links. Log: {applied_path}")
            print("⚠️  Remember to commit changes in Ralph-Site and deploy.")
    else:
        print("\nNo links applied — all targets already covered or no natural insertion points found.")


if __name__ == "__main__":
    main()
