#!/usr/bin/env python3
"""
SEO Retrofit Lane — post-publishing content quality improvement.

Triggered when owned_content_amplification hits the content saturation gate
(≥40 live posts). Instead of generating new posts, this lane retrofits
existing posts with internal cross-links, enriches thin sections, and
verifies conversion CTAs are present.

Purpose: Improve Google indexation rate (currently 14/102 = 13.7%) by
building internal link structure — the single highest-leverage on-page
SEO action for a 44-post blog with 38 link orphans.

Usage:
    python3 agents/marketing/seo_retrofit_lane.py              # full run
    python3 agents/marketing/seo_retrofit_lane.py --dry-run     # preview only
    python3 agents/marketing/seo_retrofit_lane.py --status      # check last run
    python3 agents/marketing/seo_retrofit_lane.py --post <slug> # single post
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
BLOG_DIR = ROOT / "Ralph-Site" / "content" / "blog"
STATE_PATH = ROOT / "agents" / "marketing" / "logs" / "seo_retrofit_state.json"
LOG_DIR = ROOT / "agents" / "marketing" / "logs"

# Posts excluded from internal-link enrichment.
# Previously skipped as "noindexed doorway pages" — but these comparison pages
# are actually index,follow in robots meta, present in sitemap.xml, and linked
# from the canonical /compare hub page.  Excluding them from internal backlinks
# starved legitimate high-intent landing pages of link equity.
# Fixed 2026-06-01: empty set — all posts now eligible for internal links.
NOINDEX_SKIP_SLUGS: set[str] = set()

# Minimum post size to consider for enrichment (bytes)
THIN_THRESHOLD = 3500
# Maximum related posts to add
MAX_RELATED = 4


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Parse YAML-like frontmatter from markdown. Returns (meta, body)."""
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    meta_raw = parts[1].strip()
    body = parts[2].strip()
    meta = {}
    current_key = None
    current_list = []
    for line in meta_raw.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            if current_key:
                current_list.append(stripped[2:].strip())
            continue
        if ":" in stripped or current_key is None:
            # Flush previous list
            if current_key and current_list:
                meta[current_key] = current_list
                current_list = []
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip()
                if val:
                    meta[key] = val
                    current_key = None
                else:
                    current_key = key
                    current_list = []
    if current_key and current_list:
        meta[current_key] = current_list
    return meta, body


def load_posts() -> dict[str, dict]:
    """Load all blog posts with metadata. Returns {slug: {meta, body, path, size}}."""
    posts = {}
    for f in sorted(BLOG_DIR.glob("*.md")):
        if f.name.startswith(".") or f.name == "index.md":
            continue
        slug = f.stem
        raw = f.read_text()
        meta, body = parse_frontmatter(raw)
        posts[slug] = {
            "meta": meta,
            "body": body,
            "path": f,
            "size": f.stat().st_size,
            "raw": raw,
        }
    return posts


def _tag_similarity(tags_a: list[str], tags_b: list[str]) -> int:
    """Count shared tags between two posts."""
    return len(set(tags_a) & set(tags_b))


def _title_keyword_overlap(title_a: str, title_b: str) -> int:
    """Count shared significant words between two titles."""
    stop = {"the", "a", "an", "is", "for", "to", "of", "in", "and", "or",
            "with", "your", "you", "it", "its", "how", "what", "why", "not",
            "that", "are", "on", "by", "as", "at", "be", "this", "from"}
    words_a = set(w.lower() for w in re.findall(r"[a-z]{3,}", title_a.lower()) if w.lower() not in stop)
    words_b = set(w.lower() for w in re.findall(r"[a-z]{3,}", title_b.lower()) if w.lower() not in stop)
    return len(words_a & words_b)


def find_related(slug: str, posts: dict[str, dict], max_n: int = MAX_RELATED) -> list[str]:
    """Find most related posts for a given slug."""
    if slug not in posts:
        return []
    source = posts[slug]
    source_tags = source["meta"].get("tags", [])
    if isinstance(source_tags, str):
        source_tags = [t.strip() for t in source_tags.split(",")]
    source_title = source["meta"].get("title", slug)

    scores = []
    for other_slug, other in posts.items():
        if other_slug == slug:
            continue
        if other_slug in NOINDEX_SKIP_SLUGS:
            continue
        other_tags = other["meta"].get("tags", [])
        if isinstance(other_tags, str):
            other_tags = [t.strip() for t in other_tags.split(",")]
        tag_score = _tag_similarity(source_tags, other_tags)
        title_score = _title_keyword_overlap(source_title, other["meta"].get("title", other_slug))
        # Heavily weight tag similarity
        total = tag_score * 3 + title_score
        if total > 0:
            scores.append((total, other_slug, other["meta"].get("title", other_slug)))

    scores.sort(key=lambda x: -x[0])
    return [s for _, s, _ in scores[:max_n]]


def has_related_section(body: str) -> bool:
    """Check if body already has a related-posts section."""
    return bool(re.search(
        r"(?:^|\n)#{1,3}\s*(?:Related|Further Reading|See Also|More on|Keep Reading)",
        body, re.IGNORECASE
    ))


def _title_link(title: str, slug: str) -> str:
    """Format a title as a markdown link to the blog post."""
    return f"[{title}](/blog/{slug})"


def generate_related_section(related_slugs: list[str], posts: dict[str, dict]) -> str:
    """Generate a Related Posts markdown section."""
    lines = ["\n## Related Posts\n"]
    for slug in related_slugs:
        if slug in posts:
            title = posts[slug]["meta"].get("title", slug.replace("-", " ").title())
            lines.append(f"- {_title_link(title, slug)}")
    return "\n".join(lines) + "\n"


def check_cta(body: str) -> bool:
    """Check if post has any conversion CTA."""
    cta_patterns = [
        r"codeberg\.org/RalphWorkflow",
        r"github\.com/Ralph-Workflow",
        r"pip install ralph",
        r"pip3 install ralph",
        r"pipx install ralph",
        r"\bstars?\s+on\s+(Codeberg|GitHub)",
        r"star\s+(it\s+)?on\s+(Codeberg|GitHub)",
        r"try\s+(Ralph|it)\s+(out|today|now)",
        r"get\s+started",
        r"ralphworkflow\.com",
    ]
    return any(re.search(p, body, re.IGNORECASE) for p in cta_patterns)


def generate_cta_block() -> str:
    """Generate a conversion CTA block for posts missing one."""
    return (
        "\n---\n\n"
        "**Start shipping overnight.** [Install Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) "
        "in under five minutes — pip install ralph-workflow — and wake up to tested code, a clean diff, "
        "and a morning-after summary of what changed.\n"
    )


def retrofit_post(slug: str, posts: dict[str, dict], dry_run: bool = False) -> dict:
    """Retrofit a single post with internal links and CTA. Returns change report."""
    if slug not in posts:
        return {"slug": slug, "error": "not found"}
    if slug in NOINDEX_SKIP_SLUGS:
        return {"slug": slug, "skipped": "noindex doorway page"}

    post = posts[slug]
    body = post["body"]
    changes = []

    # 1. Add internal cross-links if missing
    if not has_related_section(body):
        related = find_related(slug, posts, MAX_RELATED)
        if related:
            section = generate_related_section(related, posts)
            body_with = body + "\n" + section
            changes.append(f"+{len(related)} related links")

            if not dry_run:
                new_raw = _rebuild_post(post["meta"], body_with)
                post["path"].write_text(new_raw)
    else:
        changes.append("already has related links")

    # 2. Check CTA
    if not check_cta(body):
        cta_block = generate_cta_block()
        body_with_cta = body + "\n" + cta_block
        changes.append("+CTA block")

        if not dry_run:
            # Re-read body in case it was modified by step 1
            current_raw = post["path"].read_text()
            _, current_body = parse_frontmatter(current_raw)
            if not check_cta(current_body):
                new_body = current_body + "\n" + cta_block
                new_raw = _rebuild_post(post["meta"], new_body)
                post["path"].write_text(new_raw)
    else:
        changes.append("CTA OK")

    return {"slug": slug, "changes": changes, "title": post["meta"].get("title", slug)}


def _rebuild_post(meta: dict, body: str) -> str:
    """Rebuild a complete markdown file from meta and body."""
    lines = ["---"]
    for key, val in meta.items():
        if isinstance(val, list):
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {item}")
        else:
            # Quote values containing YAML-special characters (colons, etc.)
            if isinstance(val, str) and (":" in val or "#" in val or val.startswith(("'", '"')) or val in ("true", "false", "yes", "no", "on", "off", "null", "~")):
                lines.append(f"{key}: \"{val}\"")
            else:
                lines.append(f"{key}: {val}")
    lines.append("---")
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    return "\n".join(lines)


def run(dry_run: bool = False, target_slug: Optional[str] = None) -> dict:
    """Main retrofit execution."""
    posts = load_posts()
    if not posts:
        return {"error": "no posts found", "blog_dir": str(BLOG_DIR)}

    now = datetime.now(timezone.utc)
    report = {
        "ran_at": now.isoformat(),
        "dry_run": dry_run,
        "total_posts": len(posts),
        "posts_with_related_links_before": sum(1 for p in posts.values() if has_related_section(p["body"])),
        "noindex_skipped": len(NOINDEX_SKIP_SLUGS),
        "results": [],
    }

    targets = [target_slug] if target_slug else sorted(posts.keys())
    for slug in targets:
        if slug in NOINDEX_SKIP_SLUGS:
            report["results"].append({"slug": slug, "skipped": "noindex"})
            continue
        result = retrofit_post(slug, posts, dry_run)
        report["results"].append(result)

    # Summary
    added_links = sum(
        1 for r in report["results"]
        if "changes" in r and any("related links" in str(c) for c in r.get("changes", []))
    )
    added_ctas = sum(
        1 for r in report["results"]
        if "changes" in r and any("CTA block" in str(c) for c in r.get("changes", []))
    )
    skipped_noindex = sum(1 for r in report["results"] if r.get("skipped") == "noindex")
    report["summary"] = {
        "posts_retrofitted_links": added_links,
        "posts_retrofitted_cta": added_ctas,
        "posts_skipped_noindex": skipped_noindex,
        "posts_already_linked": report["posts_with_related_links_before"],
    }

    # Persist state
    if not dry_run:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(report, indent=2))

    # Write log
    log_path = LOG_DIR / f"seo_retrofit_{now.strftime('%Y-%m-%d_%H%M%S')}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(report, indent=2))

    return report


def show_status():
    """Show last retrofit run status."""
    if not STATE_PATH.exists():
        print(json.dumps({"status": "no previous run", "state_path": str(STATE_PATH)}, indent=2))
        return

    state = json.loads(STATE_PATH.read_text())
    # Count orphaned posts (still need links)
    blogs_need_links = sum(
        1 for r in state.get("results", [])
        if "changes" in r and any("+related" in str(c) for c in r.get("changes", []))
    )
    orphans_remaining = sum(
        1 for r in state.get("results", [])
        if "changes" in r and "+related links" not in str(r.get("changes", ""))
        and not r.get("skipped")
        and not any("already has" in str(c) for c in r.get("changes", []))
    )

    print(json.dumps({
        "last_run": state.get("ran_at"),
        "dry_run": state.get("dry_run"),
        "total_posts": state.get("total_posts"),
        "linked_before": state.get("posts_with_related_links_before"),
        "posts_retrofitted_links": state.get("summary", {}).get("posts_retrofitted_links"),
        "posts_retrofitted_cta": state.get("summary", {}).get("posts_retrofitted_cta"),
        "posts_now_linked": state.get("posts_with_related_links_before", 0) +
                           state.get("summary", {}).get("posts_retrofitted_links", 0),
        "state_path": str(STATE_PATH),
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description="SEO Retrofit Lane")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes only")
    parser.add_argument("--status", action="store_true", help="Show last run status")
    parser.add_argument("--post", type=str, help="Retrofit a single post by slug")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    report = run(dry_run=args.dry_run, target_slug=args.post)
    print(json.dumps(report.get("summary", report), indent=2))


if __name__ == "__main__":
    main()
