#!/usr/bin/env python3
"""
Conversion Surface Watchdog — structural agent for Codeberg adoption.

Problem: Blog posts are well-SEO'd, have Codeberg-primary links, but many end
with cross-links to other posts instead of a conversion CTA. The visitor reads,
learns, and clicks to another blog post — never reaching "try this tonight."

This agent:
1. Scans all Ralph-Site blog posts for conversion-surface quality
2. Scores each post on: CTA presence, free-use invite, pip install,
   first-task guide link, Codeberg-primary ordering
3. Auto-fixes low-risk CTAs (adds standardized conversion block to posts
   that score below threshold)
4. Runs as a scheduled watchdog to prevent CTA drift

Resolution: outcome_system_underpowered repair from marketing_workflow_audit
Expected outcome: more visitors reach the free-use path → Codeberg delta
Measurement window: 14 days from first deployment
Success metric: Codeberg stars_delta_window > 0 within 14 days
Kill condition: Still flat Codeberg adoption after 14 days despite all
                blog posts having strong conversion blocks
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace")))
RALPH_SITE_BLOG = WORKSPACE / "Ralph-Site" / "content" / "blog"
LOG_DIR = WORKSPACE / "agents" / "marketing" / "logs"
WATCHDOG_LOG = LOG_DIR / "conversion_surface_watchdog_latest.json"
WATCHDOG_REPORT = LOG_DIR / "conversion_surface_watchdog_latest.md"

# Conversion scoring
SCORE_THRESHOLD_AUTOFIX = 6  # Auto-fix posts scoring <= this
MAX_AUTOFIX_PER_RUN = 5  # Don't fix more than this many posts in one run

STANDARD_CONVERSION_BLOCK = """

---

**Try it on your own backlog tonight.** Pick one task that outgrew a single AI coding session. Write a one-paragraph spec, run it through Ralph Workflow, and ask yourself tomorrow morning: would you merge the output?

**Ralph Workflow is free and open source.** It runs the coding agents you already have on your own machine.

- **[Codeberg (primary repo)](https://codeberg.org/RalphWorkflow/Ralph-Workflow)** — ⭐ star, watch, fork
- **[GitHub (mirror)](https://github.com/Ralph-Workflow/Ralph-Workflow)**
- **[First-task guide](https://ralphworkflow.com/docs/first-task-guide)** — what task to pick and how to judge the result
- **[Quick install](https://ralphworkflow.com/install):** `pipx install ralph-workflow`
"""


def slugify(path: Path) -> str:
    return path.stem


def score_post(content: str, path: Path) -> dict:
    """Score a blog post on conversion-surface quality."""
    slug = slugify(path)
    result = {
        "slug": slug,
        "path": str(path),
        "lines": content.count("\n") + 1,
        "scores": {},
        "findings": [],
    }

    # 1. Has Codeberg link? (weight: 3)
    cb_count = len(re.findall(r"codeberg\.org/RalphWorkflow", content))
    result["scores"]["codeberg_link"] = 3 if cb_count > 0 else 0
    if cb_count == 0:
        result["findings"].append("MISSING: No Codeberg repo link")

    # 2. Codeberg appears before GitHub? (weight: 2)
    cb_pos = content.find("codeberg.org/RalphWorkflow")
    gh_pos = content.find("github.com/Ralph-Workflow")
    if cb_pos >= 0 and (gh_pos < 0 or cb_pos < gh_pos):
        result["scores"]["codeberg_primary"] = 2
    elif cb_pos >= 0:
        result["scores"]["codeberg_primary"] = 0
        result["findings"].append("ORDERING: GitHub link appears before Codeberg")
    else:
        result["scores"]["codeberg_primary"] = 0

    # 3. Has pip/pipx install instruction? (weight: 2)
    has_pip = bool(re.search(r"pip[x3]?\s+install\s+ralph", content, re.IGNORECASE))
    result["scores"]["pip_install"] = 2 if has_pip else 0
    if not has_pip:
        result["findings"].append("MISSING: No pip/pipx install instruction")

    # 4. Has first-task guide or start-here link? (weight: 2)
    has_first_task = bool(
        re.search(
            r"first[- ]task[- ]guide|start[- ]here|your-first-overnight|/docs/first-task",
            content,
            re.IGNORECASE,
        )
    )
    result["scores"]["first_task_guide"] = 2 if has_first_task else 0
    if not has_first_task:
        result["findings"].append("MISSING: No first-task guide link")

    # 5. Has free-use invite in final 30% of post? (weight: 3)
    # Free-use invite = language inviting the reader to try it themselves
    # tonight/today, pick a task, judge the result, merge it, etc.
    lines = content.split("\n")
    final_section_start = max(0, int(len(lines) * 0.7))
    final_section = "\n".join(lines[final_section_start:])

    free_use_patterns = [
        r"pick\s+(one|a)\s+(backlog\s+)?task",
        r"(run|try)\s+it\s+(tonight|today|on\s+your)",
        r"would\s+you\s+merge",
        r"(judge|evaluate)\s+the\s+(result|output)",
        r"(wake|come\s+back)\s+up\s+to",
        r"try\s+it\s+on\s+your\s+own",
        r"start\s+with\s+(one|a\s+single)",
        r"try\s+ralph\s+workflow",
        r"pipx\s+install\s+ralph",
        r"your\s+(first|next)\s+(overnight|unattended)",
    ]
    free_use_score = 0
    for pat in free_use_patterns:
        if re.search(pat, final_section, re.IGNORECASE):
            free_use_score += 1

    result["scores"]["free_use_invite"] = min(3, free_use_score)  # cap at 3
    if free_use_score == 0:
        result["findings"].append(
            "WEAK: No free-use invite in final section (pick a task, try tonight, judge result)"
        )
    elif free_use_score == 1:
        result["findings"].append("THIN: Only one free-use signal in final section")

    # 6. Ends with cross-links instead of conversion CTA? (weight: -1, more is worse)
    # If the last 10 lines are mostly internal blog links, that's a miss
    last_10 = "\n".join(lines[-10:])
    cross_link_count = len(
        re.findall(r"\[.*?\]\(/blog/", last_10)
    )
    result["scores"]["ends_with_crosslinks"] = -min(2, cross_link_count) if cross_link_count >= 3 else 0
    if cross_link_count >= 3:
        result["findings"].append(
            f"WEAK_ENDING: Last 10 lines are {cross_link_count} cross-links — no conversion CTA"
        )

    # 7. Has explicit star/watch/fork CTA? (weight: 1)
    has_star_ask = bool(re.search(r"star\s+the\s+repo|⭐|star\s+(it|the|us)", content, re.IGNORECASE))
    result["scores"]["star_ask"] = 1 if has_star_ask else 0

    result["total_score"] = sum(result["scores"].values())
    return result


def needs_standard_block(content: str) -> bool:
    """Check if the post already has a conversion block similar to our standard one."""
    # Check for the standard block or a close variant
    patterns = [
        r"try\s+it\s+on\s+your\s+own\s+backlog",
        r"Codeberg\s+\(primary\s+repo\)",
        r"codeberg\.org/RalphWorkflow/Ralph-Workflow.*\n.*GitHub.*mirror",
    ]
    matches = sum(1 for p in patterns if re.search(p, content, re.IGNORECASE))
    return matches >= 2


def auto_fix_post(path: Path, content: str) -> tuple[bool, str]:
    """Add standard conversion block to a post that needs it."""
    if needs_standard_block(content):
        return False, content

    # Find the last section break before the final paragraph
    # Insert our conversion block before the last horizontal rule, or at the end
    lines = content.split("\n")

    # Remove any trailing cross-link section (last section that's just blog links)
    # Look for a pattern like "---\n\n- [Blog Post Title](...)\n- [Another](...)"
    # and replace it with our conversion block
    cross_link_start = None
    for i in range(len(lines) - 1, max(0, len(lines) - 30), -1):
        if lines[i].strip().startswith("---"):
            # Check if everything after this is just cross-links
            after = "\n".join(lines[i + 1 :])
            blog_links = len(re.findall(r"\[.*?\]\(/blog/", after))
            all_lines_are_content = [l.strip() for l in lines[i + 1 :] if l.strip()]
            if blog_links >= 2 and all(
                l.strip().startswith("- [") or l.strip().startswith("[") or l.strip() == ""
                for l in all_lines_are_content
            ):
                cross_link_start = i
                break

    if cross_link_start is not None:
        # Replace trailing cross-links with conversion block
        new_content = "\n".join(lines[:cross_link_start]) + STANDARD_CONVERSION_BLOCK + "\n"
    else:
        # Append conversion block at end
        new_content = content.rstrip() + STANDARD_CONVERSION_BLOCK + "\n"

    return True, new_content


def run_audit(auto_fix: bool = True) -> dict:
    """Scan all blog posts, score them, optionally auto-fix weak posts."""
    blog_posts = sorted(RALPH_SITE_BLOG.glob("*.md"))
    if not blog_posts:
        return {"error": "No blog posts found", "path": str(RALPH_SITE_BLOG)}

    results = []
    fixes_applied = []
    unchanged = []

    for post in blog_posts:
        content = post.read_text(encoding="utf-8")
        score = score_post(content, post)
        results.append(score)

    # Sort by total_score ascending (worst first)
    results.sort(key=lambda r: r["total_score"])

    # Summary stats
    avg_score = sum(r["total_score"] for r in results) / len(results) if results else 0
    below_threshold = [r for r in results if r["total_score"] <= SCORE_THRESHOLD_AUTOFIX]
    strong = [r for r in results if r["total_score"] >= 8]
    weak = [r for r in results if r["total_score"] <= 4]

    # Auto-fix worst posts
    if auto_fix and below_threshold:
        fix_count = 0
        for result in below_threshold:
            if fix_count >= MAX_AUTOFIX_PER_RUN:
                unchanged.append({"slug": result["slug"], "reason": "max_fix_limit"})
                continue

            path = Path(result["path"])
            if not path.exists():
                continue

            content = path.read_text(encoding="utf-8")
            changed, new_content = auto_fix_post(path, content)

            if changed:
                path.write_text(new_content, encoding="utf-8")
                fixes_applied.append({
                    "slug": result["slug"],
                    "before_score": result["total_score"],
                    "action": "added_standard_conversion_block",
                })
                fix_count += 1
            else:
                unchanged.append({"slug": result["slug"], "reason": "already_has_block"})

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agent": "conversion_surface_watchdog",
        "repair_target": "outcome_system_underpowered",
        "total_posts": len(results),
        "avg_score": round(avg_score, 1),
        "below_threshold_count": len(below_threshold),
        "strong_posts_count": len(strong),
        "weak_posts_count": len(weak),
        "posts": results,
        "auto_fixes": fixes_applied,
        "unchanged": unchanged,
        "measurement_window_days": 14,
        "success_metric": "Codeberg stars_delta_window > 0 within 14 days",
        "kill_condition": "Still flat Codeberg adoption after 14 days despite all blog posts having conversion blocks",
    }

    # Write logs
    WATCHDOG_LOG.write_text(json.dumps(report, indent=2, default=str))
    write_markdown_report(report)

    return report


def write_markdown_report(report: dict):
    """Write a human-readable markdown report."""
    lines = [
        "# Conversion Surface Watchdog — Audit Report",
        f"Generated: {report['generated_at']}",
        "",
        f"**Status:** {'PASS' if report['weak_posts_count'] == 0 else 'NEEDS FIX'}",
        "",
        f"- Total posts audited: **{report['total_posts']}**",
        f"- Average conversion score: **{report['avg_score']}/13**",
        f"- Below autofix threshold (≤{SCORE_THRESHOLD_AUTOFIX}): **{report['below_threshold_count']}**",
        f"- Strong posts (≥8): **{report['strong_posts_count']}**",
        f"- Weak posts (≤4): **{report['weak_posts_count']}**",
        "",
    ]

    if report["auto_fixes"]:
        lines.append("## Auto-Fixes Applied")
        lines.append("")
        for fix in report["auto_fixes"]:
            lines.append(
                f"- **{fix['slug']}** (score {fix['before_score']}): {fix['action']}"
            )
        lines.append("")

    if report["weak_posts_count"] > 0:
        lines.append("## Weak Posts (≤4 score) — Need Manual Review")
        lines.append("")
        for post in [p for p in report["posts"] if p["total_score"] <= 4]:
            slug = post["slug"]
            score = post["total_score"]
            findings = "; ".join(post["findings"]) if post["findings"] else "no specific issues"
            lines.append(f"- **{slug}** (score {score}): {findings}")
        lines.append("")

    # Score distribution
    lines.append("## Score Distribution")
    lines.append("")
    scores = sorted(set(p["total_score"] for p in report["posts"]))
    for s in scores:
        count = sum(1 for p in report["posts"] if p["total_score"] == s)
        bar = "█" * count
        lines.append(f"- {s:2d}: {bar} ({count})")
    lines.append("")

    lines.append("## Scoring Guide")
    lines.append("")
    lines.append("| Component | Max | Description |")
    lines.append("|-----------|-----|-------------|")
    lines.append("| Codeberg link | 3 | Has Codeberg repo link |")
    lines.append("| Codeberg primary | 2 | Codeberg appears before GitHub |")
    lines.append("| pip install | 2 | Has pip/pipx install instruction |")
    lines.append("| First-task guide | 2 | Links to first-task or start-here guide |")
    lines.append("| Free-use invite | 3 | Invites reader to try tonight/judge result |")
    lines.append("| Ends with cross-links | ≤0 | Penalty: ends with blog links, not CTA |")
    lines.append("| Star ask | 1 | Asks to star the repo |")
    lines.append("")

    WATCHDOG_REPORT.write_text("\n".join(lines))


if __name__ == "__main__":
    auto_fix = "--no-fix" not in sys.argv
    report = run_audit(auto_fix=auto_fix)

    print(f"Audit complete: {report['total_posts']} posts checked")
    print(f"Average score: {report['avg_score']}/13")
    print(f"Auto-fixes applied: {len(report['auto_fixes'])}")
    print(f"Weak posts (≤4): {report['weak_posts_count']}")

    if report["auto_fixes"]:
        print("\nFixes:")
        for f in report["auto_fixes"]:
            print(f"  - {f['slug']} (was {f['before_score']})")

    # Return exit code 1 if weak posts remain (signals work needed)
    if report["weak_posts_count"] > 0:
        print(f"\n⚠️  {report['weak_posts_count']} posts still need manual review")
        sys.exit(1)
