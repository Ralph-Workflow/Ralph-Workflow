# RalphWorkflow Reddit next-window packet - 2026-05-20 13:05 CEST

Live posting is **not** the move right now.

Latest watchdog state references report `reddit_monitor_2026-05-20_1220.md`.
- `status: fresh_opportunity_rate_limited`
- `detail: opportunity_state:fresh_rate_limited; opportunities:8`

So this packet is optimized for the **next safe window**, not for forcing a post now.

Use these only if the next watchdog check clears the rate window and the thread still looks fresh enough.

Messaging guardrails preserved in every draft:
- what it is: Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator.
- who it is for: Developers and technical teams doing ambitious software work that benefits from a structured workflow instead of a chat session.
- why different: It keeps a simple Ralph-loop core, then composes that core into planning, development, verification, and broader workflow loops with strong defaults.
- why now: You can use the default workflow as-is today, or build your own workflow on top without giving up control of your tools or process.

---

## 1) `r/ClaudeCode` - How do you ACTUALLY use CC+codex?
- URL: <https://www.reddit.com/r/ClaudeCode/comments/1tb7j36/how_do_you_actually_use_cccodex/>
- Mention fit: medium-low
- Best landing page to seed:
  - https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/claude-code-codex-workflow.md
- Why this stays in the packet:
  - thread naturally fits builder/reviewer phase boundaries and handoff discipline
  - landing page explains why mixed-agent flow only matters when the finish stays inspectable
  - best RalphWorkflow angle from the monitor: use both only if the finish gets clearer: what changed, what passed, what still needs a decision

### Draft body A
The constraint that cleaned up our multi-agent runs: the second pass never re-implements. It only reviews and flags what the first pass left ambiguous.

That separation alone removed most of the overlap confusion and made the morning result something I could actually grade.

Ralph Workflow is free and open-source: it enforces that role separation so the morning result is reviewable.

https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/claude-code-codex-workflow.md

---

## Posting discipline before using any of these
1. Re-read the last 3 logged Reddit bodies first.
2. Re-run `python3 agents/marketing/reddit_watchdog.py` before posting; do not trust this file alone on timing.
3. If the thread shifted, rewrite the opener instead of forcing the draft.
4. If the reply is useful without Ralph, keep Ralph secondary.
5. Use one seeded proof/comparison link only - no link pile.
6. Prefer only one post in the next safe window unless a second thread is clearly exceptional.
7. Reject any draft that falls back into the same cadence as the last three bodies.
