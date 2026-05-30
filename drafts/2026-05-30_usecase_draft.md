---
date: "2026-05-30"
product: "RalphWorkflow"
channel: "writeas"
experiment_id: "2026-05-30-usecase"
content_type: "usecase"
angle: "How a solo dev shipped 23 commits in 4 hours with no supervision"
keyword: "ai coding workflow"
cta: "install_ralphworkflow"
hypothesis: "Concrete use-case posts should attract the strongest engagement because they show results, not just theory."
---

# How I Shipped 23 Commits in 4 Hours With No Supervision

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

## Where Ralph Workflow Fits

Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator. It keeps the core loop simple, ships with a strong default workflow for writing software, and lets you use that default as-is or build your own workflow on top.
