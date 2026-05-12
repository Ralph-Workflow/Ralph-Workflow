#!/usr/bin/env python3
"""Generate RalphWorkflow content drafts with experiment metadata.

This is intentionally simple and deterministic so weekly evaluation can compare
content buckets over time.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

DRAFTS_DIR = Path("/home/mistlight/.openclaw/workspace/drafts")
DRAFTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Topic:
    slug: str
    content_type: str
    angle: str
    keyword: str
    cta: str
    hypothesis: str
    body: str


TOPICS: dict[int, Topic] = {
    0: Topic(
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
""",
    ),
    2: Topic(
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

This is the difference between \"it ran\" and \"it's correct.\"
""",
    ),
    4: Topic(
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
""",
    ),
}


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
    return f"{render_front_matter(metadata)}\n\n{topic.body.strip()}\n"


def generate_draft(now: Optional[datetime] = None) -> Optional[Path]:
    now = now or datetime.now()
    topic = TOPICS.get(now.weekday())
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
