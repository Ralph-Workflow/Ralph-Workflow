#!/usr/bin/env python3
"""Generate RalphWorkflow content drafts with experiment metadata.

This is intentionally simple and deterministic so weekly evaluation can compare
content buckets over time.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path("/home/mistlight/.openclaw/workspace")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing.positioning import validate_marketing_copy

DRAFTS_DIR = Path("/home/mistlight/.openclaw/workspace/drafts")
DRAFTS_DIR.mkdir(parents=True, exist_ok=True)




def load_seo_insights() -> dict:
    """Read SEO gap insights from run.py's latest analysis."""
    insights_file = Path(__file__).parent / 'logs' / 'seo-insights.json'
    if not insights_file.exists():
        return {}
    try:
        return json.loads(insights_file.read_text(encoding='utf-8'))
    except Exception:
        return {}


@dataclass(frozen=True)
class Topic:
    slug: str
    content_type: str
    angle: str
    keyword: str
    cta: str
    hypothesis: str
    body: str


TOPIC_ROTATION: list[Topic] = [
    Topic(
        slug="orchestration",
        content_type="comparison",
        angle="What an AI agent orchestration CLI actually does that a prompt chain cannot",
        keyword="ai agent orchestration CLI",
        cta="install_ralphworkflow",
        hypothesis="Comparison posts targeting the 'orchestration CLI' keyword attract developers who already tried basic AI coding tools and need something more structured.",
        body="""# What an AI Agent Orchestration CLI Actually Does That a Prompt Chain Cannot

You have a prompt chain. You have Claude Code. You have a bash script that strings them together. So why do you still need to babysit everything?

The gap is orchestration — not generation. Prompt chains generate. Orchestration frameworks coordinate, verify, and loop.

## The Difference in One Sentence

A prompt chain says: do X, then do Y, then do Z.

An orchestration CLI says: do X, check that X is correct, loop if not, then do Y, check that Y is correct, loop if not, then do Z — and give me a diff I can review.

## Why This Matters for Real Engineering Work

Unattended work only works when:
- each step has an exit criterion
- failures trigger revision, not propagation
- the final output is reviewable without re-running anything

Ralph Workflow is a composable loop framework for this. It is a CLI that orchestrates your existing AI coding agents through phases with explicit handoffs and automated checks.

## What an Orchestration CLI Gives You

- **Spec-first phases**: the agent works against a spec, not a vibe
- **Automated verify step**: catches obvious mistakes before they compound
- **Clean re-entry point**: if something fails, you know exactly where — and can resume
- **Reviewable diff**: not "it ran" but "here's what changed and why"
- **Looping structure**: planning loops, development loops, the whole thing loops

## What It Doesn't Do

It does not write your code for you. It runs the coding agents you already have, on your own machine, in a structure that survives unattended overnight runs.

The difference between a prompt chain and an orchestration CLI is the difference between a todo list and a project manager. Both have your tasks. Only one checks the work.

---

**Try it on Codeberg:** [RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — star, fork, and open issues there. GitHub mirror: [Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow).
""",
    ),
    Topic(
        slug="unattended_agent",
        content_type="technical",
        angle="What 'unattended coding agent' actually means and why most setups aren't",
        keyword="unattended coding agent",
        cta="install_ralphworkflow",
        hypothesis="Directly targeting the 'unattended coding agent' keyword fills the clearest SEO gap and matches the strongest product differentiator.",
        body="""# What 'Unattended Coding Agent' Actually Means — and Why Most Setups Aren't

Unattended does not mean "set it and forget it." It means you can walk away from the session and come back to something reviewable.

Most AI coding setups call themselves unattended because you can start a long task. They are not unattended in any meaningful sense — you still have to watch for failures, catch hallucinated tests, and manually verify the output.

## The Three Requirements for a Genuinely Unattended Setup

1. **Bounded scope** — the task has a spec, not just a prompt
2. **Automated verification** — something checks the output before you see it
3. **Clean re-entry** — if it fails, you know exactly where and can resume without starting over

Without all three, "unattended" just means "the AI is failing without you watching."

## What Ralph Workflow Adds

Ralph Workflow is a composable loop framework that runs your existing AI coding agents through those three requirements automatically.

```text
spec-first → agent builds → verify catches mistakes → loop if broken → clean output
```

You write the spec. The orchestration loop handles the rest — including the verify step that catches what the agent would otherwise miss.

## The Overnight Test

The real test of an unattended setup: can you start it at 11pm, sleep 8 hours, and wake up to something you can actually review?

If your current setup can't pass that test, it is not unattended — it just doesn't require constant input. There's a meaningful difference.

Ralph Workflow is built to pass the overnight test. That's what the loop structure is for.

---

**Try it on Codeberg:** [RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — star, fork, and open issues there. GitHub mirror: [Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow).
""",
    ),
    Topic(
        slug="philosophy",
        content_type="philosophy",
        angle="Why AI agents need structure, not just prompts",
        keyword="ai agent workflow",
        cta="install_ralphworkflow",
        hypothesis="Philosophy posts clarify positioning and should build baseline awareness.",
        body="""# Why AI Agents Need Structure, Not Just Prompts

Most AI coding tools treat prompts like magic spells — cast the right words and code appears. It rarely works at scale.

The problem isn't the AI. It's the absence of a feedback loop.

## The Wandering Agent Problem

Give an AI agent \"build a login system\" and watch what happens:
- It builds auth from scratch instead of using a library
- It picks PostgreSQL when SQLite would do
- It forgets to hash passwords
- It writes tests that don't actually test anything

The agent isn't stupid. It's just optimizing for the wrong thing — completing the task as fast as possible, not getting it right.

## Structure Forces Correctness

When you add a spec-first phase, something shifts:

```text
❌ Prompt: \"build a login system\"
✅ Spec: \"Use Django auth. On failure show inline error. Lock after 3 attempts.\"
```

Now the agent has a contract to satisfy. It can still wander, but it has to wander within the spec.

## The Real Win: Reviewability

With a spec and a diff, you can review in 5 minutes. Without them, you're debugging a black box.

This is what Ralph Workflow is built around — not better AI, better workflow structure.

---

**Try it on Codeberg:** [RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — star, fork, and open issues there. The GitHub mirror is at [Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow) if you prefer it.
""",
    ),
    Topic(
        slug="technical",
        content_type="technical",
        angle="How nested analysis loops catch bugs before they commit",
        keyword="unattended ai coding",
        cta="install_ralphworkflow",
        hypothesis="Technical posts should outperform philosophy posts on write.as because they are more concrete and searchable.",
        body="""# How Nested Analysis Loops Catch Bugs Before They Commit

The commit is not where you should catch bugs. The analysis loop is.

Here's the pattern that changes everything: each phase has its own feedback loop, separate from the program loop.

## Two Loops, Not One

Most AI coding workflows are one big loop:
- Write code → Run it → Looks good → Commit

Ralph Workflow separates concerns:

```text
PHASE LOOP (inside each phase)
  build → analyze → revise → analyze → ... → commit

PROGRAM LOOP (between phases)
  plan → [phase loop] → develop → [phase loop] → commit → plan (fresh)
```

The phase loop catches implementation mistakes. The program loop catches direction errors.

## What Analysis Actually Does

Analysis isn't \"review the code.\" It's running the code against the spec, automatically.

```python
# The analysis agent checks:
# 1. Does the diff match the spec item?
# 2. Does it break existing tests?
# 3. Are there obvious bugs?
# 4. Is the code readable?
```

If any check fails, the loop goes back with specific feedback.

## Why This Matters for Unattended Runs

Without this, unattended runs are just unattended bug creation. With it, the loop acts as an automated senior developer review on every commit.

This is the difference between "it ran" and "it's correct."

---

**Try it on Codeberg:** [RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — star, fork, and open issues there. The GitHub mirror is at [Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow) if you prefer it.
""",
    ),
    Topic(
        slug="usecase",
        content_type="usecase",
        angle="How a solo dev shipped 23 commits in 4 hours with no supervision",
        keyword="ai coding workflow",
        cta="install_ralphworkflow",
        hypothesis="Concrete use-case posts should attract the strongest engagement because they show results, not just theory.",
        body="""# How I Shipped 23 Commits in 4 Hours With No Supervision

Last week I needed to build a job application tracker. I had 4 hours before dinner. Here's what I did.

## The Setup (10 minutes)

1. Opened a new branch
2. Wrote 12 spec items covering the core features
3. Kicked off Ralph Workflow with a token budget

Then I made dinner.

## What Happened

When I came back:
- 23 commits on a feature branch
- Every commit traced to a spec item
- 2 issues caught by the verify step and fixed automatically
- Zero debugging required

## The Spec That Made It Possible

```markdown
## Job Application Tracker

### Core Features
- Add job: company, role, link, status, salary range, notes
- List view: sortable by date, status, company
- Status workflow: Applied → Phone Screen → Onsite → Offer → Rejected
- Reminder: flag stale applications (>2 weeks since last update)
```

Every one of those became a commit with a reference. I could review any change in seconds.

## The Point

You don't need to watch AI code. You need to give it a spec and let it work.

The 4 hours I wasn't watching was the 4 hours it was actually productive.

---

**Try it on Codeberg:** [RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — star, fork, and open issues there. The GitHub mirror is at [Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow) if you prefer it.
""",
    ),
]


def build_metadata(topic: Topic, now: datetime) -> dict[str, str]:
    date_str = now.strftime("%Y-%m-%d")
    return {
        "date": date_str,
        "product": "RalphWorkflow",
        "channel": "writeas",
        "experiment_id": f"{date_str}-{topic.slug}",
        "content_type": topic.content_type,
        "angle": topic.angle,
        "keyword": topic.keyword,
        "cta": topic.cta,
        "hypothesis": topic.hypothesis,
    }


def render_front_matter(metadata: dict[str, str]) -> str:
    lines = ["---"]
    for key, value in metadata.items():
        safe = json.dumps(value, ensure_ascii=False)
        lines.append(f"{key}: {safe}")
    lines.append("---")
    return "\n".join(lines)


def build_draft_content(topic: Topic, now: datetime) -> str:
    metadata = build_metadata(topic, now)
    body = topic.body.strip()
    if "operating system for autonomous coding" not in body.lower():
        body += (
            "\n\n## Where Ralph Workflow Fits\n\n"
            "Ralph Workflow is the operating system for autonomous coding: a free and open-source "
            "composable loop framework and AI orchestrator. It keeps the core loop simple, ships "
            "with a strong default workflow for writing software, and lets you use that default "
            "as-is or build your own workflow on top."
        )
    issues = validate_marketing_copy(body)
    if issues:
        raise RuntimeError(f"Generated draft failed positioning validation for topic {topic.slug}: {issues}")
    return f"{render_front_matter(metadata)}\n\n{body}\n"


def generate_draft(now: Optional[datetime] = None) -> Optional[Path]:
    now = now or datetime.now()
    # SEO-informed topic selection: prioritize gaps identified by run.py
    topic = TOPIC_ROTATION[now.weekday() % len(TOPIC_ROTATION)]
    insights = load_seo_insights()
    gap_keywords = insights.get('gaps', [])
    if gap_keywords:
        # Find a topic whose angle/body mentions a gap keyword
        for t in TOPIC_ROTATION:
            for gap in gap_keywords:
                if gap.lower() in (t.angle + t.body).lower():
                    topic = t
                    print(f"[generate_content.py] SEO match: using topic '{topic.slug}' for gap keyword '{gap}'", flush=True)
                    break
            else:
                continue
            break
    if topic is None:
        return None

    filename = DRAFTS_DIR / f"{now.strftime('%Y-%m-%d')}_{topic.slug}_draft.md"
    filename.write_text(build_draft_content(topic, now), encoding="utf-8")
    return filename


if __name__ == "__main__":
    result = generate_draft()
    if result:
        print(f"[ContentGen] Created: {result}")
    else:
        print("[ContentGen] No draft scheduled for today")
