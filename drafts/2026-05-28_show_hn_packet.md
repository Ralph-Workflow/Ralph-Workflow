# Show HN: Ralph Workflow — Autonomous Coding That Ends in Something You'd Actually Merge
Generated: 2026-05-28T21:50 CEST
Status: NEW — breaks the 7-cycle HN/Lobsters stalemate with a different category

## Why Show HN instead of comparison essay
HN/Lobsters has been the "only remaining move" for 7+ audit cycles. The packets were framed
as comparison/analysis essays posted via Telegraph. Those require category authority and get
flagged as blogspam if the author isn't known in the community.

Show HN is a different category entirely:
- Show HN posts are about projects you've built
- They don't require category authority — you are answering questions about your own work
- The framing is "I built X, try it, tell me what you think" — not "here's why X is better"
- Show HN is the #1 path for open-source devtools to gain initial traction on HN

## Post title (3 variants)
A: "Show HN: Ralph Workflow — free open-source AI orchestrator for overnight coding"
B: "Show HN: Ralph Workflow — wake up to a finished, tested, reviewable PR"
C: "Show HN: Ralph Workflow — stop babysitting your agents, start reviewing results"

## Post body
```
I built Ralph Workflow because I was tired of waking up to agent runs that said
"done!" but left me with broken tests, half-finished refactors, and no clear
path to actually merging anything.

What it does:
Ralph Workflow takes your existing coding agents (Claude Code, Codex CLI, etc.)
and wraps them in a structured composable loop: plan → build → verify → repeat.
At the end, you get back a bounded diff, test results, and a review surface —
not a confident summary.

The defaults handle most workflows out of the box:
- Planning loop: goes deeper on ambiguous parts of your spec
- Development loop: iterates until tests pass
- Verification loop: runs the full test suite plus integrity checks
- Everything stays in your repo, on your machine

It's free and open source (MIT), runs locally with any LLM provider,
and the quick-start takes about 5 minutes:
pip install ralph-workflow
ralph init my-project
# write a short spec, then:
ralph

Repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow
Walkthrough: https://ralphworkflow.com/blog/real-task-walkthrough-overnight-refactoring

I'd love feedback — especially from anyone who's tried running coding agents
on multi-hour tasks and been disappointed by the morning-after review experience.
```

## Key differences from previous HN packets
1. **Show HN category** — not a standalone blog post
2. **Personal voice** ("I built" not "This comparison examines")
3. **One concrete outcome** — "wake up to a finished, tested, reviewable PR"
4. **Quick-start call** — pip install → ralph init → ralph (3 steps)
5. **Links directly to Codeberg** — not Telegraph as an intermediate hop
6. **Feedback ask** — Show HN thrives on "try it and tell me what you think"
7. **Shorter** — <200 words; HN rewards concise Show HN posts

## If Show HN doesn't work either
The HN distribution path is structurally gated by human execution. If Show HN is also
blocked, the system must pivot entirely to lanes that don't require human posting:
- Publisher outreach via email (SMTP-dependent, currently blocked)
- GitHub Discussions research/reply (low effort, low ceiling)
- Further conversion surface optimization (highest-yield per unit of autonomous effort)
- Guest post pitches to blogs that accept submissions

## Measurement contract
- Expected outcome: one live Show HN post within the next review window
- If not posted: mark HN/Lobsters lane as structurally blocked and stop allocating
  packet-generation effort to it; replace with fully autonomous lane
- If posted: measure Codeberg traffic spike (available via Codeberg API)
